# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Занятие по уроку для веб-чата (lms-platform#151).

Метод только читает: по нему сервис узнаёт, идёт ли по уроку разговор и
закрыт ли урок, — и решает, показать историю или начинать занятие. Поэтому он
не заводит занятий и не пишет событий в журнал: иначе каждый возврат на
страницу оставлял бы след, которого не было.
"""

import json
from datetime import datetime, timedelta

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.agent_learning import quiz
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.tests.sample_data import (
	зачислить,
	зачислить_на_курс,
	курс_из_релиза,
	отметить_все_пункты,
	политика_по_умолчанию,
	создать_занятие,
	создать_ученика,
	урок_релиза,
)
from lms_frappe_app.api import student

СОСТОЯНИЕ = json.dumps({"id": "conv-1", "messages": []})


class IntegrationTestLessonSession(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"lesson-{суффикс}@example.com")
		self.курс, _ = курс_из_релиза()
		self.урок = урок_релиза(self.курс, "l-1")
		зачислить(self.ученик, self.урок)
		self.чужой = создать_ученика(f"lesson-other-{суффикс}@example.com")
		# Зачисление — под Administrator: обычному ученику Learning запрещает
		# записываться на неопубликованный курс, а тестовые курсы такие и есть.
		зачислить(self.чужой, self.урок)
		frappe.set_user(self.ученик)

	def событий(self, занятие: str) -> int:
		return frappe.db.count("Agent Session Event", {"session": занятие})

	def занятий(self) -> int:
		return frappe.db.count("Agent Learning Session", {"student": self.ученик, "lesson": self.урок})

	def test_последнее_своё_занятие_урока(self):
		"""Чужое занятие не видно; отдаётся последнее занятие урока; метод ничего
		не заводит и не пишет в журнал. Закрытое занятие с разговором видно
		целиком — ради этого метод и заводится: ученик вернулся к пройденному
		уроку, и сервису нужно найти разговор, а не начинать урок заново."""
		чужое = создать_занятие(self.чужой, self.урок)
		with self.subTest("чужое занятие не видно"):
			данные = student.lesson_session(self.урок)["data"]

			self.assertNotEqual(данные["session"], чужое)
			self.assertEqual(данные["session"], None)

		первое = создать_занятие(self.ученик, self.урок)
		frappe.db.set_value("Agent Learning Session", первое, "status", "Abandoned")
		последнее = создать_занятие(self.ученик, self.урок)
		with self.subTest("последнее занятие урока"):
			данные = student.lesson_session(self.урок)["data"]

			self.assertEqual(данные["session"], последнее)

		with self.subTest("ничего не заводит и не пишет в журнал"):
			событий_до = self.событий(последнее)
			занятий_до = self.занятий()

			student.lesson_session(self.урок)
			student.lesson_session(self.урок)

			self.assertEqual(self.событий(последнее), событий_до)
			self.assertEqual(self.занятий(), занятий_до)
			self.assertFalse(frappe.db.exists(прохождения.ПРОХОЖДЕНИЕ, {"student": self.ученик}))

		занятие = создать_занятие(self.ученик, self.урок)
		student.save_chat_state(занятие, СОСТОЯНИЕ, "1")
		запись = frappe.get_doc("Agent Learning Session", занятие)
		quiz.отметить_урок_пройденным(запись)
		запись.status = "Completed"
		запись.save(ignore_permissions=True)
		with self.subTest("закрытое занятие с разговором видно целиком"):
			данные = student.lesson_session(self.урок)["data"]

			self.assertEqual(данные["session"], занятие)
			self.assertEqual(данные["status"], "Completed")
			self.assertTrue(данные["has_chat_state"])
			self.assertTrue(данные["completed"])


class IntegrationTestStartState(IntegrationTestCase):
	"""С чего начинать занятие — решает сервер (#238).

	Признаки — первое ли занятие по курсу, повтор ли урока, есть ли
	незакрытое, сколько прошло. Разбирать их заново на каждом старте — место
	для ошибки агента, поэтому сервер сводит их в одно `opening`. След
	прошлого занятия — отметки пунктов прохождения с этим занятием или
	закрытый урок (learning-services#506).
	"""

	@classmethod
	def setUpClass(cls):
		"""Курс и релиз тесты не меняют: история занятий — у ученика своего теста.
		Тест, который правит курс и строки релиза, заводит себе свой — `свой_курс`."""
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.курс, cls.релиз = курс_из_релиза()
		cls.урок = урок_релиза(cls.курс, "l-1")
		cls.второй = урок_релиза(cls.курс, "l-2")

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(политика_по_умолчанию)
		self.новый_ученик()

	def новый_ученик(self) -> None:
		frappe.set_user("Administrator")
		self.ученик = создать_ученика(f"start-{frappe.generate_hash(length=6)}@example.com")
		зачислить_на_курс(self.ученик, self.курс)
		frappe.set_user(self.ученик)

	def свой_курс(self) -> None:
		"""Курс только этого теста — для теста, который правит курс и строки релиза."""
		frappe.set_user("Administrator")
		self.курс, self.релиз = курс_из_релиза()
		зачислить_на_курс(self.ученик, self.курс)
		self.урок = урок_релиза(self.курс, "l-1")
		self.второй = урок_релиза(self.курс, "l-2")
		frappe.set_user(self.ученик)

	def начало(self, урок: str) -> dict:
		ответ = student.start_lesson(lesson=урок)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def прошлое(self, ключ: str, часов_назад: float, незакрыто: bool) -> None:
		"""Завершённое занятие в прошлом с отметками пунктов — с незакрытой целью или без."""
		когда = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-часов_назад)
		run = прохождения.прохождение(self.ученик, self.курс, ключ)
		занятие = создать_занятие(self.ученик, run.lesson, run=run.name)
		if незакрыто:
			прохождения.отметить(run.name, "term:T1", "done", "Назвал термин", занятие=занятие)
		else:
			отметить_все_пункты(run.name, занятие)
		frappe.db.set_value(
			"Agent Learning Session",
			занятие,
			{"status": "Completed", "started_at": когда, "last_activity_at": когда, "finished_at": когда},
		)

	def test_пустые_зачин_и_обещание_отдаются_как_none(self):
		self.свой_курс()
		frappe.db.set_value("LMS Course", self.курс, "course_promise", "")
		frappe.db.set_value("Agent Release Lesson", {"parent": self.релиз, "lesson_key": "l-1"}, "hook", "")

		данные = self.начало(self.урок)

		self.assertIsNone(данные["course_promise"])
		self.assertIsNone(данные["lesson"]["hook"])

	def test_мостик_после_перерыва(self):
		"""Проводка `начало_занятия`: прочие занятия со следом, незакрытое из
		истории и перерыв из настроек — 2 часа назад при перерыве в 1 час
		дают `return`. Граничные случаи правила — `TestНачалоЗанятия`."""
		прежний = frappe.db.get_single_value("Agent Learning Settings", "bridge_after_hours")
		self.перерыв(1)
		try:
			self.прошлое("l-1", часов_назад=2, незакрыто=True)

			данные = self.начало(self.второй)
		finally:
			self.перерыв(прежний)

		self.assertEqual(данные["start"]["opening"], "return")
		self.assertIsNotNone(данные["start"]["last_session_at"])
		self.assertEqual(
			[(у["key"], [ц["key"] for ц in у["objectives_open"]]) for у in данные["history"]["lessons"]],
			[("l-1", ["l-1-D1"])],
		)

	def перерыв(self, часов: int) -> None:
		"""`bridge_after_hours` в настройках — под Administrator, с возвратом ученика."""
		frappe.set_user("Administrator")
		try:
			frappe.db.set_single_value("Agent Learning Settings", "bridge_after_hours", часов)
			frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")
		finally:
			frappe.set_user(self.ученик)

	def брошенная_попытка(self, урок: str) -> None:
		"""Занятие без следа: ни отметки пункта, ни закрытого урока."""
		занятие = создать_занятие(self.ученик, урок)
		frappe.db.set_value("Agent Learning Session", занятие, "status", "Abandoned")

	def test_брошенная_попытка_не_повтор_и_не_прошлое_занятие(self):
		"""#408: две брошенные попытки своим агентом, потом веб-чат — человек
		урока не видел, начинать надо как с первого занятия по курсу."""
		self.брошенная_попытка(self.урок)
		self.брошенная_попытка(self.урок)
		данные = self.начало(self.урок)
		self.assertEqual(данные["start"]["opening"], "first_in_course")
		self.assertIsNone(данные["start"]["last_session_at"])

	def test_закрытый_урок_без_отметок_это_повтор(self):
		self.брошенная_попытка(self.урок)
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "LMS Course Progress",
				"member": self.ученик,
				"course": self.курс,
				"lesson": self.урок,
				"status": "Complete",
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)
		self.assertEqual(self.начало(self.урок)["start"]["opening"], "repeat")

	def test_продолжение_занятия_не_повтор(self):
		"""Незакрытое занятие того же урока переиспользуется — продолжение не
		должно выглядеть повтором, и отметки в нём самом — тоже."""
		первый_раз = self.начало(self.урок)
		прохождения.отметить(
			frappe.db.get_value("Agent Learning Session", первый_раз["session"], "run"),
			"term:T1",
			"done",
			"Назвал термин",
			занятие=первый_раз["session"],
		)
		ещё_раз = self.начало(self.урок)
		self.assertEqual(ещё_раз["session"], первый_раз["session"])
		self.assertEqual(ещё_раз["start"]["opening"], "first_in_course")


class TestНачалоЗанятия(UnitTestCase):
	"""Без базы: с чего начинать занятие — `начало_занятия` (#238)."""

	def test_opening_по_прочим_занятиям(self):
		"""Приоритет `repeat` → `first_in_course` → `return` → `new_lesson`;
		`return` — только при незакрытом и строго больше перерыва. Перерыв
		запрашивается только на проверке `return` — последнее число строки."""
		сейчас = datetime(2026, 10, 13, 12, 0)

		def занятие(
			урок: str = "L0", часов_назад: float | None = 48, начато_часов_назад: float | None = None
		):
			"""Прочее занятие: `last_activity_at` и `started_at` — часов назад, `None` — пусто."""
			return {
				"lesson": урок,
				"last_activity_at": None if часов_назад is None else сейчас - timedelta(hours=часов_назад),
				"started_at": None
				if начато_часов_назад is None
				else сейчас - timedelta(hours=начато_часов_назад),
			}

		случаи = {
			"прочих нет": ([], True, ("first_in_course", None, 0)),
			"этот урок уже проходили": ([занятие("L1")], False, ("repeat", 48, 0)),
			"повтор важнее мостика": ([занятие("L0"), занятие("L1")], True, ("repeat", 48, 0)),
			"незакрытое и перерыв прошёл": ([занятие()], True, ("return", 48, 1)),
			"перерыв прошёл, незакрытого нет": ([занятие()], False, ("new_lesson", 48, 0)),
			"незакрытое, перерыв не прошёл": ([занятие(часов_назад=2)], True, ("new_lesson", 2, 1)),
			"ровно на перерыве — не мостик": ([занятие(часов_назад=24)], True, ("new_lesson", 24, 1)),
			"последнее — самое позднее из занятий": (
				[занятие(часов_назад=48), занятие(часов_назад=3)],
				True,
				("new_lesson", 3, 1),
			),
			"без last_activity_at — по started_at": (
				[занятие(часов_назад=None, начато_часов_назад=30)],
				True,
				("return", 30, 1),
			),
			"last_activity_at важнее started_at": (
				[занятие(часов_назад=2, начато_часов_назад=30)],
				True,
				("new_lesson", 2, 1),
			),
			"без времени — не мостик": ([занятие(часов_назад=None)], True, ("new_lesson", None, 0)),
		}
		for что, (прочие, незакрыто, (opening, часов_назад, запросов)) in случаи.items():
			with self.subTest(что):
				запрошен = []

				def перерыв() -> timedelta:
					запрошен.append(1)
					return timedelta(hours=24)

				начало = student.начало_занятия(прочие, "L1", незакрыто, сейчас, перерыв)

				последнее = (
					None if часов_назад is None else (сейчас - timedelta(hours=часов_назад)).isoformat()
				)
				self.assertEqual(
					(начало, len(запрошен)), ({"opening": opening, "last_session_at": последнее}, запросов)
				)
