# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Даты на языке читателя — без базы (learning-services#767)."""

import datetime

import frappe
from frappe.tests import UnitTestCase

from lms_frappe_app import languages

МОМЕНТ = datetime.datetime(2026, 10, 29, 14, 5, 33)


class UnitTestLanguages(UnitTestCase):
	def test_даты_по_языку(self):
		"""Русская запись — прежняя, цифрами; не русский язык — английская."""
		случаи = {
			"ru": ("29.10.2026", "29.10.2026 14:05"),
			"ru-RU": ("29.10.2026", "29.10.2026 14:05"),
			"en": ("Oct 29, 2026", "Oct 29, 2026, 2:05 PM"),
			"de": ("Oct 29, 2026", "Oct 29, 2026, 2:05 PM"),
		}
		for язык, (дата, дата_время) in случаи.items():
			with self.subTest(язык):
				self.assertEqual(languages.дата(МОМЕНТ, язык), дата)
				# Между временем и «PM» CLDR ставит узкий неразрывный пробел.
				self.assertEqual(languages.дата_время(МОМЕНТ, язык).replace(" ", " "), дата_время)

	def test_без_языка_язык_запроса_и_пусто_без_значения(self):
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.local.lang = "en"
		self.assertEqual(languages.дата("2026-10-29"), "Oct 29, 2026")
		frappe.local.lang = "ru"
		self.assertEqual(languages.дата_время("2026-10-29 14:05:33"), "29.10.2026 14:05")
		self.assertEqual((languages.дата(None), languages.дата_время("")), ("", ""))
