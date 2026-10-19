# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Идентификатор для аналитики — без email и имени (learning-services#582)."""

import hashlib

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
		self.assertNotIn(self.первый.split("@")[0], первый)
		# Без секрета сайта не подобрать: голый хеш адреса — не он.
		self.assertFalse(hashlib.sha256(self.первый.encode()).hexdigest().startswith(первый))

	def test_гостю_нет(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.AuthenticationError):
				analytics.analytics_id()
		finally:
			frappe.set_user("Administrator")
