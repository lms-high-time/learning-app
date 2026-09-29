# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Лёгкий старт и контекст по запросу (learning-services#410)."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import (
	зачислить,
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

	def _старт(self, **параметры) -> dict:
		ответ = student.start_lesson(lesson=self.урок, **параметры)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def _события(self, занятие: str) -> list[str]:
		return frappe.get_all(
			"Agent Session Event", filters={"session": занятие}, pluck="kind", ignore_permissions=True
		)

	def test_лёгкий_старт_без_материала_указаний_и_контекста(self):
		данные = self._старт(brief=True)

		self.assertNotIn("markdown", данные["content"])
		self.assertEqual(данные["content"]["total_segments"], 1)
		for поле in ("directive", "course_directive", "student_context", "artifact_blocks", "media"):
			self.assertNotIn(поле, данные)
		self.assertEqual(данные["objectives"], ["Понимать цикл", "Уметь читать код"])
		self.assertEqual(данные["start"]["opening"], "first_in_course")
		self.assertEqual(
			данные["context"], {"notes": 0, "carried_over": 0, "recent_work": 0, "closed_reports": 0}
		)
		self.assertNotIn("Directive Issued", self._события(данные["session"]), "указания не выданы")

	def test_без_brief_прежний_полный_ответ(self):
		данные = self._старт()

		self.assertIn("Цикл повторяет действие", данные["content"]["markdown"])
		self.assertEqual(данные["directive"]["audience"], "teacher_only")
		self.assertIn("student_context", данные)
		self.assertNotIn("context", данные)

	def test_материал_указания_и_контекст_по_занятию(self):
		занятие = self._старт(brief="true")["session"]

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
		занятие = self._старт(brief=True)["session"]

		первая = student.mark_objective(занятие, 1, "touched", "с примера")["data"]
		self.assertEqual(
			первая["warnings"], ["teaching_notes_not_taken", "lesson_material_not_taken"]
		)

		student.teaching_notes(занятие)
		student.lesson_material(занятие)
		вторая = student.mark_objective(занятие, 2, "touched", "с чтения")["data"]
		self.assertEqual(вторая["warnings"], [])

	def test_полный_старт_не_предупреждает(self):
		занятие = self._старт()["session"]

		ответ = student.mark_objective(занятие, 1, "touched", "с примера")["data"]

		self.assertEqual(ответ["warnings"], [])

	def test_чужое_занятие_не_читается(self):
		занятие = self._старт(brief=True)["session"]
		frappe.set_user("Administrator")
		чужой = создать_ученика(f"brief-other-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(чужой)

		for метод in (student.lesson_material, student.teaching_notes, student.student_context):
			ответ = метод(занятие)
			self.assertFalse(ответ["ok"], метод.__name__)
			self.assertEqual(ответ["error"]["code"], student.ЧУЖОЕ_ЗАНЯТИЕ)
