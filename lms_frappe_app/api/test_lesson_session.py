# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Занятие по уроку для веб-чата (lms-platform#151).

Метод только читает: по нему сервис узнаёт, идёт ли по уроку разговор и
закрыт ли урок, — и решает, показать историю или начинать занятие. Поэтому он
не заводит занятий и не пишет событий в журнал: иначе каждый возврат на
страницу оставлял бы след, которого не было.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

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
		return frappe.db.count(
			"Agent Learning Session", {"student": self.ученик, "lesson": self.урок}
		)

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
		"""Мостик срабатывает на «есть незакрытое и прошло больше N», а не на
		время само по себе: без незакрытого строить его не из чего. Перерыв
		читается из настроек. История каждого случая — у своего ученика."""
		случаи = {
			"возвращение после перерыва с незакрытым": (48, True, None, "return"),
			"перерыв без незакрытого — не возвращение": (48, False, None, "new_lesson"),
			"перерыв из настроек": (2, True, 1, "return"),
		}
		for что, (часов_назад, незакрыто, перерыв, opening) in случаи.items():
			with self.subTest(что):
				self.новый_ученик()
				if перерыв is not None:
					frappe.set_user("Administrator")
					frappe.db.set_single_value("Agent Learning Settings", "bridge_after_hours", перерыв)
					frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")
					frappe.set_user(self.ученик)
				self.прошлое("l-1", часов_назад=часов_назад, незакрыто=незакрыто)

				данные = self.начало(self.второй)

				self.assertEqual(данные["start"]["opening"], opening)
				if что == "возвращение после перерыва с незакрытым":
					self.assertEqual(
						[
							(у["key"], [ц["key"] for ц in у["objectives_open"]])
							for у in данные["history"]["lessons"]
						],
						[("l-1", ["l-1-D1"])],
					)

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
