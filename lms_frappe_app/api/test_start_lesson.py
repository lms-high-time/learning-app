# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""`start_lesson` по прохождению урока курса из релиза (learning-services#506)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from lms_frappe_app.agent_learning.releases.course_guard import ИЗ_РЕЛИЗА
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.api import student
from lms_frappe_app.tests.release_sample import релиз_двух_целей
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	зачислить_на_курс,
	курс_из_релиза,
	настроить_квиз,
	отметить_все_пункты,
	политика_по_умолчанию,
	создать_занятие,
	создать_организацию,
	создать_урок,
	создать_ученика,
	урок_релиза,
)


class IntegrationTestStartLesson(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		"""Строки курса и релиза тесты не меняют; занятия и прохождения пишет ученик
		своего теста. Тест, который правит строки релиза или уроки курса, заводит
		себе свой курс — `свой_курс`."""
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.курс, cls.релиз = курс_из_релиза()
		cls.урок = урок_релиза(cls.курс, "l-1")

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(политика_по_умолчанию)
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"start-{суффикс}@example.com")
		зачислить_на_курс(self.ученик, self.курс)
		frappe.set_user(self.ученик)

	def старт(self, **параметры) -> dict:
		ответ = student.start_lesson(**параметры)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def прохождение(self, ключ: str = "l-1", курс: str | None = None):
		return frappe.get_doc(
			"Agent Lesson Run",
			{"student": self.ученик, "course": курс or self.курс, "lesson_key": ключ},
		)

	def свой_курс(self) -> tuple[str, str, str]:
		"""Курс только этого теста, ученик на нём: (курс, релиз, первый урок)."""
		frappe.set_user("Administrator")
		try:
			курс, релиз = курс_из_релиза()
			зачислить_на_курс(self.ученик, курс)
		finally:
			frappe.set_user(self.ученик)
		return курс, релиз, урок_релиза(курс, "l-1")

	def курс_двух_целей(self) -> tuple[str, str]:
		frappe.set_user("Administrator")
		курс, _ = курс_из_релиза(релиз=релиз_двух_целей(f"start-two-{frappe.generate_hash(length=6)}"))
		зачислить_на_курс(self.ученик, курс)
		frappe.set_user(self.ученик)
		return курс, урок_релиза(курс, "l-1")

	# --- ответ ---

	def test_урок_пакет_и_квиз_из_релиза(self):
		данные = self.старт(lesson=self.урок)

		self.assertEqual(
			данные["lesson"],
			{
				"id": self.урок,
				"key": "l-1",
				"title": "Урок первый",
				"hook": "Зачин урока «Урок первый»",
				"chapter": {"key": "ch-1", "title": "Глава первая"},
				"course": self.курс,
				"overdue": False,
			},
		)
		self.assertEqual(данные["course_promise"], "К концу курса вы умеете пример.")
		self.assertIn("Директива урока l-1", данные["directive"])
		self.assertIn("Тезисы урока l-1", данные["material"])
		self.assertEqual(данные["section_hints"], {"log": "Урок l-1 заполняет раздел log."})
		self.assertEqual(данные["quiz"], {"required": True, "pass_threshold": 0.7, "attempts_left": 3})
		self.assertEqual(данные["start"]["opening"], "first_in_course")
		self.assertIsNone(данные["start"]["last_session_at"])
		self.assertIsNone(данные["resume_from"])
		self.assertEqual(
			frappe.get_all(
				"Agent Session Event", filters={"session": данные["session"]}, pluck="kind", ignore_permissions=True
			),
			["Directive Issued"],
		)
		занятие = frappe.get_doc("Agent Learning Session", данные["session"])
		self.assertEqual((занятие.student, занятие.lesson, занятие.course), (self.ученик, self.урок, self.курс))
		self.assertTrue(занятие.via_trusted_service)

	def test_срез_без_директивы_материала_и_подсказок_пустой(self):
		_, релиз, урок = self.свой_курс()
		frappe.db.set_value("Agent Release Lesson", {"parent": релиз, "lesson_key": "l-1"}, "agent", "{}")

		данные = self.старт(lesson=урок)

		self.assertEqual((данные["directive"], данные["material"], данные["section_hints"]), ("", "", {}))

	def test_карта_урока_со_статусами_и_без_снятого(self):
		курс, урок = self.курс_двух_целей()
		run = прохождения.прохождение(self.ученик, курс, "l-1")
		прохождения.отметить(run.name, "term:T1", "done", "Назвал термин")
		frappe.db.set_value(
			"Agent Lesson Run Goal", {"parent": run.name, "goal_key": "refute:M1"}, "removed", 1
		)

		карта = self.старт(lesson=урок)["lesson_map"]

		self.assertEqual(
			карта,
			[
				{
					"key": "l-1-D1",
					"text": "Цель с обязательными пунктами",
					"status": "touched",
					"goals": [
						{"key": "term:T1", "kind": "term", "required": True, "title": "Термин «пример»", "status": "done"},
						{"key": "exec:E1", "kind": "execution", "required": True, "title": "Сделать пример", "status": "open"},
					],
				},
				{
					"key": "l-1-D2",
					"text": "Цель без обязательных пунктов",
					"status": "covered",
					"goals": [
						{"key": "return:R1", "kind": "return", "required": False, "title": "Вернуться к примеру", "status": "open"}
					],
				},
			],
		)

	def test_следующий_шаг_пункт_квиз_закрытие_и_ничего(self):
		курс, урок = self.курс_двух_целей()

		первый = self.старт(lesson=урок)
		self.assertEqual(
			первый["next_step"],
			{"kind": "goal", "objective": "l-1-D1", "goal": "term:T1", "title": "Термин «пример»"},
		)

		отметить_все_пункты(self.прохождение(курс=курс).name, первый["session"])
		self.assertEqual(self.старт(lesson=урок)["next_step"], {"kind": "quiz"})

		frappe.set_user("Administrator")
		настроить_квиз(quiz_required=0)
		frappe.set_user(self.ученик)
		данные = self.старт(lesson=урок)
		self.assertEqual(данные["next_step"], {"kind": "complete"})
		self.assertFalse(данные["quiz"]["required"])

		прохождения.отметить_пройденным(self.ученик, курс, "l-1")
		self.assertIsNone(self.старт(lesson=урок)["next_step"])

	def test_урок_без_вопросов_закрывается_без_квиза(self):
		курс, релиз, урок = self.свой_курс()
		frappe.db.delete("Agent Release Question", {"parent": релиз, "lesson_key": "l-1"})
		отметить_все_пункты(прохождения.прохождение(self.ученик, курс, "l-1").name)

		данные = self.старт(lesson=урок)

		self.assertEqual(данные["next_step"], {"kind": "complete"})
		self.assertEqual(данные["quiz"], {"required": False, "pass_threshold": 0.7, "attempts_left": None})

	# --- рамка ---

	def test_рамка_только_по_просьбе(self):
		рамка = self.старт(lesson=self.урок)["course_frame"]
		self.assertEqual(рамка["frame"], "Рамка курса: ученик, граница, контур ведения.")
		self.assertEqual([э["key"] for э in рамка["learn_about_student"]], ["where_applies", "c-team"])

		for без_рамки in (False, 0, "false"):
			self.assertNotIn("course_frame", self.старт(lesson=self.урок, frame=без_рамки), без_рамки)

	# --- история ---

	def отметить(self, ключ: str, когда) -> None:
		run = прохождения.прохождение(self.ученик, self.курс, ключ)
		прохождения.отметить(run.name, "term:T1", "done", "Назвал термин")
		frappe.db.set_value("Agent Lesson Run Goal", {"parent": run.name, "goal_key": "term:T1"}, "marked_at", когда)
		frappe.db.set_value("Agent Lesson Run", run.name, "started_at", когда)

	def test_история_ограничена_глубиной_и_несёт_незакрытые_цели(self):
		frappe.set_user("Administrator")
		настроить_квиз(carry_over_depth=1)
		frappe.set_user(self.ученик)
		self.отметить("l-1", add_to_date(now_datetime(), days=-2))
		self.отметить("l-2", add_to_date(now_datetime(), days=-1))

		история = self.старт(lesson=урок_релиза(self.курс, "l-3"))["history"]["lessons"]

		self.assertEqual(
			история,
			[
				{
					"key": "l-2",
					"title": "Урок второй",
					"status": "in_progress",
					"objectives_open": [{"key": "l-2-D1", "text": "Цель урока «Урок второй»", "status": "touched"}],
				}
			],
		)

	def test_история_без_урока_старта_и_без_неначатых(self):
		self.отметить("l-2", add_to_date(now_datetime(), days=-1))
		прохождения.прохождение(self.ученик, self.курс, "l-3")
		отметить_все_пункты(прохождения.прохождение(self.ученик, self.курс, "l-1").name)

		история = self.старт(lesson=self.урок)["history"]["lessons"]

		self.assertEqual([у["key"] for у in история], ["l-2"])

	def test_пройденный_урок_в_истории_без_незакрытых_целей(self):
		отметить_все_пункты(прохождения.прохождение(self.ученик, self.курс, "l-1").name)

		история = self.старт(lesson=урок_релиза(self.курс, "l-2"))["history"]["lessons"]

		self.assertEqual(история, [{"key": "l-1", "title": "Урок первый", "status": "covered", "objectives_open": []}])

	def test_заметки_по_ключам_что_выяснять_и_прочие(self):
		student.remember("fact", "where_applies", "В отделе продаж")
		student.remember("fact", "role", "Руководитель")
		занятие = создать_занятие(self.ученик, self.урок)
		student.remember("observation", "c-team", "Пятеро в команде", session=занятие)

		заметки = self.старт(lesson=self.урок)["history"]["notes"]

		self.assertEqual(заметки["by_key"], {"where_applies": "В отделе продаж", "c-team": "Пятеро в команде"})
		self.assertEqual([(з["key"], з["kind"], з["text"]) for з in заметки["other"]], [("role", "fact", "Руководитель")])

	# --- репорты ---

	# --- прохождение и занятие ---

	def test_прохождение_принято_один_раз_и_занятие_на_нём(self):
		первое = self.старт(lesson=self.урок)["session"]
		run = self.прохождение()
		self.assertTrue(run.accepted_at)
		self.assertEqual(frappe.db.get_value("Agent Learning Session", первое, "run"), run.name)
		frappe.db.set_value("Agent Lesson Run", run.name, "accepted_at", "2026-01-01 10:00:00")

		второе = self.старт(lesson=self.урок)["session"]

		self.assertEqual(второе, первое)
		self.assertEqual(str(frappe.db.get_value("Agent Lesson Run", run.name, "accepted_at")), "2026-01-01 10:00:00")
		self.assertEqual(frappe.db.count("Agent Lesson Run", {"student": self.ученик}), 1)

	def test_занятие_без_прохождения_получает_его(self):
		занятие = создать_занятие(self.ученик, self.урок)

		данные = self.старт(lesson=self.урок)

		self.assertEqual(данные["session"], занятие)
		self.assertEqual(frappe.db.get_value("Agent Learning Session", занятие, "run"), self.прохождение().name)

	def test_гонка_за_прохождение_busy_и_занятия_нет(self):
		with (
			patch.object(прохождения, "прохождение", side_effect=frappe.QueryDeadlockError("1213")),
			patch.object(frappe.db, "rollback") as откат,
		):
			ответ = student.start_lesson(lesson=self.урок)

		self.assertEqual(ответ["error"]["code"], "busy")
		self.assertEqual(ответ["error"]["lesson"], self.урок)
		откат.assert_called_once_with()
		self.assertFalse(frappe.db.exists("Agent Learning Session", {"student": self.ученик}))

	# --- отказы до записи ---

	def нет_записей(self) -> None:
		self.assertFalse(frappe.db.exists("Agent Learning Session", {"student": self.ученик}))
		self.assertFalse(frappe.db.exists("Agent Lesson Run", {"student": self.ученик}))

	def test_отказы_без_записей(self):
		"""Курс без релиза, урок не из релиза, чужой курс, неизвестный канал —
		отказ без занятия и прохождения. Кончившаяся проба веб-чата — последней:
		она меняет настройку и заводит пробное занятие."""
		frappe.set_user("Administrator")
		без_релиза = создать_урок(f"Без релиза {frappe.generate_hash(length=6)}")
		курс_без_релиза = зачислить(self.ученик, без_релиза)
		_, _, свой_урок = self.свой_курс()
		frappe.set_user("Administrator")
		глава = frappe.db.get_value("Course Lesson", свой_урок, "chapter")
		лишний = frappe.get_doc({"doctype": "Course Lesson", "title": "Вне релиза", "chapter": глава})
		# Мимо охраны курса из релиза: урок вне релиза у курса из релиза —
		# запись, сделанная мимо публикации.
		лишний.flags[ИЗ_РЕЛИЗА] = True
		лишний.insert(ignore_permissions=True)
		чужой, _ = курс_из_релиза()
		frappe.set_user(self.ученик)

		отказы = {
			"курс без релиза": (
				{"lesson": без_релиза},
				"course_not_released",
				{"course": курс_без_релиза, "lesson": без_релиза},
			),
			"урок не из релиза": ({"lesson": лишний.name}, "course_not_released", {}),
			"чужой курс": ({"lesson": урок_релиза(чужой, "l-1")}, "not_enrolled", {}),
			"неизвестный канал": ({"lesson": self.урок, "channel": "sms"}, "unknown_channel", {}),
		}
		for что, (параметры, код, подробности) in отказы.items():
			with self.subTest(что):
				ответ = student.start_lesson(**параметры)

				self.assertEqual(ответ["error"]["code"], код)
				self.assertEqual({к: ответ["error"][к] for к in подробности}, подробности)
				self.нет_записей()

		with self.subTest("проба веб-чата кончилась"):
			try:
				frappe.set_user("Administrator")
				настроить_квиз(web_demo_lessons=1)
				frappe.set_user(self.ученик)
				пробный = создать_занятие(self.ученик, урок_релиза(self.курс, "l-2"))
				frappe.db.set_value("Agent Learning Session", пробный, "web_chat", 1)

				ответ = student.start_lesson(lesson=self.урок, channel="web")

				self.assertEqual(ответ["error"]["code"], "web_demo_exhausted")
				self.assertEqual(
					frappe.get_all("Agent Learning Session", {"student": self.ученик}, pluck="name"), [пробный]
				)
				self.assertFalse(frappe.db.exists("Agent Lesson Run", {"student": self.ученик}))
			finally:
				frappe.set_user("Administrator")
				политика_по_умолчанию()
				frappe.set_user(self.ученик)

	# --- веб-канал и начало ---

	def test_веб_чат_получает_место_урока_и_пробу(self):
		урок = self.старт(lesson=урок_релиза(self.курс, "l-2"), channel="web")["lesson"]

		self.assertEqual((урок["number"], урок["total"]), (2, 3))
		self.assertEqual(урок["course_title"], "Пример курса")
		self.assertEqual(урок["web_demo"]["used"], 1)
		self.assertNotIn("number", self.старт(lesson=self.урок)["lesson"])

	def test_начало_по_следу_отметок(self):
		первое = self.старт(lesson=self.урок)
		self.assertEqual(первое["start"]["opening"], "first_in_course")
		# Занятие без отметок — не след: брошенная в начале попытка не в счёт.
		frappe.db.set_value("Agent Learning Session", первое["session"], "status", "Abandoned")
		self.assertEqual(self.старт(lesson=self.урок)["start"]["opening"], "first_in_course")

		второе = self.старт(lesson=self.урок)["session"]
		прохождения.отметить(self.прохождение().name, "term:T1", "done", "Назвал термин", занятие=второе)
		frappe.db.set_value("Agent Learning Session", второе, "status", "Abandoned")

		self.assertEqual(self.старт(lesson=self.урок)["start"]["opening"], "repeat")
		self.assertEqual(self.старт(lesson=урок_релиза(self.курс, "l-2"))["start"]["opening"], "new_lesson")

	# --- блоки документа ---

	def test_блоки_документа_своего_урока_с_заполненностью(self):
		student.update_artifact(self.курс, "notebook", "log", rows=[{"topic": "Первая встреча"}])

		[журнал] = self.старт(lesson=self.урок)["artifact_blocks"]
		блоки = self.старт(lesson=урок_релиза(self.курс, "l-2"))["artifact_blocks"]

		self.assertEqual(
			журнал,
			{
				"artifact": "notebook",
				"key": "log",
				"title": "Журнал",
				"description": "Что сюда записывают.",
				"filled": True,
			},
		)
		self.assertEqual([(б["artifact"], б["key"]) for б in блоки], [("notebook", "rules")])
		self.assertEqual(self.старт(lesson=урок_релиза(self.курс, "l-3"))["artifact_blocks"], [])


class IntegrationTestStartLessonChoice(IntegrationTestCase):
	"""Какой урок открывает `start_lesson` без `lesson`: по сроку, по последнему занятию, по курсу."""

	@classmethod
	def setUpClass(cls):
		"""Курсы тесты только читают; назначения — у организации своего теста,
		занятия и прогресс — у её ученика."""
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.курс, _ = курс_из_релиза()
		cls.урок = урок_релиза(cls.курс, "l-1")
		cls._срочный = None

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"choice-{суффикс}@example.com")
		self.организация = создать_организацию(f"Выбор {суффикс}")
		добавить_в_организацию(self.ученик, self.организация)
		self.назначить(self.курс, "2026-12-31")
		frappe.set_user(self.ученик)

	def назначить(self, курс: str, срок: str) -> None:
		frappe.set_user("Administrator")
		try:
			frappe.get_doc(
				{
					"doctype": "Course Allocation",
					"organization": self.организация,
					"course": курс,
					"deadline": срок,
					"mandatory": 1,
				}
			).insert(ignore_permissions=True)
		finally:
			frappe.set_user(self.ученик)

	def курс_со_сроком(self, срок: str) -> str:
		"""Второй курс, назначенный организации теста со сроком `срок`. Курс один
		на класс и заводится при первом обращении: тесты его только читают."""
		класс = type(self)
		if класс._срочный is None:
			класс._срочный, _ = курс_из_релиза()
		self.назначить(класс._срочный, срок)
		return класс._срочный

	def урок_старта(self, **параметры) -> str:
		ответ = student.start_lesson(**параметры)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]["lesson"]["id"]

	def test_без_аргумента_урок_с_ближайшим_сроком(self):
		срочный = self.курс_со_сроком("2026-06-30")

		self.assertEqual(self.урок_старта(), урок_релиза(срочный, "l-1"))

	def test_без_аргумента_курс_последнего_занятия(self):
		self.курс_со_сроком("2026-06-30")
		создать_занятие(self.ученик, self.урок)

		self.assertEqual(self.урок_старта(), self.урок)

	def test_с_курсом_урок_этого_курса_а_урок_важнее_курса(self):
		срочный = self.курс_со_сроком("2026-06-30")
		второй = урок_релиза(self.курс, "l-2")

		self.assertEqual(self.урок_старта(course=self.курс), self.урок)
		self.assertEqual(self.урок_старта(lesson=второй, course=срочный), второй)

	def test_пройденный_курс_нечего_учить(self):
		frappe.set_user("Administrator")
		for ключ in ("l-1", "l-2", "l-3"):
			frappe.get_doc(
				{
					"doctype": "LMS Course Progress",
					"member": self.ученик,
					"lesson": урок_релиза(self.курс, ключ),
					"status": "Complete",
				}
			).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)

		ответ = student.start_lesson(course=self.курс)

		self.assertEqual(ответ["error"]["code"], student.НЕЧЕГО_УЧИТЬ)
		self.assertEqual(ответ["error"]["course"], self.курс)

	def test_чужой_курс_отказ_по_доступу_а_не_по_пройденности(self):
		frappe.set_user("Administrator")
		чужой, _ = курс_из_релиза()
		frappe.set_user(self.ученик)

		ответ = student.start_lesson(course=чужой)

		self.assertEqual(ответ["error"]["code"], "not_enrolled")
		self.assertFalse(frappe.db.exists("Agent Learning Session", {"student": self.ученик, "course": чужой}))
