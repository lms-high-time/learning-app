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
from lms_frappe_app.tests.sample_data import создать_куратора, создать_ученика

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

	def отметить(self, run, ключ: str, статус: str = "done", свидетельство: str = "Ученик объяснил сам"):
		"""Отметка пункта напрямую — как её запишет `отметить` (шаг 3): первая начинает урок."""
		run = frappe.get_doc(ПРОХОЖДЕНИЕ, run.name, for_update=True)
		[строка] = [п for п in run.goals if п.goal_key == ключ]
		строка.status, строка.evidence = статус, свидетельство
		if статус != "open" and not run.started_at:
			run.started_at = now_datetime()
		service.статусы(run)
		run.save(ignore_permissions=True)
		return run

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
