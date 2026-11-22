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


def _выпуск(версия: int) -> dict:
	return {
		"doctype": "Agent Competency Catalog Release",
		# Номер вне диапазона настоящих выпусков: версия уникальна.
		"version": версия,
		"catalog_format": "lms-competency-catalog/1",
		"digest": frappe.generate_hash(length=64),
		"published_by": "Administrator",
		"published_at": now_datetime(),
		"snapshot": "{}",
	}


class IntegrationTestКаталогТолькоВыпуском(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		суффикс = frappe.generate_hash(length=6)
		cls.выпуск = _вставить(_выпуск(900000 + int(суффикс, 16) % 99999))
		cls.схема = _вставить(
			{
				"doctype": "Agent Competency Classification",
				"classification_key": f"scheme-{суффикс}",
				"title": "Схема",
				"source": "https://example.com/scheme",
				"classes": [{"class_key": f"class-{суффикс}", "title": "Класс"}],
			}
		)
		cls.компетенция = _вставить(
			{
				"doctype": "Agent Competency",
				"competency_key": f"skill-{суффикс}",
				"title": "Умение",
				"description": "Что делает",
				"kind": "base",
				"classes": [{"classification": cls.схема.name, "class_key": f"class-{суффикс}"}],
				"version": 1,
				"version_release": cls.выпуск.name,
				**{f"level_{н}": f"Признак {н}" for н in range(1, 5)},
			}
		)

	def test_правка_и_удаление_мимо_выпуска_отклоняются(self):
		"""Даже администратором: ни правка компетенции, схемы классификации и выпуска, ни
		удаление, ни вставка компетенции, схемы и выпуска мимо публикации."""
		self.assertEqual(self.компетенция.name, self.компетенция.competency_key)
		self.assertEqual(self.выпуск.name, f"catalog-{self.выпуск.version}")
		for doctype, имя, поле in (
			("Agent Competency", self.компетенция.name, "title"),
			("Agent Competency Classification", self.схема.name, "title"),
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
						"version": 1,
						"version_release": self.выпуск.name,
					}
				).insert(ignore_permissions=True)

		with self.subTest("класс схемы мимо выпуска"):
			схема = frappe.get_doc("Agent Competency Classification", self.схема.name)
			схема.append("classes", {"class_key": f"{схема.name}-x", "title": "Класс"})
			with self.assertRaises(frappe.ValidationError):
				схема.save()
			self.assertEqual(len(frappe.get_doc("Agent Competency Classification", схема.name).classes), 1)

		with self.subTest("новая схема мимо выпуска"):
			with self.assertRaises(frappe.ValidationError):
				frappe.get_doc(
					{
						"doctype": "Agent Competency Classification",
						"classification_key": f"{self.схема.name}-x",
						"title": "Схема",
						"source": "https://example.com/scheme",
					}
				).insert(ignore_permissions=True)

		with self.subTest("выпуск мимо публикации"):
			with self.assertRaises(frappe.ValidationError):
				frappe.get_doc(_выпуск(self.выпуск.version + 1)).insert(ignore_permissions=True)
