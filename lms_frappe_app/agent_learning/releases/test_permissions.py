# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Права на релиз курса и его индекс (learning-services#500)."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import (
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
)


class IntegrationTestПраваНаРелиз(IntegrationTestCase):
	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"rel-pupil-{суффикс}@example.com")
		self.куратор = создать_куратора(f"rel-curator-{суффикс}@example.com")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_ученик_не_читает_релиз_и_индекс(self):
		"""В индексе ответы квиза — ученику нельзя ни записи, ни строк (CLAUDE.md §10).
		Срез урока и рамка пакета агента — там же: ученику их отдают только методы агента."""
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
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("Agent Course Release", fields=["agent_frame"])
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("Agent Release Lesson", parent_doctype="Agent Course Release", fields=["agent"])

	def test_куратор_читает_но_не_создаёт_релиз(self):
		"""Релиз создаёт только сервис публикации: права `create` нет ни у кого."""
		модератор = создать_куратора(
			f"rel-moder-{frappe.generate_hash(length=6)}@example.com", роль="Moderator"
		)
		for кто in (self.куратор, модератор):
			self.assertTrue(frappe.has_permission("Agent Course Release", "read", user=кто), кто)
			self.assertFalse(frappe.has_permission("Agent Course Release", "create", user=кто), кто)

	def test_руководитель_не_читает_релиз(self):
		организация = создать_организацию(f"Релиз {frappe.generate_hash(length=6)}")
		руководитель = создать_менеджера(f"rel-mgr-{frappe.generate_hash(length=6)}@example.com", организация)
		self.assertFalse(frappe.has_permission("Agent Course Release", "read", user=руководитель))

	def test_релиз_не_правит_никто(self):
		админ = создать_куратора(
			f"rel-admin-{frappe.generate_hash(length=6)}@example.com", роль="System Manager"
		)
		for кто in (self.куратор, админ):
			self.assertFalse(frappe.has_permission("Agent Course Release", "write", user=кто), кто)
			self.assertFalse(frappe.has_permission("Agent Course Release", "delete", user=кто), кто)
