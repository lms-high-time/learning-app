# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Материал, указания и контекст по занятию — методы лёгкого старта (learning-services#410).

Сам лёгкий старт ушёл из `start_lesson` (learning-services#506); методы
живут до удаления старого пути и проверяются на занятии курса старой модели.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import (
	зачислить,
	создать_занятие,
	создать_ученика,
	создать_урок,
)
from lms_frappe_app.api import student


class IntegrationTestBriefStart(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"brief-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		frappe.db.set_value(
			"Course Lesson", self.урок, "body", "## Циклы\n\nЦикл повторяет действие."
		)
		self.курс = зачислить(self.ученик, self.урок)
		frappe.get_doc(
			{
				"doctype": "Agent Lesson Directive",
				"lesson": self.урок,
				"objectives": "Понимать цикл\nУметь читать код",
				"teaching_directive": "Начать с примера, не с определения",
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)

	def _занятие(self, brief: bool = False) -> str:
		"""Занятие курса старой модели — `start_lesson` таким курсам отказывает
		(learning-services#506); `brief_start` ставится так, как его ставил
		лёгкий старт."""
		занятие = создать_занятие(self.ученик, self.урок)
		frappe.db.set_value("Agent Learning Session", занятие, "brief_start", int(brief))
		return занятие

	def _события(self, занятие: str) -> list[str]:
		return frappe.get_all(
			"Agent Session Event", filters={"session": занятие}, pluck="kind", ignore_permissions=True
		)

	def test_материал_указания_и_контекст_по_занятию(self):
		занятие = self._занятие(brief=True)

		материал = student.lesson_material(занятие)["data"]
		self.assertIn("Цикл повторяет действие", материал["content"]["markdown"])
		self.assertEqual(student.lesson_material(занятие, segment=9)["data"]["content"]["segment_index"], 1)

		указания = student.teaching_notes(занятие)["data"]
		self.assertEqual(указания["directive"]["audience"], "teacher_only")
		self.assertIn("Начать с примера", указания["directive"]["teaching_directive"])

		контекст = student.student_context(занятие)["data"]
		for поле in ("facts", "observations", "project", "projects_elsewhere", "carried_over", "recent_work", "closed_reports"):
			self.assertIn(поле, контекст)

		self.assertEqual(
			sorted(set(self._события(занятие)) & {"Directive Issued", "Material Issued"}),
			["Directive Issued", "Material Issued"],
		)

	def test_отметка_предупреждает_о_невзятых_указаниях_и_материале(self):
		занятие = self._занятие(brief=True)

		первая = student.mark_objective(занятие, 1, "touched", "с примера")["data"]
		self.assertEqual(
			первая["warnings"], ["teaching_notes_not_taken", "lesson_material_not_taken"]
		)

		student.teaching_notes(занятие)
		student.lesson_material(занятие)
		вторая = student.mark_objective(занятие, 2, "touched", "с чтения")["data"]
		self.assertEqual(вторая["warnings"], [])

	def test_без_лёгкого_старта_не_предупреждает(self):
		занятие = self._занятие()

		ответ = student.mark_objective(занятие, 1, "touched", "с примера")["data"]

		self.assertEqual(ответ["warnings"], [])

	def test_чужое_занятие_не_читается(self):
		занятие = self._занятие(brief=True)
		frappe.set_user("Administrator")
		чужой = создать_ученика(f"brief-other-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(чужой)

		for метод in (student.lesson_material, student.teaching_notes, student.student_context):
			ответ = метод(занятие)
			self.assertFalse(ответ["ok"], метод.__name__)
			self.assertEqual(ответ["error"]["code"], student.ЧУЖОЕ_ЗАНЯТИЕ)
