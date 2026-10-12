# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Язык платформы на новом сайте.

`Why:` интерфейс LMS своего переключателя не показывает — язык он спрашивает
у сервера, а настройка живёт в базе и не переживает пересоздания сайта.
Ставится при установке, а не хуком миграции: на действующем сайте язык —
выбор администратора (#540).
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.translate import get_all_languages

from lms_frappe_app.install import ЯЗЫК, обеспечить_русский_язык


class IntegrationTestLanguage(IntegrationTestCase):
	def test_установка_ставит_русский_и_включает_его(self):
		"""Так Frappe оставляет язык после своей установки: `en`, `ru` выключен."""
		прежний = frappe.db.get_single_value("System Settings", "language")
		self.addCleanup(frappe.clear_cache)
		self.addCleanup(frappe.db.set_single_value, "System Settings", "language", прежний or "")
		frappe.db.set_single_value("System Settings", "language", "en")
		frappe.db.set_value("Language", ЯЗЫК, "enabled", 0)

		обеспечить_русский_язык()

		self.assertEqual(frappe.db.get_single_value("System Settings", "language"), ЯЗЫК)
		self.assertTrue(frappe.db.get_value("Language", ЯЗЫК, "enabled"))
		self.assertIn(ЯЗЫК, get_all_languages())
