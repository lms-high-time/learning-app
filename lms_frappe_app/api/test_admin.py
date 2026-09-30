# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import admin, student
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	создать_вопрос,
	создать_квиз,
	создать_куратора,
	создать_организацию,
	создать_ученика,
	создать_урок,
	сдать_отчёт,
)


class IntegrationTestResetProgress(IntegrationTestCase):
	"""Сброс прогресса ученика по курсу из админки (learning-services#313)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"reset-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		вопрос = создать_вопрос("Два плюс два?", варианты=[("4", True), ("5", False)])
		создать_квиз(self.урок, [вопрос])
		self.курс = зачислить(self.ученик, self.урок)
		frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"course": self.курс,
				"slug": "summary",
				"title": "Резюме",
				"blocks": [{"block_key": "goal", "title": "Цель", "lesson": self.урок}],
			}
		).insert(ignore_permissions=True)

		# Ученик позанимался: занятие, попытка квиза, документ, заметки, репорт.
		frappe.set_user(self.ученик)
		self.занятие = student.start_lesson(lesson=self.урок)["data"]["session"]
		student.update_artifact(self.курс, "summary", "goal", "Открыть кофейню")
		student.remember("observation", "pace", "Любит примеры", session=self.занятие)
		student.remember("fact", "role", "Владелец кофейни")
		self.репорт = student.report_issue(self.занятие, "stuck", "Непонятен пример")["data"]["report"]
		сдать_отчёт(self.занятие)
		self.попытка = student.request_quiz(self.занятие)["data"]["attempt"]
		frappe.set_user("Administrator")

	def запись(self) -> str:
		return frappe.db.get_value("LMS Enrollment", {"member": self.ученик, "course": self.курс})

	def сбросить(self, кто: str = "Administrator") -> dict:
		frappe.set_user(кто)
		ответ = admin.reset_student_progress(self.запись())
		frappe.set_user("Administrator")
		return ответ

	def test_занятия_попытки_документ_и_заметки_уходят_в_архив(self):
		итог = self.сбросить()["data"]

		self.assertEqual(
			(итог["sessions_archived"], итог["attempts_archived"], итог["artifacts_archived"], итог["notes_archived"]),
			(1, 1, 1, 1),
		)
		занятие = frappe.db.get_value(
			"Agent Learning Session", self.занятие, ["student", "archived_student", "status"], as_dict=True
		)
		self.assertIsNone(занятие.student)
		self.assertEqual(занятие.archived_student, self.ученик)
		self.assertEqual(занятие.status, "Abandoned", "открытое занятие закрыто, а не брошено задачей")
		попытка = frappe.db.get_value(
			"Agent Quiz Attempt", self.попытка, ["student", "archived_student", "status"], as_dict=True
		)
		self.assertEqual((попытка.student, попытка.archived_student, попытка.status), (None, self.ученик, "Abandoned"))
		self.assertTrue(
			frappe.db.exists("Agent Student Note", {"student": self.ученик, "note_key": "role"}),
			"факт без курса — не прогресс по курсу",
		)

	def test_сдачи_домашки_уходят_в_архив(self):
		"""Повторное прохождение начинается с чистого листа (learning-services#439)."""
		from lms_frappe_app.agent_learning.test_homework import задание

		задание(self.урок)
		frappe.set_user(self.ученик)
		сдача = student.submit_homework(lesson=self.урок, answer="Сделал")["data"]["submission"]["id"]
		итог = self.сбросить()["data"]

		self.assertEqual(итог["homework_archived"], 1)
		запись = frappe.db.get_value(
			"Agent Homework Submission", сдача, ["member", "archived_student", "archived_at"], as_dict=True
		)
		self.assertIsNone(запись.member)
		self.assertEqual(запись.archived_student, self.ученик)
		self.assertIsNotNone(запись.archived_at)

		зачислить(self.ученик, self.урок)
		frappe.set_user(self.ученик)
		self.assertIsNone(student.homework(lesson=self.урок)["data"]["submission"])
		заново = student.submit_homework(lesson=self.урок, answer="Сделал заново")["data"]["submission"]
		self.assertNotEqual(заново["id"], сдача)
		self.assertEqual(заново["version"], 1)

	def test_запись_на_курс_снимается(self):
		self.сбросить()

		self.assertIsNone(self.запись())
		frappe.set_user(self.ученик)
		self.assertNotIn(self.курс, [к["id"] for к in student.list_my_courses()["data"]["courses"]])

	def test_после_записи_заново_занятие_первое_и_попытки_полные(self):
		frappe.set_user(self.ученик)
		до = student.start_lesson(lesson=self.урок)["data"]["quiz"]["attempts_left"]
		self.сбросить()
		зачислить(self.ученик, self.урок)

		frappe.set_user(self.ученик)
		урок = student.start_lesson(lesson=self.урок)["data"]

		self.assertNotEqual(урок["session"], self.занятие)
		self.assertEqual(урок["start"]["opening"], "first_in_course")
		self.assertEqual(урок["artifact_blocks"][0]["content"], "", "документ начинается пустым")
		self.assertEqual(урок["student_context"]["carried_over"], [])
		if до is not None:
			self.assertGreaterEqual(урок["quiz"]["attempts_left"], до)

	def test_ответы_на_репорты_ученик_видит_и_после_сброса(self):
		self.сбросить()

		frappe.set_user(self.ученик)
		репорты = student.my_reports()["data"]["reports"]

		self.assertIn(self.репорт, [р["id"] for р in репорты])

	def test_по_назначению_организации_запись_выдаётся_заново(self):
		организация = создать_организацию(f"Компания {frappe.generate_hash(length=6)}")
		добавить_в_организацию(self.ученик, организация)
		frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": организация,
				"course": self.курс,
				"mandatory": 1,
			}
		).insert(ignore_permissions=True)
		прежняя = self.запись()

		итог = self.сбросить()["data"]

		self.assertTrue(итог["enrolled_again"])
		self.assertIsNotNone(self.запись())
		self.assertNotEqual(self.запись(), прежняя, "запись новая, без прежнего прогресса")

	def test_след_сброса_в_карточке_ученика(self):
		self.сбросить()

		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{"reference_doctype": "User", "reference_name": self.ученик, "comment_type": "Info"},
			)
		)

	def test_ученик_и_чужой_куратор_сбросить_не_могут(self):
		куратор = создать_куратора(f"cc-{frappe.generate_hash(length=6)}@example.com")
		for кто in (self.ученик, куратор):
			with self.subTest(кто=кто), self.assertRaises(frappe.PermissionError):
				self.сбросить(кто)
		self.assertIsNotNone(self.запись(), "после отказа ничего не тронуто")

	def test_преподаватель_курса_сбросить_может(self):
		куратор = создать_куратора(f"in-{frappe.generate_hash(length=6)}@example.com")
		курс = frappe.get_doc("LMS Course", self.курс)
		курс.append("instructors", {"instructor": куратор})
		курс.save(ignore_permissions=True)

		self.assertTrue(self.сбросить(куратор)["ok"])

	def test_несуществующая_запись_отказ_кодом(self):
		ответ = admin.reset_student_progress("нет-такой")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], admin.ЗАПИСЬ_НЕ_НАЙДЕНА)
