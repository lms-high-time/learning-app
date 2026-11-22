# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Каталог компетенций пишет только публикация каталога (learning-services#825)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.doctype.agent_competency.agent_competency import ИЗ_КАТАЛОГА


def _вставить(документ: dict):
	запись = frappe.get_doc(документ)
	запись.flags[ИЗ_КАТАЛОГА] = True
	return запись.insert(ignore_permissions=True)


class IntegrationTestКаталогТолькоВыпуском(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		суффикс = frappe.generate_hash(length=6)
		cls.выпуск = frappe.get_doc(
			{
				"doctype": "Agent Competency Catalog Release",
				# Номер вне диапазона настоящих выпусков: версия уникальна.
				"version": 900000 + int(суффикс, 16) % 99999,
				"catalog_format": "lms-competency-catalog/1",
				"digest": frappe.generate_hash(length=64),
				"published_by": "Administrator",
				"published_at": now_datetime(),
				"snapshot": "{}",
			}
		).insert(ignore_permissions=True)
		cls.область = _вставить(
			{"doctype": "Agent Competency Area", "area_key": f"area-{суффикс}", "title": "Область"}
		)
		cls.компетенция = _вставить(
			{
				"doctype": "Agent Competency",
				"competency_key": f"skill-{суффикс}",
				"title": "Умение",
				"description": "Что делает",
				"kind": "base",
				"area": cls.область.name,
				"version": 1,
				"version_release": cls.выпуск.name,
				**{f"level_{н}": f"Признак {н}" for н in range(1, 5)},
			}
		)

	def test_правка_и_удаление_мимо_выпуска_отклоняются(self):
		"""Даже администратором: ни правка компетенции, области и выпуска, ни удаление."""
		self.assertEqual(self.компетенция.name, self.компетенция.competency_key)
		self.assertEqual(self.выпуск.name, f"catalog-{self.выпуск.version}")
		for doctype, имя, поле in (
			("Agent Competency", self.компетенция.name, "title"),
			("Agent Competency Area", self.область.name, "title"),
			("Agent Competency Catalog Release", self.выпуск.name, "snapshot"),
		):
			with self.subTest(f"{doctype}: правка"):
				запись = frappe.get_doc(doctype, имя)
				прежнее = запись.get(поле)
				запись.set(поле, "подмена")
				with self.assertRaises(frappe.ValidationError):
					запись.save()
				self.assertEqual(frappe.db.get_value(doctype, имя, поле), прежнее)
			with self.subTest(f"{doctype}: удаление"):
				with self.assertRaises(frappe.ValidationError):
					frappe.delete_doc(doctype, имя)
				self.assertTrue(frappe.db.exists(doctype, имя))

		with self.subTest("новая компетенция мимо выпуска"):
			with self.assertRaises(frappe.ValidationError):
				frappe.get_doc(
					{
						"doctype": "Agent Competency",
						"competency_key": f"{self.компетенция.name}-x",
						"title": "Умение",
						"description": "Что делает",
						"kind": "base",
						"area": self.область.name,
						"version": 1,
						"version_release": self.выпуск.name,
					}
				).insert(ignore_permissions=True)
