# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Страница «Задайте пароль» вместо страницы Frappe (learning-services#460)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_html_for_route

from lms_frappe_app.access import КЛЮЧ_ДЕЙСТВУЕТ, КЛЮЧ_НЕ_НАЙДЕН
from lms_frappe_app.tests.test_access import ключ, ученик
from lms_frappe_app.www.update_password import ЗАПРОС_ССЫЛКИ, get_context, сведения


class IntegrationTestUpdatePasswordPage(IntegrationTestCase):
	def tearDown(self):
		frappe.form_dict.clear()
		frappe.local.flags.redirect_location = None
		frappe.set_user("Administrator")

	def test_гость_без_ключа_уходит_на_восстановление(self):
		"""Без ключа страница Frappe просит старый пароль, и её принимали за
		продолжение сброса (learning-services#459)."""
		frappe.set_user("Guest")

		with self.assertRaises(frappe.Redirect):
			get_context(frappe._dict())
		self.assertEqual(frappe.local.flags.redirect_location, "/login#forgot")

	def test_вошедший_без_ключа_получает_ссылку_на_свой_адрес(self):
		"""Ссылка «Reset Password» на `/me` ведёт сюда без ключа, а `/login`
		вошедшего уводит на главную — без этого путь кончался тупиком."""
		пользователь = ученик()
		frappe.set_user(пользователь)

		контекст = get_context(frappe._dict())

		self.assertEqual(контекст.state, ЗАПРОС_ССЫЛКИ)
		self.assertEqual(контекст.email, пользователь)

	def test_действующий_ключ_даёт_форму(self):
		пользователь = ученик()
		свой = ключ(пользователь)

		self.assertEqual(сведения(свой), {"state": КЛЮЧ_ДЕЙСТВУЕТ, "key": свой, "email": пользователь})

	def test_ненайденный_ключ_в_страницу_не_попадает(self):
		self.assertEqual(сведения("выдуманный"), {"state": КЛЮЧ_НЕ_НАЙДЕН, "key": "", "email": ""})

	def test_форма_пароля_с_логином_и_без_обещания_длины(self):
		"""Логин нужен менеджеру паролей, а длину Frappe не проверяет — решает
		политика паролей, если она включена."""
		пользователь = ученик()
		frappe.form_dict.key = ключ(пользователь)

		html = get_html_for_route("update-password")

		self.assertIn(f'value="{пользователь}"', html)
		self.assertIn('autocomplete="username"', html)
		self.assertNotIn("8 символов", html)

	def test_frappe_отдаёт_нашу_страницу(self):
		"""Страница приложения, установленного позже, перекрывает страницу Frappe.

		`Why:` без этого тесты `сведения` зеленели бы, а человек видел бы
		прежнюю страницу.
		"""
		frappe.form_dict.key = "выдуманный"

		html = get_html_for_route("update-password")

		self.assertIn("Ссылка уже использована или недействительна", html)
		self.assertIn("frappe.core.doctype.user.user.reset_password", html)
