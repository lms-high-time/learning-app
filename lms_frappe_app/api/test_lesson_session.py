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
	создать_урок,
	урок_релиза,
)
from lms_frappe_app.api import student

СОСТОЯНИЕ = json.dumps({"id": "conv-1", "messages": []})


class IntegrationTestLessonSession(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"lesson-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
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

	def test_без_занятий_отдаётся_пусто(self):
		данные = student.lesson_session(self.урок)["data"]

		self.assertEqual(данные["session"], None)
		self.assertEqual(данные["status"], None)
		self.assertFalse(данные["has_chat_state"])
		self.assertFalse(данные["completed"])

	def test_незакрытое_занятие_отдаётся_как_идущее(self):
		занятие = создать_занятие(self.ученик, self.урок)

		данные = student.lesson_session(self.урок)["data"]

		self.assertEqual(данные["session"], занятие)
		self.assertEqual(данные["status"], "In Progress")
		self.assertFalse(данные["completed"])

	def test_закрытое_занятие_с_разговором_видно_целиком(self):
		"""Ради этого метод и заводится: ученик вернулся к пройденному уроку,
		и сервису нужно найти разговор, а не начинать урок заново."""
		занятие = создать_занятие(self.ученик, self.урок)
		student.save_chat_state(занятие, СОСТОЯНИЕ, "1")
		запись = frappe.get_doc("Agent Learning Session", занятие)
		quiz.отметить_урок_пройденным(запись)
		запись.status = "Completed"
		запись.save(ignore_permissions=True)

		данные = student.lesson_session(self.урок)["data"]

		self.assertEqual(данные["session"], занятие)
		self.assertEqual(данные["status"], "Completed")
		self.assertTrue(данные["has_chat_state"])
		self.assertTrue(данные["completed"])

	def test_отдаётся_последнее_занятие_урока(self):
		первое = создать_занятие(self.ученик, self.урок)
		frappe.db.set_value("Agent Learning Session", первое, "status", "Abandoned")
		последнее = создать_занятие(self.ученик, self.урок)

		данные = student.lesson_session(self.урок)["data"]

		self.assertEqual(данные["session"], последнее)

	def test_чужое_занятие_не_видно(self):
		чужое = создать_занятие(self.чужой, self.урок)

		данные = student.lesson_session(self.урок)["data"]

		self.assertNotEqual(данные["session"], чужое)
		self.assertEqual(данные["session"], None)

	def test_метод_ничего_не_заводит_и_не_пишет_в_журнал(self):
		занятие = создать_занятие(self.ученик, self.урок)
		событий_до = self.событий(занятие)
		занятий_до = self.занятий()

		student.lesson_session(self.урок)
		student.lesson_session(self.урок)

		self.assertEqual(self.событий(занятие), событий_до)
		self.assertEqual(self.занятий(), занятий_до)


class IntegrationTestStartState(IntegrationTestCase):
	"""С чего начинать занятие — решает сервер (#238).

	Признаки — первое ли занятие по курсу, повтор ли урока, есть ли
	незакрытое, сколько прошло. Разбирать их заново на каждом старте — место
	для ошибки агента, поэтому сервер сводит их в одно `opening`. След
	прошлого занятия — отметки пунктов прохождения с этим занятием или
	закрытый урок (learning-services#506).
	"""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(политика_по_умолчанию)
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"start-{суффикс}@example.com")
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

	def test_первое_занятие_по_курсу(self):
		данные = self.начало(self.урок)

		self.assertEqual(данные["start"]["opening"], "first_in_course")
		self.assertIsNone(данные["start"]["last_session_at"])
		self.assertEqual(данные["course_promise"], "К концу курса вы умеете пример.")
		self.assertEqual(данные["lesson"]["hook"], "Зачин урока «Урок первый»")

	def test_пустые_зачин_и_обещание_отдаются_как_none(self):
		frappe.db.set_value("LMS Course", self.курс, "course_promise", "")
		frappe.db.set_value("Agent Release Lesson", {"parent": self.релиз, "lesson_key": "l-1"}, "hook", "")

		данные = self.начало(self.урок)

		self.assertIsNone(данные["course_promise"])
		self.assertIsNone(данные["lesson"]["hook"])

	def test_новый_урок_после_недавнего_занятия(self):
		self.прошлое("l-1", часов_назад=1, незакрыто=True)
		self.assertEqual(self.начало(self.второй)["start"]["opening"], "new_lesson")

	def test_возвращение_после_перерыва_с_незакрытым(self):
		self.прошлое("l-1", часов_назад=48, незакрыто=True)
		данные = self.начало(self.второй)
		self.assertEqual(данные["start"]["opening"], "return")
		self.assertEqual(
			[(у["key"], [ц["key"] for ц in у["objectives_open"]]) for у in данные["history"]["lessons"]],
			[("l-1", ["l-1-D1"])],
		)

	def test_перерыв_без_незакрытого_это_не_возвращение(self):
		"""Мостик срабатывает на «есть незакрытое и прошло больше N», а не на
		время само по себе: без незакрытого строить его не из чего."""
		self.прошлое("l-1", часов_назад=48, незакрыто=False)
		self.assertEqual(self.начало(self.второй)["start"]["opening"], "new_lesson")

	def test_перерыв_читается_из_настроек(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Agent Learning Settings", "bridge_after_hours", 1)
		frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")
		frappe.set_user(self.ученик)
		self.прошлое("l-1", часов_назад=2, незакрыто=True)
		self.assertEqual(self.начало(self.второй)["start"]["opening"], "return")

	def test_повтор_урока(self):
		self.прошлое("l-1", часов_назад=48, незакрыто=True)
		self.assertEqual(self.начало(self.урок)["start"]["opening"], "repeat")

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
