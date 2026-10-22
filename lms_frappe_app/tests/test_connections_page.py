# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Страница «Агенты и сессии» `/connections` (learning-services#669)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_html_for_route

from lms_frappe_app.connections import отпечаток
from lms_frappe_app.tests.sample_data import создать_ученика
from lms_frappe_app.tests.test_connections import завести_клиента, завести_сессию, завести_токен
from lms_frappe_app.tests.test_consent import запрос
from lms_frappe_app.www import connections as страница


class IntegrationTestConnectionsPage(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(setattr, frappe.local.flags, "redirect_location", None)

	def test_гость_уходит_на_вход(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.Redirect):
			страница.get_context(frappe._dict())
		self.assertEqual(frappe.local.flags.redirect_location, "/login?redirect-to=%2Fconnections")

	def test_агент_по_токену_страницу_не_читает(self):
		frappe.set_user(создать_ученика(f"page-agent-{frappe.generate_hash(length=8)}@example.com"))
		with запрос("/connections", Authorization="Bearer x"), self.assertRaises(frappe.PermissionError):
			страница.get_context(frappe._dict())

	def test_страница_показывает_агентов_и_сессии(self):
		суффикс = frappe.generate_hash(length=8)
		ученик = создать_ученика(f"page-{суффикс}@example.com")
		# Имя агента клиент пишет о себе сам. Очистка Frappe при сохранении
		# режет обработчики, но не разметку, — в базу оно кладётся как есть.
		чужое_имя = '<img src=x onerror="alert(1)">'
		клиент = завести_клиента(адрес="https://agent.example/cb")
		frappe.db.set_value("OAuth Client", клиент, "app_name", чужое_имя, update_modified=False)
		семья = f"page-{суффикс}"
		токен = завести_токен(клиент, ученик, семья)
		frappe.set_user(ученик)
		другая = завести_сессию(ученик, user_agent="Mozilla/5.0 (X11; Linux x86_64) Firefox/142.0")

		html = get_html_for_route("connections")

		with self.subTest("агент и кнопка отключения"):
			self.assertIn("Подключённые агенты", html)
			self.assertIn(f'data-disconnect="{отпечаток(клиент, семья)}"', html)
		with self.subTest("имя агента экранировано"):
			self.assertIn("&lt;img src=x", html)
			self.assertNotIn(чужое_имя, html)
		with self.subTest("сессия с кнопкой и формой пароля"):
			self.assertIn(f'data-end="{отпечаток(другая)}"', html)
			self.assertIn("Firefox, Linux", html)
			self.assertIn('autocomplete="current-password"', html)
			self.assertIn("Завершить все, кроме этой", html)
		with self.subTest("методы и токен CSRF — странице"):
			self.assertIn("lms_frappe_app.api.account.end_other_sessions", html)
			self.assertIn(frappe.session.data.csrf_token, html)
		with self.subTest("ни токена, ни sid"):
			self.assertNotIn(токен, html)
			self.assertNotIn(другая, html)
