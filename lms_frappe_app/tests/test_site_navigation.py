# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Шапка страниц платформы на теме сайта.

`Why:` логотип этих страниц вёл «Главной» на маркетинговый сайт, а пунктов не
было вовсе: из документов не было дороги ни к курсам, ни к занятию
(lms-platform#311).
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.site_navigation import АГЕНТЫ_И_СЕССИИ, КАБИНЕТ_АВТОРА, ПУНКТЫ, шапка_платформы
from lms_frappe_app.tests.sample_data import создать_куратора, создать_ученика


class IntegrationTestSiteNavigation(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		"""Пользователей тесты класса только читают — они заводятся один раз."""
		super().setUpClass()
		суффикс = frappe.generate_hash(length=6)
		cls.ученик = создать_ученика(f"nav-{суффикс}@example.com")
		cls.куратор = создать_куратора(f"nav-cur-{суффикс}@example.com")

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")

	def шапка(self, пользователь: str) -> frappe._dict:
		frappe.set_user(пользователь)
		context = frappe._dict()
		шапка_платформы(context)
		return context

	def test_шапка_ученика(self):
		шапка = self.шапка(self.ученик)

		with self.subTest("курсы, занятие, агент и документы"):
			пункты = [(п.label, п.url) for п in шапка.top_bar_items]

			self.assertEqual(пункты, [(frappe._(подпись), адрес) for подпись, адрес in ПУНКТЫ])

		with self.subTest("подвал ведёт к исходникам, а не на Frappe"):
			self.assertIn("github.com/lms-high-time/learning-app", шапка.footer_powered)

		with self.subTest("меню пользователя: «Агенты и сессии» перед пунктами Frappe"):
			context = frappe._dict(post_login=[{"label": "My Account", "url": "/me"}])
			шапка_платформы(context)

			self.assertEqual(
				[(п["label"], п["url"]) for п in context.post_login],
				[(frappe._(АГЕНТЫ_И_СЕССИИ[0]), АГЕНТЫ_И_СЕССИИ[1]), ("My Account", "/me")],
			)

	def test_шапка_и_подвал_на_языке_запроса(self):
		"""Подписи — английский исходник, русский — перевод (learning-services#767)."""
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		for язык, курсы, агенты, подвал in (
			("ru", "Курсы", "Агенты и сессии", "исходный код"),
			("en", "Courses", "Agents and sessions", "source code"),
		):
			with self.subTest(язык):
				frappe.local.lang = язык
				шапка = self.шапка(self.куратор)
				self.assertEqual(шапка.top_bar_items[0].label, курсы)
				self.assertEqual(шапка.post_login[0].label, агенты)
				self.assertIn(f">{подвал}</a>", шапка.footer_powered)

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
		# Кабинет автора русский при любом языке — и шапка с подвалом тоже.
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.local.lang = "en"
		context = frappe._dict()
		author.get_context(context)
		self.assertEqual(context.home_page, "/lms")
		self.assertEqual(context.top_bar_items[0].label, "Курсы")
		self.assertIn(">исходный код</a>", context.footer_powered)
