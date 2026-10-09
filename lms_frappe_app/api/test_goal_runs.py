# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Прохождения уроков курса для автора — `goal_runs` (learning-services#512)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.api import authoring
from lms_frappe_app.tests.release_sample import пример_релиза, релиз_двух_целей
from lms_frappe_app.tests.sample_data import создать_занятие, создать_куратора, создать_курс, создать_ученика

ПРОХОЖДЕНИЕ = "Agent Lesson Run"


class IntegrationTestGoalRuns(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.суффикс = frappe.generate_hash(length=6)
		self.автор = создать_куратора(f"goal-runs-{self.суффикс}@example.com")
		self.ученик = создать_ученика(f"goal-runs-p1-{self.суффикс}@example.com")
		self.второй = создать_ученика(f"goal-runs-p2-{self.суффикс}@example.com")
		self.курс = self.опубликовать(пример_релиза(f"goal-runs-{self.суффикс}"))

	def опубликовать(self, релиз: dict) -> str:
		frappe.set_user(self.автор)
		ответ = authoring.publish_release(release=релиз)
		frappe.set_user("Administrator")
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]["course"]

	def пройти(self, ученик: str, ключ: str, *отметки: tuple, курс: str | None = None, **поля):
		"""Прохождение урока с отметками `(пункт, статус, свидетельство)` — в одном занятии."""
		run = прохождения.прохождение(ученик, курс or self.курс, ключ)
		занятие = создать_занятие(ученик, run.lesson, run=run.name)
		for пункт, статус, свидетельство in отметки:
			прохождения.отметить(run.name, пункт, статус, свидетельство, занятие, **поля)
		return run.name, занятие

	def прохождения(self, кто: str | None = None, **аргументы) -> dict:
		frappe.set_user(кто or self.автор)
		ответ = authoring.goal_runs(course=аргументы.pop("course", self.курс), **аргументы)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def код(self, **аргументы) -> tuple[str | None, dict]:
		frappe.set_user(self.автор)
		ответ = authoring.goal_runs(**аргументы)
		self.assertFalse(ответ["ok"], ответ)
		return ответ["error"]["code"], ответ["error"]

	# --- что видит автор ---

	def test_инструктор_видит_прохождение_со_свидетельством(self):
		_, занятие = self.пройти(
			self.ученик, "l-1", ("term:T1", "done", "Назвал термин сам"), продолжить="С выбора варианта"
		)

		данные = self.прохождения()

		self.assertEqual(
			{к: данные[к] for к in ("course", "lesson", "start", "limit", "has_more")},
			{"course": self.курс, "lesson": None, "start": 0, "limit": 20, "has_more": False},
		)
		[run] = данные["runs"]
		отмечено = run["goals"][0].pop("marked_at")
		self.assertTrue(отмечено)
		self.assertEqual(run.pop("started_at"), отмечено)
		self.assertEqual(
			run,
			{
				"student": {"id": self.ученик, "name": f"goal-runs-p1-{self.суффикс}"},
				"lesson": {"key": "l-1", "title": "Урок первый"},
				"status": "in_progress",
				"accepted_at": None,
				"passed_at": None,
				"resume_from": "С выбора варианта",
				"objectives": [
					{
						"objective_key": "l-1-D1",
						"status": "touched",
						"quiz_confirmed": False,
						"removed": False,
					}
				],
				"goals": [
					{
						"objective_key": "l-1-D1",
						"goal_key": "term:T1",
						"title": "Термин «пример»",
						"status": "done",
						"evidence": "Назвал термин сам",
						"session": занятие,
						"removed": False,
					},
					{
						"objective_key": "l-1-D1",
						"goal_key": "l-1-D1/V1",
						"title": "Выбор: «первый»",
						"status": "open",
						"evidence": None,
						"marked_at": None,
						"session": None,
						"removed": False,
					},
					{
						"objective_key": "l-1-D1",
						"goal_key": "refute:M1",
						"title": "Если проявится: «пример не нужен»",
						"status": "open",
						"evidence": None,
						"marked_at": None,
						"session": None,
						"removed": False,
					},
				],
			},
		)

	def test_модератор_и_администратор_видят_прохождения_чужого_курса(self):
		self.пройти(self.ученик, "l-1", ("term:T1", "done", "Назвал термин сам"))
		frappe.set_user("Administrator")
		for роль in ("Moderator", "System Manager"):
			кто = создать_куратора(f"goal-runs-{роль[:3].lower()}-{self.суффикс}@example.com", роль)
			with self.subTest(роль=роль):
				[run] = self.прохождения(кто)["runs"]
				self.assertEqual(run["goals"][0]["evidence"], "Назвал термин сам")

	def test_чужому_куратору_и_ученику_403_гостю_401(self):
		"""Курс ведут его инструкторы: Course Creator, который курс не ведёт,
		прохождений не видит, как и сам ученик."""
		self.пройти(self.ученик, "l-1", ("term:T1", "done", "Назвал термин сам"))
		чужой = создать_куратора(f"goal-runs-other-{self.суффикс}@example.com")
		for кто, ошибка in (
			(чужой, frappe.PermissionError),
			(self.ученик, frappe.PermissionError),
			("Guest", frappe.AuthenticationError),
		):
			frappe.set_user(кто)
			with self.subTest(кто=кто), self.assertRaises(ошибка):
				authoring.goal_runs(course=self.курс)

	def test_инструктор_из_публикации_видит_а_снятый_нет(self):
		"""Доступ — по списку инструкторов курса, а не по тому, кто публиковал."""
		self.пройти(self.ученик, "l-1")
		новый = создать_куратора(f"goal-runs-new-{self.суффикс}@example.com")
		frappe.set_user(self.автор)
		ответ = authoring.publish_release(
			release=пример_релиза(f"goal-runs-{self.суффикс}"), instructors=[новый]
		)
		self.assertTrue(ответ["ok"], ответ)

		self.assertEqual(len(self.прохождения(новый)["runs"]), 1)
		frappe.set_user(self.автор)
		with self.assertRaises(frappe.PermissionError):
			authoring.goal_runs(course=self.курс)

	# --- отбор ---

	def test_архивные_прохождения_не_отдаются(self):
		self.пройти(self.ученик, "l-1", ("term:T1", "done", "Назвал термин сам"))
		архивное, _ = self.пройти(self.второй, "l-1", ("term:T1", "done", "Тоже назвал"))
		frappe.db.set_value(
			ПРОХОЖДЕНИЕ,
			архивное,
			{"student": None, "archived_student": self.второй, "archived_at": now_datetime()},
		)

		self.assertEqual([r["student"]["id"] for r in self.прохождения()["runs"]], [self.ученик])

	def test_фильтр_по_ключу_урока(self):
		self.пройти(self.ученик, "l-1", ("term:T1", "done", "Назвал термин сам"))
		self.пройти(self.ученик, "l-2", ("term:T1", "done", "Назвал термин сам"))
		self.пройти(self.второй, "l-2")

		второй_урок = self.прохождения(lesson="l-2")
		self.assertEqual(второй_урок["lesson"], "l-2")
		self.assertEqual(
			sorted((r["student"]["id"], r["lesson"]["key"]) for r in второй_урок["runs"]),
			sorted([(self.ученик, "l-2"), (self.второй, "l-2")]),
		)
		self.assertEqual({r["lesson"]["title"] for r in второй_урок["runs"]}, {"Урок второй"})
		self.assertEqual(self.прохождения(lesson="нет-такого")["runs"], [])
		self.assertEqual(len(self.прохождения(lesson="")["runs"]), 3)

	# --- страницы ---

	def test_порядок_и_страницы(self):
		"""Свежее начало сверху, не начатые — в конце; страницы не перекрываются."""
		начатые = []
		for ученик in (self.ученик, self.второй):
			for ключ in ("l-1", "l-2"):
				имя, _ = self.пройти(ученик, ключ, ("term:T1", "done", "Назвал термин сам"))
				начатые.append(имя)
		for номер, имя in enumerate(начатые):
			frappe.db.set_value(ПРОХОЖДЕНИЕ, имя, "started_at", f"2026-09-0{номер + 1} 10:00:00")
		self.пройти(self.ученик, "l-3")
		ожидаемый = [(self.второй, "l-2"), (self.второй, "l-1"), (self.ученик, "l-2"), (self.ученик, "l-1")]
		ожидаемый.append((self.ученик, "l-3"))

		def ключи(данные):
			return [(r["student"]["id"], r["lesson"]["key"]) for r in данные["runs"]]

		self.assertEqual(ключи(self.прохождения()), ожидаемый)
		self.assertIsNone(self.прохождения()["runs"][-1]["started_at"])
		страницы = [self.прохождения(start=начало, limit=2) for начало in (0, 2, 4)]
		self.assertEqual([ключ for с in страницы for ключ in ключи(с)], ожидаемый)
		self.assertEqual([с["has_more"] for с in страницы], [True, True, False])
		# Параметры строкой — как их отдаёт Frappe из запроса.
		self.assertEqual(ключи(self.прохождения(start="1", limit="1")), ожидаемый[1:2])
		self.assertEqual(self.прохождения(start=5)["runs"], [])

	def test_limit_ограничен_пределом_сервера(self):
		for ученик in (self.ученик, self.второй):
			self.пройти(ученик, "l-1")
		self.assertEqual(self.прохождения(limit=1000)["limit"], authoring.ПРОХОЖДЕНИЙ_ЗА_РАЗ)
		with patch.object(authoring, "ПРОХОЖДЕНИЙ_ЗА_РАЗ", 1):
			for limit in (None, 5):
				with self.subTest(limit=limit):
					данные = self.прохождения(limit=limit)
					self.assertEqual((len(данные["runs"]), данные["limit"], данные["has_more"]), (1, 1, True))

	def test_неверная_страница_отказ(self):
		for аргументы, поле in (
			({"limit": 0}, "limit"),
			({"limit": "-1"}, "limit"),
			({"limit": "десять"}, "limit"),
			({"limit": 2.5}, "limit"),
			({"start": -1}, "start"),
			({"start": "1a"}, "start"),
		):
			with self.subTest(**{k: str(v) for k, v in аргументы.items()}):
				код, ошибка = self.код(course=self.курс, **аргументы)
				self.assertEqual((код, ошибка["where"]), ("invalid_page", поле))

	def test_курс_без_релиза_и_неизвестный_курс(self):
		frappe.set_user("Administrator")
		анонс = frappe.get_doc("LMS Course", создать_курс(f"Анонс {self.суффикс}"))
		анонс.append("instructors", {"instructor": self.автор})
		анонс.save(ignore_permissions=True)
		анонс = анонс.name

		self.assertEqual(self.код(course=анонс)[0], "course_not_released")
		self.assertEqual(self.код(course="такого-курса-нет")[0], "course_not_found")

	# --- релизы ---

	def test_снятые_цели_и_пункты_и_название_записи_урока(self):
		ключ = f"goal-runs-two-{self.суффикс}"
		курс = self.опубликовать(релиз_двух_целей(ключ))
		self.пройти(self.ученик, "l-1", ("exec:E1", "planned", "Сделает дома"), курс=курс)

		новый = релиз_двух_целей(ключ)
		урок = новый["lessons"][0]
		урок["title"] = "Урок с одной целью"
		урок["objectives"] = урок["objectives"][:1]
		урок["objectives"][0]["goals"] = [п for п in урок["objectives"][0]["goals"] if п["key"] != "exec:E1"]
		del новый["agent"]["lessons"]["l-1"]["items"]["exec:E1"]
		del новый["agent"]["lessons"]["l-1"]["items"]["return:R1"]
		with patch.object(frappe, "enqueue"):
			self.опубликовать(новый)

		# Прохождение ещё на прошлом релизе: пункты — его, название — записи урока,
		# которую проекция уже переименовала.
		[run] = self.прохождения(course=курс)["runs"]
		self.assertEqual(run["lesson"]["title"], "Урок с одной целью")
		self.assertFalse(any(п["removed"] for п in run["goals"]))

		прохождения.сверить_курс(курс)
		[run] = self.прохождения(course=курс)["runs"]

		self.assertEqual(run["lesson"]["title"], "Урок с одной целью")
		self.assertEqual(
			[(ц["objective_key"], ц["removed"]) for ц in run["objectives"]],
			[("l-1-D1", False), ("l-1-D2", True)],
		)
		self.assertEqual(
			[(п["goal_key"], п["removed"]) for п in run["goals"]],
			[("term:T1", False), ("refute:M1", False), ("exec:E1", True), ("return:R1", True)],
		)
		снятый = run["goals"][2]
		self.assertEqual(
			(снятый["status"], снятый["evidence"], снятый["title"]),
			("planned", "Сделает дома", "Сделать пример"),
		)
