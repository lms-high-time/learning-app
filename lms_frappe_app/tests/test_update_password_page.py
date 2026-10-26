# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Страница «Задайте пароль» вместо страницы Frappe (learning-services#460)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_html_for_route

from lms_frappe_app.passwords import МИНИМУМ_ДЛИНЫ
from lms_frappe_app.tests.test_access import ключ, ученик
from lms_frappe_app.www.update_password import ЗАПРОС_ССЫЛКИ, get_context


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

	def test_форма_пароля_с_логином_и_требованием_длины(self):
		"""Логин нужен менеджеру паролей. Длина в подсказке и в проверке формы —
		та, что проверяет сервер (learning-services#666)."""
		пользователь = ученик()
		frappe.form_dict.key = ключ(пользователь)

		html = get_html_for_route("update-password")

		self.assertIn(f'value="{пользователь}"', html)
		self.assertIn(frappe.form_dict.key, html)
		self.assertIn('autocomplete="username"', html)
		self.assertIn(f"Не короче {МИНИМУМ_ДЛИНЫ} символов", html)
		self.assertIn(f'"min_length": {МИНИМУМ_ДЛИНЫ}', html)

	def test_frappe_отдаёт_нашу_страницу(self):
		"""Страница приложения, установленного позже, перекрывает страницу Frappe.

		`Why:` без этого `get_context` отдавал бы верные сведения, а человек
		видел бы прежнюю страницу.
		"""
		frappe.form_dict.key = "выдуманный"

		html = get_html_for_route("update-password")

		self.assertIn("Ссылка уже использована или недействительна", html)
		self.assertIn("frappe.core.doctype.user.user.reset_password", html)
		self.assertNotIn("выдуманный", html)

	def test_логотип_выводится_экранированным(self):
		"""Адрес логотипа задаётся в настройках сайта и выводится через `| e`."""
		frappe.form_dict.key = "выдуманный"
		адрес = '/files/logo.png" data-x="1'

		with patch("lms_frappe_app.www.update_password.get_app_logo", return_value=адрес):
			html = get_html_for_route("update-password")

		self.assertIn('src="/files/logo.png&#34; data-x=&#34;1"', html)
		self.assertNotIn(адрес, html)
