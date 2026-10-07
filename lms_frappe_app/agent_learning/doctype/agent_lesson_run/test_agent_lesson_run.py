# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_куратора,
	создать_курс,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
)

DOCTYPE = "Agent Lesson Run"


class IntegrationTestAgentLessonRun(IntegrationTestCase):
	"""Прохождение урока: одно на ученика и урок, видит его только платформа (learning-services#504)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.курс = создать_курс(f"Курс {суффикс}")
		self.ученик = создать_ученика(f"run-pupil-{суффикс}@example.com")
		self.организация = создать_организацию(f"Прохождения {суффикс}")
		добавить_в_организацию(self.ученик, self.организация)
		self.руководитель = создать_менеджера(f"run-mgr-{суффикс}@example.com", self.организация)

	def прохождение(self, ключ: str = "l-1"):
		return frappe.get_doc(
			{"doctype": DOCTYPE, "student": self.ученик, "course": self.курс, "lesson_key": ключ}
		).insert(ignore_permissions=True)

	def test_одно_прохождение_на_ученика_и_урок(self):
		self.прохождение()
		self.прохождение("l-2")
		with self.assertRaises((frappe.UniqueValidationError, frappe.DuplicateEntryError)):
			self.прохождение()

	def test_ученик_и_руководитель_не_читают_прохождение(self):
		"""Пункты — инструмент агента: ни своё прохождение, ни прохождение сотрудника."""
		запись = self.прохождение()
		автор = создать_куратора(f"run-author-{frappe.generate_hash(length=6)}@example.com")
		for кто in (self.ученик, self.руководитель, автор):
			frappe.set_user(кто)
			self.assertFalse(frappe.has_permission(DOCTYPE, "read", doc=запись.name), кто)
			with self.assertRaises(frappe.PermissionError, msg=кто):
				frappe.get_doc(DOCTYPE, запись.name).check_permission("read")
			with self.assertRaises(frappe.PermissionError, msg=кто):
				frappe.get_list(DOCTYPE)
			with self.assertRaises(frappe.PermissionError, msg=кто):
				frappe.get_list("Agent Lesson Run Goal", parent_doctype=DOCTYPE, fields=["evidence"])

	def test_модератор_читает_но_не_правит(self):
		модератор = создать_куратора(
			f"run-moder-{frappe.generate_hash(length=6)}@example.com", роль="Moderator"
		)
		запись = self.прохождение()
		self.assertTrue(frappe.has_permission(DOCTYPE, "read", doc=запись.name, user=модератор))
		self.assertFalse(frappe.has_permission(DOCTYPE, "write", doc=запись.name, user=модератор))
