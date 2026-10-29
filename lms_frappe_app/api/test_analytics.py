# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Идентификатор для аналитики — без email и имени (learning-services#582)."""

import hashlib
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.api import analytics
from lms_frappe_app.site_secrets import АНАЛИТИКА
from lms_frappe_app.tests.sample_data import создать_ученика

СЕКРЕТ = "0f" * 32


class IntegrationTestAnalyticsId(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		подмена = patch.dict(frappe.conf, {АНАЛИТИКА: СЕКРЕТ})
		подмена.start()
		self.addCleanup(подмена.stop)
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

	def test_гостю_нет(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.AuthenticationError):
				analytics.analytics_id()
		finally:
			frappe.set_user("Administrator")


class UnitTestAnalyticsSecret(UnitTestCase):
	"""Секрет идентификатора — свой ключ конфигурации сайта (learning-services#690)."""

	def test_идентификатор_зависит_только_от_своего_секрета(self):
		with patch.dict(frappe.conf, {АНАЛИТИКА: СЕКРЕТ}):
			прежний = analytics.идентификатор("a@example.com")
			self.assertEqual(analytics.идентификатор("A@Example.com "), прежний, "регистр и пробелы не важны")
			# Смена ключа шифрования сайта идентификатор не трогает.
			with patch.dict(frappe.conf, {"encryption_key": "смена-ключа-шифрования"}):
				self.assertEqual(analytics.идентификатор("a@example.com"), прежний)
		with patch.dict(frappe.conf, {АНАЛИТИКА: "1e" * 32}):
			self.assertNotEqual(analytics.идентификатор("a@example.com"), прежний)

	def test_без_секрета_идентификатора_нет(self):
		with patch.dict(frappe.conf):
			frappe.conf.pop(АНАЛИТИКА, None)
			with self.assertRaises(RuntimeError):
				analytics.идентификатор("a@example.com")
