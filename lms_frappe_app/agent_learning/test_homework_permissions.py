# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import homework as домашка
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	создать_домашку,
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
	создать_урок,
)


class IntegrationTestHomeworkPermissions(IntegrationTestCase):
	"""Кто видит сдачу (learning-services#439, дизайн «Права»)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		с = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"hwp-{с}@example.com")
		self.урок = создать_урок(f"Урок {с}")
		зачислить(self.ученик, self.урок)
		создать_домашку(self.урок)
		self.организация = создать_организацию(f"Орг {с}")
		добавить_в_организацию(self.ученик, self.организация)
		чужая = создать_организацию(f"Чужая {с}")
		добавить_в_организацию(self.ученик, чужая)
		self.чужая_организация = чужая
		self.менеджер = создать_менеджера(f"hwm-{с}@example.com", self.организация)
		self.методист = создать_куратора(f"hwc-{с}@example.com", роль="Course Creator")
		self.модератор = создать_куратора(f"hwmod-{с}@example.com", роль="Moderator")
		self.сосед = создать_ученика(f"hwn-{с}@example.com")
		self.личная = домашка.сохранить(self.ученик, self.урок, None, answer="лично")
		self.рабочая = домашка.сохранить(self.ученик, self.урок, self.организация, answer="в компании")
		self.чужая = домашка.сохранить(self.ученик, self.урок, чужая, answer="в другой компании")

	def видит(self, user):
		frappe.set_user(user)
		return set(frappe.get_list("Agent Homework Submission", pluck="name"))

	def test_ученик_видит_свои(self):
		self.assertEqual(
			self.видит(self.ученик), {self.личная.name, self.рабочая.name, self.чужая.name}
		)

	def test_чужой_ученик_не_видит_ничего(self):
		self.assertEqual(self.видит(self.сосед), set())
		self.assertFalse(frappe.has_permission("Agent Homework Submission", "read", doc=self.личная))

	def test_руководитель_видит_только_пространство_своей_организации(self):
		self.assertEqual(self.видит(self.менеджер), {self.рабочая.name})
		self.assertFalse(frappe.has_permission("Agent Homework Submission", "read", doc=self.личная))
		self.assertFalse(frappe.has_permission("Agent Homework Submission", "read", doc=self.чужая))
		self.assertTrue(frappe.has_permission("Agent Homework Submission", "read", doc=self.рабочая))

	def test_методист_видит_и_личное(self):
		for user in (self.методист, self.модератор):
			self.assertTrue(
				{self.личная.name, self.рабочая.name, self.чужая.name} <= self.видит(user), user
			)
			self.assertTrue(frappe.has_permission("Agent Homework Submission", "read", doc=self.личная))

	def test_запись_мимо_методов_запрещена(self):
		for user in (self.ученик, self.менеджер, self.методист, self.модератор):
			frappe.set_user(user)
			self.assertFalse(
				frappe.has_permission("Agent Homework Submission", "write", doc=self.рабочая), user
			)

	def test_файл_сдачи_видят_те_же_кто_сдачу(self):
		"""Права на `File` Frappe берёт у сдачи: файл открывают те, кто видит сдачу.

		Файл вставлен от администратора — доступ ученика идёт через сдачу, а не
		через владельца `File`.
		"""
		рабочая = домашка.сохранить(
			self.ученик, self.урок, self.организация, новые=[("отчёт.txt", frappe.generate_hash().encode())]
		)
		файл = frappe.get_doc("File", рабочая.files[0].file)
		руководитель_чужой = создать_менеджера(
			f"hwm2-{frappe.generate_hash(length=6)}@example.com", self.чужая_организация
		)
		for user, видит in (
			(self.сосед, False),
			(руководитель_чужой, False),
			(self.методист, True),
			(self.менеджер, True),
			(self.ученик, True),
		):
			frappe.set_user(user)
			self.assertEqual(bool(frappe.has_permission("File", "read", doc=файл)), видит, user)
