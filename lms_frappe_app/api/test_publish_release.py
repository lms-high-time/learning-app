# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Метод `publish_release`: граница прав и форма ответа (learning-services#500).

Поведение публикации проверяет `agent_learning/releases/test_service.py`;
здесь — что оно дошло до метода: кто может звать, что релиз принимается и
объектом, и строкой JSON, и что отказ едет кодом контракта.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import authoring
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import создать_куратора, создать_ученика


class IntegrationTestPublishRelease(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"rel-api-{суффикс}@example.com")
		self.ученик = создать_ученика(f"rel-api-pupil-{суффикс}@example.com")
		self.ключ = f"api-{суффикс}"

	def test_ученику_нельзя(self):
		frappe.set_user(self.ученик)
		with self.assertRaises(frappe.PermissionError):
			authoring.publish_release(release=пример_релиза(self.ключ))
		self.assertFalse(frappe.db.exists("LMS Course", {"course_key": self.ключ}))

	def test_куратор_публикует_объектом(self):
		frappe.set_user(self.куратор)
		ответ = authoring.publish_release(release=пример_релиза(self.ключ))

		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(ответ["data"]["version"], 1)
		self.assertTrue(ответ["data"]["course_created"])

	def test_строка_json_то_же_что_объект(self):
		frappe.set_user(self.куратор)
		authoring.publish_release(release=пример_релиза(self.ключ))

		ответ = authoring.publish_release(release=json.dumps(пример_релиза(self.ключ), ensure_ascii=False))

		self.assertTrue(ответ["data"]["unchanged"], ответ)

	def test_отказ_кодом_контракта(self):
		frappe.set_user(self.куратор)
		ответ = authoring.publish_release(release={"format": "lms-release/2"})

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], "release_format_unsupported")

	def test_пустой_course_ищет_по_ключу(self):
		frappe.set_user(self.куратор)
		первый = authoring.publish_release(release=пример_релиза(self.ключ))["data"]

		ответ = authoring.publish_release(release=пример_релиза(self.ключ), course="")

		self.assertEqual(ответ["data"]["course"], первый["course"])

	def test_только_post(self):
		self.assertEqual(
			set(frappe.allowed_http_methods_for_whitelisted_func[authoring.publish_release]), {"POST"}
		)
