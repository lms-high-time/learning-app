# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Прохождение урока: создание и сверка с релизом (learning-services#504)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases import service as релизы
from lms_frappe_app.agent_learning.runs import service
from lms_frappe_app.tests.release_sample import пример_релиза, релиз_двух_целей
from lms_frappe_app.tests.sample_data import создать_занятие, создать_куратора, создать_ученика

ПРОХОЖДЕНИЕ = "Agent Lesson Run"
СВЕРКА = "lms_frappe_app.agent_learning.runs.service.сверить_курс"
ПУНКТЫ = ["term:T1", "exec:E1", "refute:M1", "return:R1"]


def пункт(релиз: dict, ключ: str) -> dict:
	"""Пункт урока `l-1` по ключу — чтобы тест правил релиз по месту."""
	for цель in релиз["lessons"][0]["objectives"]:
		for п in цель["goals"]:
			if п["key"] == ключ:
				return п
	raise KeyError(ключ)


def сверки(очередь) -> list:
	"""Постановки сверки прохождений среди вызовов `frappe.enqueue`."""
	return [в for в in очередь.call_args_list if в.args == (СВЕРКА,)]


def итог(ответ: dict) -> tuple[str, str]:
	"""Статус цели пункта и урока из ответа `отметить`."""
	return ответ["objective"]["status"], ответ["lesson"]["status"]


def убрать_пункт(релиз: dict, ключ: str) -> None:
	for цель in релиз["lessons"][0]["objectives"]:
		цель["goals"] = [п for п in цель["goals"] if п["key"] != ключ]


class IntegrationTestПрохождение(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"run-svc-{суффикс}@example.com")
		self.ученик = создать_ученика(f"run-svc-pupil-{суффикс}@example.com")
		self.ключ = f"runs-{суффикс}"
		frappe.set_user(self.куратор)

	def опубликовать(self, релиз: dict) -> dict:
		return релизы.опубликовать(релиз, None, self.куратор)

	def пункты(self, run) -> dict[str, dict]:
		return {
			п.goal_key: {
				"objective_key": п.objective_key,
				"status": п.status,
				"evidence": п.evidence,
				"removed": п.removed,
			}
			for п in run.goals
		}

	def цели(self, run) -> dict[str, tuple]:
		return {ц.objective_key: (ц.status, ц.removed) for ц in run.objectives}

	def отметить(
		self, run, ключ: str, статус: str = "done", свидетельство: str | None = "Ученик объяснил сам"
	):
		"""Отметка пункта; отдаёт прохождение, перечитанное после неё."""
		service.отметить(run.name, ключ, статус, свидетельство)
		return self.перечитать(run)

	def перечитать(self, run):
		return frappe.get_doc(ПРОХОЖДЕНИЕ, run.name)

	def test_новое_прохождение(self):
		ответ = self.опубликовать(релиз_двух_целей(self.ключ))

		run = service.прохождение(self.ученик, ответ["course"], "l-1")

		self.assertEqual(run.release, ответ["release"])
		self.assertEqual((run.student, run.course, run.lesson_key), (self.ученик, ответ["course"], "l-1"))
		self.assertEqual(
			run.lesson,
			frappe.db.get_value(
				"Agent Release Lesson", {"parent": ответ["release"], "lesson_key": "l-1"}, "lesson"
			),
		)
		self.assertEqual(run.status, "not_started")
		self.assertEqual([п.goal_key for п in run.goals], ПУНКТЫ)
		self.assertEqual({п.status for п in run.goals}, {"open"})
		self.assertEqual(
			[(п.objective_key, п.kind, п.required, п.title) for п in run.goals][:2],
			[
				("l-1-D1", "term", 1, "Термин «пример»"),
				("l-1-D1", "execution", 1, "Сделать пример"),
			],
		)
		self.assertEqual(self.цели(run), {"l-1-D1": ("not_started", 0), "l-1-D2": ("covered", 0)})
		self.assertEqual(frappe.db.count(ПРОХОЖДЕНИЕ, {"student": self.ученик}), 1)

	def test_повторное_обращение_отдаёт_то_же_прохождение(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		первое = service.прохождение(self.ученик, курс, "l-1")

		второе = service.прохождение(self.ученик, курс, "l-1")

		self.assertEqual(второе.name, первое.name)
		self.assertFalse(service.сверить(второе))

	def test_статусы_урока_и_целей(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		run = service.прохождение(self.ученик, курс, "l-1")

		run = self.отметить(run, "refute:M1", "not_needed")
		self.assertEqual((run.status, self.цели(run)["l-1-D1"][0]), ("in_progress", "not_started"))
		run = self.отметить(run, "term:T1")
		self.assertEqual((run.status, self.цели(run)["l-1-D1"][0]), ("in_progress", "touched"))
		run = self.отметить(run, "exec:E1", "planned")
		self.assertEqual((run.status, self.цели(run)["l-1-D1"][0]), ("covered", "covered"))
		# Пункт вернули в `open`: цель снова в работе, урок остаётся начатым.
		run = self.отметить(run, "term:T1", "open", None)
		self.assertEqual((run.status, self.цели(run)["l-1-D1"][0]), ("in_progress", "touched"))

	def test_пройденный_урок_не_снимается_пересчётом(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		run = service.прохождение(self.ученик, курс, "l-1")
		frappe.db.set_value(ПРОХОЖДЕНИЕ, run.name, "status", "passed")

		run = self.отметить(run, "term:T1", "open", None)

		self.assertEqual(run.status, "passed")

	def test_пункт_добавлен_и_убран_затем_возвращён(self):
		релиз = релиз_двух_целей(self.ключ)
		курс = self.опубликовать(релиз)["course"]
		run = self.отметить(
			service.прохождение(self.ученик, курс, "l-1"), "exec:E1", "planned", "Сделает дома"
		)

		второй = релиз_двух_целей(self.ключ)
		убрать_пункт(второй, "exec:E1")
		второй["lessons"][0]["objectives"][0]["goals"].append(
			{"key": "trap:P1", "kind": "trap", "required": True, "title": "Ловушка"}
		)
		ответ = self.опубликовать(второй)
		run = service.прохождение(self.ученик, курс, "l-1")

		self.assertEqual(run.release, ответ["release"])
		пункты = self.пункты(run)
		self.assertEqual(
			пункты["trap:P1"], {"objective_key": "l-1-D1", "status": "open", "evidence": None, "removed": 0}
		)
		self.assertEqual(
			пункты["exec:E1"],
			{"objective_key": "l-1-D1", "status": "planned", "evidence": "Сделает дома", "removed": 1},
		)
		# Снятый пункт на статус цели не влияет, но урок, начатый его отметкой, остаётся начатым.
		self.assertEqual(self.цели(run)["l-1-D1"], ("not_started", 0))
		self.assertEqual(run.status, "in_progress")
		self.assertEqual(
			[п.goal_key for п in run.goals if not п.removed], ["term:T1", "refute:M1", "trap:P1", "return:R1"]
		)

		self.опубликовать(релиз)
		run = service.прохождение(self.ученик, курс, "l-1")

		пункты = self.пункты(run)
		self.assertEqual(
			пункты["exec:E1"],
			{"objective_key": "l-1-D1", "status": "planned", "evidence": "Сделает дома", "removed": 0},
		)
		self.assertEqual(пункты["trap:P1"]["removed"], 1)
		self.assertEqual([п.goal_key for п in run.goals if not п.removed], ПУНКТЫ)
		self.assertEqual((self.цели(run)["l-1-D1"], run.status), (("touched", 0), "in_progress"))

	def test_пункт_перешёл_к_другой_цели(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		run = self.отметить(service.прохождение(self.ученик, курс, "l-1"), "term:T1")
		self.assertEqual(self.цели(run), {"l-1-D1": ("touched", 0), "l-1-D2": ("covered", 0)})

		второй = релиз_двух_целей(self.ключ)
		перенесённый = пункт(второй, "exec:E1")
		убрать_пункт(второй, "exec:E1")
		второй["lessons"][0]["objectives"][1]["goals"].insert(0, перенесённый)
		self.опубликовать(второй)
		run = service.прохождение(self.ученик, курс, "l-1")

		self.assertEqual(self.пункты(run)["exec:E1"]["objective_key"], "l-1-D2")
		self.assertEqual(self.цели(run), {"l-1-D1": ("covered", 0), "l-1-D2": ("not_started", 0)})
		self.assertEqual([п.goal_key for п in run.goals], ["term:T1", "refute:M1", "exec:E1", "return:R1"])

	def test_цель_снята_и_возвращена(self):
		релиз = релиз_двух_целей(self.ключ)
		курс = self.опубликовать(релиз)["course"]
		run = service.прохождение(self.ученик, курс, "l-1")

		второй = релиз_двух_целей(self.ключ)
		второй["lessons"][0]["objectives"].pop()
		self.опубликовать(второй)
		run = service.прохождение(self.ученик, курс, "l-1")
		self.assertEqual(self.цели(run)["l-1-D2"], ("covered", 1))
		self.assertEqual(self.пункты(run)["return:R1"]["removed"], 1)

		self.опубликовать(релиз)
		run = service.прохождение(self.ученик, курс, "l-1")
		self.assertEqual(self.цели(run)["l-1-D2"], ("covered", 0))
		self.assertEqual(self.пункты(run)["return:R1"]["removed"], 0)

	def test_публикация_сверяет_прохождения_курса(self):
		"""Новый обязательный пункт: разобранная цель снова в работе, урок не разобран."""
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		run = service.прохождение(self.ученик, курс, "l-1")
		self.отметить(run, "term:T1")
		run = self.отметить(run, "exec:E1")
		self.assertEqual((run.status, self.цели(run)["l-1-D1"][0]), ("covered", "covered"))

		второй = релиз_двух_целей(self.ключ)
		второй["lessons"][0]["objectives"][0]["goals"].append(
			{"key": "trap:P1", "kind": "trap", "required": True, "title": "Ловушка"}
		)
		ответ = self.опубликовать(второй)

		# Без обращения к прохождению: сверку сделала фоновая задача публикации.
		run = self.перечитать(run)
		self.assertEqual(run.release, ответ["release"])
		self.assertEqual(self.пункты(run)["trap:P1"]["status"], "open")
		self.assertEqual((run.status, self.цели(run)["l-1-D1"][0]), ("in_progress", "touched"))

	def test_публикация_ставит_сверку_в_очередь_после_коммита(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		второй = релиз_двух_целей(self.ключ)
		пункт(второй, "term:T1")["title"] = "Термин «другой пример»"

		with patch.object(frappe, "enqueue") as очередь:
			self.опубликовать(второй)

		[вызов] = сверки(очередь)
		self.assertEqual(
			{к: вызов.kwargs[к] for к in ("enqueue_after_commit", "курс")},
			{"enqueue_after_commit": True, "курс": курс},
		)

	def test_повтор_публикации_и_новый_курс_сверку_не_ставят(self):
		with patch.object(frappe, "enqueue") as очередь:
			self.опубликовать(релиз_двух_целей(self.ключ))
			self.опубликовать(релиз_двух_целей(self.ключ))
		self.assertEqual(сверки(очередь), [])

	def test_сбой_очереди_не_срывает_публикацию(self):
		self.опубликовать(релиз_двух_целей(self.ключ))
		второй = релиз_двух_целей(self.ключ)
		пункт(второй, "term:T1")["title"] = "Термин «другой пример»"
		# Вне тестов: в тестах сбой сверки — ошибка теста (см. следующий тест).
		with (
			patch.object(frappe, "in_test", False),
			patch.object(frappe, "enqueue", side_effect=RuntimeError("очередь переполнена")),
			patch.object(frappe, "log_error") as журнал,
		):
			ответ = self.опубликовать(второй)
		журнал.assert_called_once()
		self.assertEqual(ответ["version"], 2)

	def test_в_тестах_ошибка_сверки_не_прячется(self):
		self.опубликовать(релиз_двух_целей(self.ключ))
		второй = релиз_двух_целей(self.ключ)
		пункт(второй, "term:T1")["title"] = "Термин «другой пример»"
		with (
			patch.object(service, "сверить_курс", side_effect=RuntimeError("ошибка сверки")),
			self.assertRaises(RuntimeError),
		):
			self.опубликовать(второй)

	def новый_релиз(self, курс: str) -> str:
		"""Второй релиз курса без фоновой сверки: её зовёт тест."""
		второй = релиз_двух_целей(self.ключ)
		пункт(второй, "term:T1")["title"] = "Термин «другой пример»"
		with patch.object(frappe, "enqueue"):
			return self.опубликовать(второй)["release"]

	def ученики(self, курс: str, сколько: int) -> list:
		return [
			service.прохождение(
				создать_ученика(f"run-svc-{номер}-{frappe.generate_hash(length=6)}@example.com"), курс, "l-1"
			)
			for номер in range(сколько)
		]

	def test_сверка_курса_пропускает_архивные(self):
		ответ = self.опубликовать(релиз_двух_целей(self.ключ))
		курс = ответ["course"]
		живые = self.ученики(курс, 2)
		[архивное] = self.ученики(курс, 1)
		frappe.db.set_value(
			ПРОХОЖДЕНИЕ,
			архивное.name,
			{"student": None, "archived_student": архивное.student, "archived_at": now_datetime()},
		)
		новый = self.новый_релиз(курс)

		with patch.object(service.index, "урок", wraps=service.index.урок) as урок:
			self.assertEqual(service.сверить_курс(курс), 2)

		# Урок релиза прочитан один раз на ключ, а не на каждое прохождение.
		self.assertEqual(урок.call_count, 1)
		for run in живые:
			self.assertEqual(self.перечитать(run).release, новый)
		self.assertEqual(self.перечитать(архивное).release, ответ["release"])
		self.assertEqual(service.сверить_курс(курс), 0)

	def test_сбой_одного_прохождения_не_срывает_сверку_курса(self):
		ответ = self.опубликовать(релиз_двух_целей(self.ключ))
		курс = ответ["course"]
		плохое, хорошее = self.ученики(курс, 2)
		новый = self.новый_релиз(курс)
		сверить = service._сверить

		def сбой(run, *args):
			if run.name == плохое.name:
				raise RuntimeError("сбой сверки")
			return сверить(run, *args)

		with (
			patch.object(service, "_сверить", side_effect=сбой),
			patch.object(frappe, "log_error") as журнал,
		):
			self.assertEqual(service.сверить_курс(курс), 1)

		журнал.assert_called_once()
		self.assertEqual(self.перечитать(хорошее).release, новый)
		self.assertEqual(self.перечитать(плохое).release, ответ["release"])

	def test_взаимоблокировка_откатывает_и_сверка_идёт_дальше(self):
		"""Взаимоблокировка откатывает транзакцию целиком: откат — полный, не к точке."""
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		гонка, хорошее = self.ученики(курс, 2)
		новый = self.новый_релиз(курс)
		сверить = service._сверить

		def сбой(run, *args):
			if run.name == гонка.name:
				raise frappe.QueryDeadlockError("1213")
			return сверить(run, *args)

		# Полный откат в тесте снял бы и записи теста — он подменён.
		with (
			patch.object(service, "_сверить", side_effect=сбой),
			patch.object(frappe.db, "rollback") as откат,
			patch.object(frappe, "log_error") as журнал,
		):
			self.assertEqual(service.сверить_курс(курс), 1)

		откат.assert_called_once_with()
		журнал.assert_called_once()
		self.assertEqual(self.перечитать(хорошее).release, новый)

	def test_сверка_курса_останавливается_на_новом_релизе(self):
		"""Релиз сменился посреди задачи: остальные прохождения сверит задача новой публикации."""
		первый = self.опубликовать(релиз_двух_целей(self.ключ))
		курс = первый["course"]
		прохождения = self.ученики(курс, 2)
		новый = self.новый_релиз(курс)
		сверить = service._сверить
		сверены = []

		def и_новый_релиз(run, *args):
			сверены.append(run.name)
			итог = сверить(run, *args)
			frappe.db.set_value("LMS Course", курс, "active_release", первый["release"])
			return итог

		with patch.object(service, "_сверить", side_effect=и_новый_релиз):
			self.assertEqual(service.сверить_курс(курс), 1)

		[сверено] = сверены
		[другое] = [run for run in прохождения if run.name != сверено]
		self.assertEqual(self.перечитать(другое).release, первый["release"])
		self.assertEqual(frappe.db.get_value(ПРОХОЖДЕНИЕ, сверено, "release"), новый)

	def test_удалённое_после_отбора_пропускается_молча(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		удалённое, хорошее = self.ученики(курс, 2)
		новый = self.новый_релиз(курс)
		прочитать = frappe.get_doc

		def прочитать_или_нет(*args, **kwargs):
			if args[1:2] == (удалённое.name,):
				raise frappe.DoesNotExistError
			return прочитать(*args, **kwargs)

		with (
			patch.object(frappe, "get_doc", side_effect=прочитать_или_нет),
			patch.object(frappe, "log_error") as журнал,
		):
			self.assertEqual(service.сверить_курс(курс), 1)

		журнал.assert_not_called()
		self.assertEqual(self.перечитать(хорошее).release, новый)

	def test_сверка_курса_коммитит_каждое_прохождение(self):
		"""Вне тестов коммит — и после сбоя, и после удалённого: следующее читает релиз заново."""
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		удалённое, плохое, хорошее = self.ученики(курс, 3)
		self.новый_релиз(курс)
		прочитать, сверить = frappe.get_doc, service._сверить

		def прочитать_или_нет(*args, **kwargs):
			if args[1:2] == (удалённое.name,):
				raise frappe.DoesNotExistError
			return прочитать(*args, **kwargs)

		def сбой(run, *args):
			if run.name == плохое.name:
				raise RuntimeError("сбой сверки")
			return сверить(run, *args)

		# `in_test` снят нарочно: коммит по прохождению идёт только вне тестов.
		# Сам коммит подменён: настоящий зафиксировал бы записи теста.
		with (
			patch.object(frappe, "in_test", False),
			patch.object(frappe, "get_doc", side_effect=прочитать_или_нет),
			patch.object(service, "_сверить", side_effect=сбой),
			patch.object(frappe.db, "commit") as коммит,
			patch.object(frappe, "log_error"),
		):
			self.assertEqual(service.сверить_курс(курс), 1)

		self.assertEqual(коммит.call_count, 3)

	def test_курс_с_прохождениями_не_удаляется(self):
		ответ = self.опубликовать(релиз_двух_целей(self.ключ))
		курс = ответ["course"]
		service.прохождение(self.ученик, курс, "l-1")
		frappe.set_user("Administrator")

		with self.assertRaises(Отказ) as пойман:
			релизы.удалить_курс(курс)

		self.assertEqual(пойман.exception.код, "course_has_lesson_runs")
		self.assertEqual(frappe.db.get_value("LMS Course", курс, "active_release"), ответ["release"])
		self.assertTrue(frappe.db.exists("Agent Course Release", ответ["release"]))

	def test_урок_исчез_из_релиза(self):
		релиз = пример_релиза(self.ключ)
		курс = self.опубликовать(релиз)["course"]
		run = service.прохождение(self.ученик, курс, "l-2")
		run = self.отметить(run, "term:T1")
		прежнее = (run.release, run.status, self.пункты(run), self.цели(run))

		без_урока = пример_релиза(self.ключ)
		без_урока["chapters"][0]["lessons"] = ["l-1"]
		без_урока["lessons"] = [у for у in без_урока["lessons"] if у["key"] != "l-2"]
		self.опубликовать(без_урока)

		run = service.прохождение(self.ученик, курс, "l-2")
		self.assertEqual((run.release, run.status, self.пункты(run), self.цели(run)), прежнее)
		self.assertFalse(service.сверить(run))

		другой = создать_ученика(f"run-svc-other-{frappe.generate_hash(length=6)}@example.com")
		with self.assertRaises(Отказ) as пойман:
			service.прохождение(другой, курс, "l-2")
		self.assertEqual(пойман.exception.код, "lesson_not_in_release")
		self.assertFalse(frappe.db.exists(ПРОХОЖДЕНИЕ, {"student": другой}))

	def test_урока_нет_в_релизе(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		with self.assertRaises(Отказ) as пойман:
			service.прохождение(self.ученик, курс, "l-404")
		self.assertEqual(пойман.exception.код, "lesson_not_in_release")

	def test_гонка_создания_отдаёт_уже_созданное(self):
		"""Параллельный вызов вставил прохождение между поиском и вставкой.

		Случай, когда чужая запись видна снимку этой транзакции. При настоящей
		гонке перечитывание падает взаимоблокировкой — её отдаёт `busy` метод
		контракта (`_вставить`)."""
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		первое = service.прохождение(self.ученик, курс, "l-1")
		найти = service._найти

		with patch.object(service, "_найти", side_effect=[None, найти(self.ученик, курс, "l-1")]):
			второе = service.прохождение(self.ученик, курс, "l-1")

		self.assertEqual(второе.name, первое.name)
		self.assertEqual(frappe.db.count(ПРОХОЖДЕНИЕ, {"student": self.ученик, "course": курс}), 1)

	def отказ(self, код: str, *args, **kwargs) -> dict:
		"""Отказ `отметить` с этим кодом; отдаёт его подробности."""
		with self.assertRaises(Отказ) as пойман:
			service.отметить(*args, **kwargs)
		self.assertEqual(пойман.exception.код, код)
		return пойман.exception.подробности

	def test_отказы_формы_отметки(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		run = service.прохождение(self.ученик, курс, "l-1")
		свидетельство = "Ученик объяснил сам"

		for код, ключ, статус, текст in (
			("goal_unknown", "term:T404", "done", свидетельство),
			("not_needed_required", "term:T1", "not_needed", свидетельство),
			("goal_status_unknown", "term:T1", "skipped", свидетельство),
			("evidence_required", "term:T1", "done", None),
			("evidence_required", "exec:E1", "planned", "   "),
			("evidence_required", "refute:M1", "not_needed", ""),
			("evidence_too_long", "term:T1", "done", "я" * (service.ПРЕДЕЛ_СВИДЕТЕЛЬСТВА + 1)),
		):
			with self.subTest(код=код, статус=статус):
				self.отказ(код, run.name, ключ, статус, текст)

		# Отказ ничего не пишет: урок не начат, пункты открыты.
		run = self.перечитать(run)
		self.assertEqual((run.status, run.started_at), ("not_started", None))
		self.assertEqual({п.status for п in run.goals}, {"open"})
		# Подробности ведут агента к верной отметке.
		self.assertEqual(
			self.отказ("goal_unknown", run.name, "term:T404", "done", свидетельство)["goals"], ПУНКТЫ
		)
		self.assertEqual(
			self.отказ("not_needed_required", run.name, "term:T1", "not_needed", свидетельство)["allowed"],
			["open", "done", "planned"],
		)
		# Ровно предел — ещё можно.
		предел = "я" * service.ПРЕДЕЛ_СВИДЕТЕЛЬСТВА
		self.assertEqual(service.отметить(run.name, "term:T1", "done", предел)["status"], "done")

	def test_отказ_после_сверки_сверку_не_откатывает(self):
		"""Сверка сохранена до проверки пункта: отказ `goal_unknown` её оставляет, отметок не пишет."""
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		run = service.прохождение(self.ученик, курс, "l-1")
		было = self.пункты(run)
		новый = self.новый_релиз(курс)

		self.отказ("goal_unknown", run.name, "term:T404", "done", "Ученик объяснил сам")

		run = self.перечитать(run)
		self.assertEqual((run.release, self.пункты(run)), (новый, было))

	def test_снятый_пункт_не_отмечается(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		run = service.прохождение(self.ученик, курс, "l-1")
		второй = релиз_двух_целей(self.ключ)
		убрать_пункт(второй, "exec:E1")
		self.опубликовать(второй)

		self.отказ("goal_removed", run.name, "exec:E1", "done", "Ученик объяснил сам")

	def test_отметка_на_снятом_уроке(self):
		курс = self.опубликовать(пример_релиза(self.ключ))["course"]
		run = service.прохождение(self.ученик, курс, "l-2")
		без_урока = пример_релиза(self.ключ)
		без_урока["chapters"][0]["lessons"] = ["l-1"]
		без_урока["lessons"] = [у for у in без_урока["lessons"] if у["key"] != "l-2"]
		self.опубликовать(без_урока)

		подробности = self.отказ("lesson_not_in_release", run.name, "term:T1", "done", "Ученик объяснил сам")
		self.assertEqual(
			подробности,
			{
				"course": курс,
				"lesson_key": "l-2",
				"release": frappe.db.get_value("LMS Course", курс, "active_release"),
			},
		)
		self.assertIsNone(self.перечитать(run).started_at)

	def test_чужое_занятие(self):
		курс = self.опубликовать(пример_релиза(self.ключ))["course"]
		run = service.прохождение(self.ученик, курс, "l-1")
		другой = создать_ученика(f"run-svc-other-{frappe.generate_hash(length=6)}@example.com")
		другое = service.прохождение(self.ученик, курс, "l-2")
		другого_прохождения = создать_занятие(self.ученик, run.lesson)
		frappe.db.set_value("Agent Learning Session", другого_прохождения, "run", другое.name)

		for занятие in (
			создать_занятие(другой, run.lesson),
			создать_занятие(self.ученик, другое.lesson),
			другого_прохождения,
			"нет-такого-занятия",
		):
			with self.subTest(занятие=занятие):
				self.отказ("not_your_session", run.name, "term:T1", "done", "Сам", занятие=занятие)
		self.assertIsNone(self.перечитать(run).started_at)

	def test_архивное_прохождение_не_отмечается(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		run = service.прохождение(self.ученик, курс, "l-1")
		архив = {"student": None, "archived_student": self.ученик, "archived_at": now_datetime()}
		frappe.db.set_value(ПРОХОЖДЕНИЕ, run.name, архив)

		self.отказ("run_archived", run.name, "term:T1", "done", "Ученик объяснил сам")
		self.assertIsNone(self.перечитать(run).started_at)

	def test_отметка_пишет_пункт_и_начинает_урок(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		run = service.прохождение(self.ученик, курс, "l-1")
		занятие = создать_занятие(self.ученик, run.lesson)

		ответ = service.отметить(run.name, "term:T1", "done", "  Объяснил своими словами  ", занятие=занятие)

		self.assertEqual(
			ответ,
			{
				"goal": "term:T1",
				"status": "done",
				"objective": {"key": "l-1-D1", "status": "touched", "open": ["exec:E1"]},
				"lesson": {"status": "in_progress"},
				"next_step": {"kind": "goal", "objective": "l-1-D1", "goal": "exec:E1", "title": "Сделать пример"},
			},
		)
		run = self.перечитать(run)
		[строка] = [п for п in run.goals if п.goal_key == "term:T1"]
		self.assertEqual((строка.evidence, строка.session), ("Объяснил своими словами", занятие))
		self.assertIsNotNone(строка.marked_at)
		self.assertIsNotNone(run.started_at)
		начат = run.started_at

		# Возврат в `open` стирает свидетельство, даже присланное; урок остаётся начатым.
		ответ = service.отметить(run.name, "term:T1", "open", "Передумали")
		self.assertEqual(
			(ответ["objective"], ответ["lesson"]),
			(
				{"key": "l-1-D1", "status": "not_started", "open": ["term:T1", "exec:E1"]},
				{"status": "in_progress"},
			),
		)
		run = self.перечитать(run)
		[строка] = [п for п in run.goals if п.goal_key == "term:T1"]
		self.assertEqual((строка.status, строка.evidence, строка.session), ("open", None, None))
		self.assertEqual(run.started_at, начат)

	def test_статусы_по_отметкам(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		имя = service.прохождение(self.ученик, курс, "l-1").name

		# Необязательный пункт цель не двигает, но урок начинает.
		ответ = service.отметить(имя, "refute:M1", "not_needed", "Не проявилось")
		self.assertEqual(итог(ответ), ("not_started", "in_progress"))
		ответ = service.отметить(имя, "term:T1", "done", "Объяснил")
		self.assertEqual(итог(ответ), ("touched", "in_progress"))
		# `planned` закрывает пункт для цели, как `done`; обе цели разобраны — урок разобран.
		ответ = service.отметить(имя, "exec:E1", "planned", "Сделает дома")
		self.assertEqual(
			(ответ["objective"], ответ["lesson"]["status"], ответ["next_step"]),
			({"key": "l-1-D1", "status": "covered", "open": []}, "covered", {"kind": "complete"}),
		)
		ответ = service.отметить(имя, "exec:E1", "open", None)
		self.assertEqual(итог(ответ), ("touched", "in_progress"))

	def test_следующий_по_порядку_релиза(self):
		релиз = релиз_двух_целей(self.ключ)
		пункт(релиз, "return:R1")["required"] = True
		курс = self.опубликовать(релиз)["course"]
		run = service.прохождение(self.ученик, курс, "l-1")
		self.assertEqual(
			service.открытые_обязательные(run),
			[
				{"objective": "l-1-D1", "goal": "term:T1", "title": "Термин «пример»"},
				{"objective": "l-1-D1", "goal": "exec:E1", "title": "Сделать пример"},
				{"objective": "l-1-D2", "goal": "return:R1", "title": пункт(релиз, "return:R1")["title"]},
			],
		)

		self.assertEqual(service.отметить(run.name, "exec:E1", "done", "Сделал")["next_step"]["goal"], "term:T1")
		ответ = service.отметить(run.name, "term:T1", "done", "Объяснил")
		self.assertEqual(
			(ответ["objective"]["open"], ответ["next_step"]["goal"], ответ["next_step"]["objective"]),
			([], "return:R1", "l-1-D2"),
		)
		service.отметить(run.name, "return:R1", "done", "Вернулся")
		run = self.перечитать(run)
		self.assertEqual((service.открытые_обязательные(run), service.следующий(run)), ([], None))

	def test_пункт_стал_обязательным_после_not_needed(self):
		"""Новый релиз сделал обязательным пункт в `not_needed`: он снова открыт — как и для статуса цели."""
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		run = service.прохождение(self.ученик, курс, "l-1")
		self.отметить(run, "refute:M1", "not_needed", "Не нужно")
		второй = релиз_двух_целей(self.ключ)
		пункт(второй, "refute:M1")["required"] = True
		self.опубликовать(второй)

		run = service.прохождение(self.ученик, курс, "l-1")

		self.assertIn("refute:M1", [п["goal"] for п in service.открытые_обязательные(run)])
		self.assertEqual(self.цели(run)["l-1-D1"][0], "not_started")

	def test_отметка_сверяет_с_новым_релизом(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		run = service.прохождение(self.ученик, курс, "l-1")
		второй = релиз_двух_целей(self.ключ)
		второй["lessons"][0]["objectives"][0]["goals"].append(
			{"key": "trap:P1", "kind": "trap", "required": True, "title": "Ловушка"}
		)
		with patch.object(frappe, "enqueue"):
			новый = self.опубликовать(второй)["release"]

		ответ = service.отметить(run.name, "trap:P1", "done", "Не попался")

		self.assertEqual(ответ["objective"]["open"], ["term:T1", "exec:E1"])
		self.assertEqual(self.перечитать(run).release, новый)

	def test_отметка_перечитывает_прохождение(self):
		"""Две отметки подряд при устаревшей копии у вызывающего — обе записаны.

		Без `TimestampMismatchError`: `отметить` берёт имя и читает прохождение сама."""
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		первая = service.прохождение(self.ученик, курс, "l-1")
		вторая = frappe.get_doc(ПРОХОЖДЕНИЕ, первая.name)

		service.отметить(первая.name, "term:T1", "done", "Объяснил")
		service.отметить(вторая.name, "exec:E1", "done", "Сделал")

		run = self.перечитать(первая)
		self.assertEqual(
			(self.пункты(run)["term:T1"]["status"], self.пункты(run)["exec:E1"]["status"], run.status),
			("done", "done", "covered"),
		)

	def test_отметка_не_пишет_журнал_занятия(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))["course"]
		run = service.прохождение(self.ученик, курс, "l-1")
		занятие = создать_занятие(self.ученик, run.lesson)

		def журнал():
			return frappe.get_all(
				"Agent Session Event", filters={"session": занятие}, fields=["name", "modified"]
			)

		было = журнал()

		service.отметить(run.name, "term:T1", "done", "Объяснил", занятие=занятие)

		self.assertEqual(журнал(), было)

	def главы(self, курс: str) -> dict[str, tuple]:
		"""Главы ученика: ключ → (статус, всего, начато, пройдено)."""
		return {
			г["key"]: (г["status"], г["lessons_total"], г["lessons_started"], г["lessons_passed"])
			for г in service.главы(self.ученик, курс)
		}

	def test_главы_без_прохождений(self):
		курс = self.опубликовать(пример_релиза(self.ключ))["course"]

		self.assertEqual([г["key"] for г in service.главы(self.ученик, курс)], ["ch-1", "ch-2"])
		self.assertEqual(
			self.главы(курс), {"ch-1": ("not_started", 2, 0, 0), "ch-2": ("not_started", 1, 0, 0)}
		)
		# Прохождение, принятое без отметок, урок не начинает; `главы` прохождений не заводит.
		service.прохождение(self.ученик, курс, "l-1")
		self.assertEqual(self.главы(курс)["ch-1"], ("not_started", 2, 0, 0))
		self.assertEqual(frappe.db.count(ПРОХОЖДЕНИЕ, {"student": self.ученик}), 1)

	def test_главы_начатая_и_пройденная(self):
		курс = self.опубликовать(пример_релиза(self.ключ))["course"]
		начатое = self.отметить(service.прохождение(self.ученик, курс, "l-1"), "term:T1")
		пройденное = service.прохождение(self.ученик, курс, "l-3")
		frappe.db.set_value(ПРОХОЖДЕНИЕ, пройденное.name, "status", "passed")

		self.assertEqual(self.главы(курс), {"ch-1": ("in_progress", 2, 1, 0), "ch-2": ("passed", 1, 1, 1)})
		# Пройден один урок из двух — глава ещё в работе.
		frappe.db.set_value(ПРОХОЖДЕНИЕ, начатое.name, "status", "passed")
		self.assertEqual(self.главы(курс)["ch-1"], ("in_progress", 2, 1, 1))
		# Чужие прохождения в счёт не идут.
		другой = создать_ученика(f"run-svc-other-{frappe.generate_hash(length=6)}@example.com")
		self.assertEqual([г["status"] for г in service.главы(другой, курс)], ["not_started", "not_started"])

	def test_главы_снятый_урок_не_в_счёт(self):
		курс = self.опубликовать(пример_релиза(self.ключ))["course"]
		снятое = service.прохождение(self.ученик, курс, "l-2")
		self.отметить(снятое, "term:T1")
		frappe.db.set_value(ПРОХОЖДЕНИЕ, снятое.name, "status", "passed")
		без_урока = пример_релиза(self.ключ)
		без_урока["chapters"][0]["lessons"] = ["l-1"]
		без_урока["lessons"] = [у for у in без_урока["lessons"] if у["key"] != "l-2"]
		self.опубликовать(без_урока)

		self.assertEqual(self.главы(курс)["ch-1"], ("not_started", 1, 0, 0))

	def test_главы_считают_несверенное_прохождение(self):
		"""Прохождение на прошлом релизе посчитано верно и не тронуто: `главы` не пишет."""
		первый = self.опубликовать(пример_релиза(self.ключ))
		курс = первый["course"]
		начатое = self.отметить(service.прохождение(self.ученик, курс, "l-1"), "term:T1")
		пройденное = service.прохождение(self.ученик, курс, "l-3")
		frappe.db.set_value(ПРОХОЖДЕНИЕ, пройденное.name, "status", "passed")
		второй = пример_релиза(self.ключ)
		второй["lessons"][0]["title"] = "Урок первый, исправленный"
		with patch.object(frappe, "enqueue"):
			self.опубликовать(второй)

		self.assertEqual(self.главы(курс), {"ch-1": ("in_progress", 2, 1, 0), "ch-2": ("passed", 1, 1, 1)})
		self.assertEqual(
			{self.перечитать(начатое).release, self.перечитать(пройденное).release}, {первый["release"]}
		)

	def test_главы_без_релиза(self):
		курс = self.опубликовать(пример_релиза(self.ключ))["course"]
		frappe.db.set_value("LMS Course", курс, "active_release", None)

		self.assertEqual(service.главы(self.ученик, курс), [])
