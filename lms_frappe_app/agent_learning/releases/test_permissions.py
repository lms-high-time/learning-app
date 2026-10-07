# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Права на релиз курса и его индекс (learning-services#500)."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import создать_куратора, создать_курс, создать_ученика


class IntegrationTestПраваНаРелиз(IntegrationTestCase):
	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"rel-pupil-{суффикс}@example.com")
		self.куратор = создать_куратора(f"rel-curator-{суффикс}@example.com")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_ученик_не_читает_релиз_и_индекс(self):
		"""В индексе ответы квиза — ученику нельзя ни записи, ни строк (CLAUDE.md §10)."""
		self.assertFalse(frappe.has_permission("Agent Course Release", "read", user=self.ученик))
		frappe.set_user(self.ученик)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("Agent Course Release")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list(
				"Agent Release Question",
				parent_doctype="Agent Course Release",
				fields=["correct"],
			)

	def test_куратор_читает_и_создаёт_релиз(self):
		for право in ("read", "create"):
			self.assertTrue(frappe.has_permission("Agent Course Release", право, user=self.куратор), право)

	def test_релиз_не_правит_никто(self):
		админ = создать_куратора(
			f"rel-admin-{frappe.generate_hash(length=6)}@example.com", роль="System Manager"
		)
		for кто in (self.куратор, админ):
			self.assertFalse(frappe.has_permission("Agent Course Release", "write", user=кто), кто)

	def test_курсы_без_ключа_не_мешают_друг_другу(self):
		"""`unique` у `course_key`: пустое значение — NULL, не дубль."""
		for _ in range(2):
			создать_курс(f"Без ключа {frappe.generate_hash(length=6)}")
