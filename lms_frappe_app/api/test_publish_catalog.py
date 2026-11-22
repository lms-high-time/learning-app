# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Методы каталога компетенций: граница прав и проводка (learning-services#825).

Поведение публикации проверяет `agent_learning/catalog/test_service.py`;
здесь — кто может звать `publish_competency_catalog` и `competency_catalog`,
что каталог доходит до сервиса строкой JSON, что отказ едет кодом контракта.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import authoring
from lms_frappe_app.tests.catalog_sample import поверх_опубликованного, пример_каталога
from lms_frappe_app.tests.sample_data import создать_пользователя, создать_ученика


class IntegrationTestМетодыКаталога(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.суффикс = frappe.generate_hash(length=6)

	def test_ученику_нельзя(self):
		frappe.set_user(создать_ученика(f"cat-pupil-{self.суффикс}@example.com"))
		for метод, вызов in (
			(
				"publish_competency_catalog",
				lambda: authoring.publish_competency_catalog(catalog=пример_каталога(self.суффикс)),
			),
			("competency_catalog", authoring.competency_catalog),
		):
			with self.subTest(метод):
				with self.assertRaises(frappe.PermissionError):
					вызов()
		self.assertFalse(frappe.db.exists("Agent Competency", f"base-a-{self.суффикс}"))

	def test_публикатор_публикует_и_читает(self):
		frappe.set_user(
			создать_пользователя(f"cat-bot-{self.суффикс}@example.com", authoring.РОЛЬ_ПУБЛИКАТОРА)
		)
		каталог = поверх_опубликованного(пример_каталога(self.суффикс))

		ответ = authoring.publish_competency_catalog(
			catalog=json.dumps(каталог, ensure_ascii=False), commit="cd" * 20
		)

		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(ответ["data"]["commit"], "cd" * 20)
		self.assertEqual(authoring.competency_catalog()["data"]["catalog"], каталог)
		with self.subTest("отказ — кодом контракта"):
			неверный = authoring.publish_competency_catalog(catalog={**каталог, "format": "x"})
			self.assertEqual(неверный["error"]["code"], "catalog_format_unsupported", неверный)
