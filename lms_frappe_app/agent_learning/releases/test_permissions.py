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
	"""Пользователи ролей тесты только читают — они общие на класс."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.addClassCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		cls.ученик = создать_ученика(f"rel-pupil-{суффикс}@example.com")
		cls.куратор = создать_куратора(f"rel-curator-{суффикс}@example.com")
		cls.модератор = создать_куратора(f"rel-moder-{суффикс}@example.com", роль="Moderator")
		cls.админ = создать_куратора(f"rel-admin-{суффикс}@example.com", роль="System Manager")
		организация = создать_организацию(f"Релиз {суффикс}")
		cls.руководитель = создать_менеджера(f"rel-mgr-{суффикс}@example.com", организация)

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

	def test_права_ролей_на_релиз(self):
		"""Куратор и модератор читают релиз. Создаёт его только сервис публикации:
		права `create` нет ни у кого; правит и удаляет — никто. Руководитель не читает."""
		for кто, право, можно in (
			("куратор", "read", True),
			("куратор", "create", False),
			("куратор", "write", False),
			("куратор", "delete", False),
			("модератор", "read", True),
			("модератор", "create", False),
			("админ", "write", False),
			("админ", "delete", False),
			("руководитель", "read", False),
		):
			with self.subTest(f"{кто}: {право}"):
				self.assertEqual(
					bool(frappe.has_permission("Agent Course Release", право, user=getattr(self, кто))), можно
				)
