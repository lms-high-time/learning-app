# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Идентификатор для аналитики — без email и имени (learning-services#582)."""

import hashlib
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import analytics
from lms_frappe_app.tests.sample_data import создать_ученика


class IntegrationTestAnalyticsId(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.первый = создать_ученика(f"aid-a-{суффикс}@example.com")
		self.второй = создать_ученика(f"aid-b-{суффикс}@example.com")

	def от_имени(self, кто: str) -> str:
		frappe.set_user(кто)
		try:
			ответ = analytics.analytics_id()
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]["id"]

	def test_один_человек_один_идентификатор_без_адреса(self):
		первый = self.от_имени(self.первый)

		self.assertEqual(первый, self.от_имени(self.первый), "стабилен между вызовами")
		self.assertNotEqual(первый, self.от_имени(self.второй), "у разных людей разный")
		self.assertRegex(первый, r"^[0-9a-f]{32}$")
		# Без секрета сайта не подобрать: голый хеш адреса — не он.
		self.assertFalse(hashlib.sha256(self.первый.encode()).hexdigest().startswith(первый))

	def test_регистр_не_важен_а_ключ_важен(self):
		self.assertEqual(analytics.идентификатор("A@Example.com"), analytics.идентификатор("a@example.com"))
		прежний = analytics.идентификатор("a@example.com")
		with patch.object(analytics, "get_encryption_key", return_value="другой-секрет"):
			self.assertNotEqual(analytics.идентификатор("a@example.com"), прежний)

	def test_гостю_нет(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.AuthenticationError):
				analytics.analytics_id()
		finally:
			frappe.set_user("Administrator")
