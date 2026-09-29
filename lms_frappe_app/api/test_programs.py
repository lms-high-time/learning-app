# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Порядок курсов программы Learning — на сервере, а не только в интерфейсе (#405)."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.programs import ПОРЯДОК_ПРОГРАММЫ
from lms_frappe_app.api import public, student
from lms_frappe_app.tests.sample_data import создать_ученика, создать_урок


class IntegrationTestPrograms(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"program-{суффикс}@example.com")
		self.посторонний = создать_ученика(f"program-other-{суффикс}@example.com")
		self.урок_первого = создать_урок(f"Первый {суффикс}")
		self.урок_второго = создать_урок(f"Второй {суффикс}")
		self.первый = self._курс(self.урок_первого)
		self.второй = self._курс(self.урок_второго)
		self.программа = frappe.get_doc(
			{
				"doctype": "LMS Program",
				"title": f"Цепочка {суффикс}",
				"published": 1,
				"enforce_course_order": 1,
				"program_courses": [{"course": self.первый}, {"course": self.второй}],
				"program_members": [{"member": self.ученик}],
			}
		).insert(ignore_permissions=True)

	@staticmethod
	def _курс(урок: str) -> str:
		курс = frappe.db.get_value(
			"Course Chapter", frappe.db.get_value("Course Lesson", урок, "chapter"), "course"
		)
		frappe.db.set_value("LMS Course", курс, "published", 1)
		return курс

	def от_имени(self, кто: str, метод, **аргументы):
		frappe.set_user(кто)
		try:
			return метод(**аргументы)
		finally:
			frappe.set_user("Administrator")

	def _пройти(self, курс: str) -> None:
		frappe.db.set_value("LMS Enrollment", {"member": self.ученик, "course": курс}, "progress", 100)

	def test_участник_не_записывается_через_голову_предыдущего(self):
		ответ = self.от_имени(self.ученик, student.enroll, course=self.второй)

		self.assertEqual(ответ["error"]["code"], ПОРЯДОК_ПРОГРАММЫ)
		self.assertEqual(ответ["error"]["previous"]["id"], self.первый)
		self.assertEqual(ответ["error"]["program"], self.программа.name)
		self.assertTrue(self.от_имени(self.ученик, student.enroll, course=self.первый)["ok"])

	def test_пройденный_предыдущий_открывает_следующий(self):
		self.от_имени(self.ученик, student.enroll, course=self.первый)
		self._пройти(self.первый)

		self.assertTrue(self.от_имени(self.ученик, student.enroll, course=self.второй)["ok"])

	def test_не_участнику_порядок_не_мешает(self):
		# Решение владельца: порядок — только для участников программы.
		self.assertTrue(self.от_имени(self.посторонний, student.enroll, course=self.второй)["ok"])

	def test_без_обязательного_порядка_курс_открыт(self):
		frappe.db.set_value("LMS Program", self.программа.name, "enforce_course_order", 0)

		self.assertTrue(self.от_имени(self.ученик, student.enroll, course=self.второй)["ok"])

	def test_занятие_тоже_проверяет_порядок(self):
		# Запись могла появиться до вступления в программу.
		frappe.get_doc(
			{"doctype": "LMS Enrollment", "member": self.ученик, "course": self.второй}
		).insert(ignore_permissions=True)

		ответ = self.от_имени(self.ученик, student.start_lesson, lesson=self.урок_второго)

		self.assertEqual(ответ["error"]["code"], ПОРЯДОК_ПРОГРАММЫ)
		self.assertFalse(
			frappe.db.exists("Agent Learning Session", {"student": self.ученик, "lesson": self.урок_второго})
		)

	def test_страница_курса_знает_место_и_замок(self):
		участнику = self.от_имени(self.ученик, public.course_programs, course=self.второй)["data"]
		постороннему = self.от_имени(self.посторонний, public.course_programs, course=self.второй)["data"]

		[программа] = участнику["programs"]
		self.assertEqual((программа["number"], программа["total"]), (2, 2))
		self.assertTrue(программа["member"])
		self.assertEqual(программа["locked_by"]["id"], self.первый)
		self.assertEqual(программа["previous"]["id"], self.первый)
		[чужая] = постороннему["programs"]
		self.assertFalse(чужая["member"])
		self.assertIsNone(чужая["locked_by"])

	def test_неопубликованную_программу_видит_только_участник(self):
		frappe.db.set_value("LMS Program", self.программа.name, "published", 0)

		self.assertEqual(
			self.от_имени(self.посторонний, public.course_programs, course=self.второй)["data"]["programs"],
			[],
		)
		self.assertEqual(
			len(self.от_имени(self.ученик, public.course_programs, course=self.второй)["data"]["programs"]),
			1,
		)

	def test_страница_урока_ведёт_к_предыдущему_курсу(self):
		вход = self.от_имени(self.ученик, public.lesson_entry, lesson=self.урок_второго)["data"]

		self.assertEqual(вход["program_lock"]["previous"]["id"], self.первый)
		self.assertIsNone(
			self.от_имени(self.ученик, public.lesson_entry, lesson=self.урок_первого)["data"]["program_lock"]
		)
