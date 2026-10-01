# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Шапка страниц платформы на теме сайта.

`Why:` логотип этих страниц вёл «Главной» на маркетинговый сайт, а пунктов не
было вовсе: из документов не было дороги ни к курсам, ни к занятию
(lms-platform#311).
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.site_navigation import КАБИНЕТ_АВТОРА, ПУНКТЫ, шапка_платформы
from lms_frappe_app.tests.sample_data import создать_куратора, создать_ученика


class IntegrationTestSiteNavigation(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"nav-{суффикс}@example.com")
		self.куратор = создать_куратора(f"nav-cur-{суффикс}@example.com")

	def шапка(self, пользователь: str) -> frappe._dict:
		frappe.set_user(пользователь)
		context = frappe._dict()
		шапка_платформы(context)
		return context

	def test_логотип_ведёт_в_learning_а_не_на_сайт(self):
		self.assertEqual(self.шапка(self.ученик).home_page, "/lms")

	def test_ученик_видит_курсы_занятие_агента_и_документы(self):
		пункты = [(п.label, п.url) for п in self.шапка(self.ученик).top_bar_items]

		self.assertEqual(пункты, list(ПУНКТЫ))

	def test_подвал_ведёт_к_исходникам_а_не_на_frappe(self):
		подвал = self.шапка(self.ученик).footer_powered

		self.assertIn("github.com/lms-high-time/learning-app", подвал)

	def test_кабинет_автора_только_автору(self):
		у_ученика = [п.url for п in self.шапка(self.ученик).top_bar_items]
		у_куратора = [п.url for п in self.шапка(self.куратор).top_bar_items]

		self.assertNotIn(КАБИНЕТ_АВТОРА[1], у_ученика)
		self.assertIn(КАБИНЕТ_АВТОРА[1], у_куратора)

	def test_страницы_платформы_получают_шапку(self):
		# «Мои документы» и «Подключить ассистента» живут в SPA
		# (learning-services#331, #470): шапка темы им не нужна.
		from lms_frappe_app.www import author

		frappe.set_user(self.ученик)
		context = frappe._dict()
		author.get_context(context)
		self.assertEqual(context.home_page, "/lms")
		self.assertTrue(context.top_bar_items)
