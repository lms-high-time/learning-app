# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Страница «Задайте пароль» вместо страницы Frappe (learning-services#460)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_html_for_route

from lms_frappe_app.access import КЛЮЧ_ДЕЙСТВУЕТ, КЛЮЧ_НЕ_НАЙДЕН
from lms_frappe_app.tests.test_access import ключ, ученик
from lms_frappe_app.www.update_password import get_context, сведения


class IntegrationTestUpdatePasswordPage(IntegrationTestCase):
	def tearDown(self):
		frappe.form_dict.clear()
		frappe.local.flags.redirect_location = None

	def test_без_ключа_ведёт_на_восстановление(self):
		"""Без ключа страница Frappe просила старый пароль, и её принимали за
		продолжение сброса (learning-services#459)."""
		frappe.form_dict.clear()

		with self.assertRaises(frappe.Redirect):
			get_context(frappe._dict())
		self.assertEqual(frappe.local.flags.redirect_location, "/login#forgot")

	def test_действующий_ключ_даёт_форму(self):
		свой = ключ(ученик())

		self.assertEqual(сведения(свой), {"state": КЛЮЧ_ДЕЙСТВУЕТ, "key": свой})

	def test_ненайденный_ключ_в_страницу_не_попадает(self):
		self.assertEqual(сведения("выдуманный"), {"state": КЛЮЧ_НЕ_НАЙДЕН, "key": ""})

	def test_frappe_отдаёт_нашу_страницу(self):
		"""Страница приложения, установленного позже, перекрывает страницу Frappe.

		`Why:` без этого тесты `сведения` зеленели бы, а человек видел бы
		прежнюю страницу.
		"""
		frappe.form_dict.key = "выдуманный"

		html = get_html_for_route("update-password")

		self.assertIn("Ссылка уже использована или недействительна", html)
		self.assertIn("frappe.core.doctype.user.user.reset_password", html)
