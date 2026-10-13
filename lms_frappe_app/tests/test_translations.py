# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Свой файл переводов интерфейса.

`Why:` в `frappe/lms` часть строк не переведена, а править её `ru.po` значит
потерять работу при первом обновлении чужого приложения. Frappe читает
`<app>/translations/<lang>.csv` без компиляции и обходит приложения в порядке
установки — наше последнее, поэтому его переводы перекрывают LMS.

Битую строку Frappe не роняет, а молча пишет в лог: без теста испорченный
файл выглядел бы как «часть интерфейса вдруг снова по-английски».
"""

import csv
import re
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.translate import get_all_translations

ФАЙЛ = Path(frappe.get_app_path("lms_frappe_app", "translations", "ru.csv"))

ПЛЕЙСХОЛДЕР = re.compile(r"\{[^}]*\}")


def _строки() -> list[list[str]]:
	with ФАЙЛ.open(encoding="utf-8") as ф:
		return list(csv.reader(ф))


class TestФайлПереводов(UnitTestCase):
	"""Разбор самого файла — без базы."""

	def test_файл_разбирается_и_не_пуст(self):
		строки = _строки()

		self.assertTrue(строки)
		for строка in строки:
			self.assertIn(len(строка), (2, 3), f"неверное число полей: {строка[:1]}")
			self.assertTrue(строка[1].strip(), f"пустой перевод: {строка[0]!r}")

	def test_плейсхолдеры_не_потеряны(self):
		"""`{0}` в переводе подставляется по номеру: потеря ломает фразу."""
		for оригинал, перевод, *_ in _строки():
			self.assertEqual(
				sorted(ПЛЕЙСХОЛДЕР.findall(оригинал)),
				sorted(ПЛЕЙСХОЛДЕР.findall(перевод)),
				f"разошлись плейсхолдеры: {оригинал!r}",
			)

	def test_строк_с_краевыми_пробелами_нет(self):
		"""`Why:` Frappe обрезает перевод (`strip` в `get_translation_dict_from_file`),
		и краевой пробел до платформы не доедет. Такие строки — куски
		предложений из писем: переведёшь — слова слипнутся («опубликован
		наFrappe Learning»). Пусть остаются английскими, это заметно меньше.
		Проверено CI: сверка с общим словарём Frappe падала именно на них."""
		с_пробелами = [
			о for о, *_ in _строки() if о[:1] == " " or о[-1:] == " "
		]

		self.assertEqual(с_пробелами, [], "такую строку Frappe обрежет")


class IntegrationTestTranslations(IntegrationTestCase):
	def test_письма_о_пароле_переведены_целиком(self):
		"""Приглашение после регистрации и сброс пароля — шаблоны Frappe.

		`Why:` у Frappe переведена половина их строк, и письмо приходило
		вперемешку русским и английским (learning-services#460). Все строки —
		в нашем файле, а не в переводах Frappe: в CI Frappe свежее стенда, и
		его перевод части строк там уже другой. Строки — из
		`frappe/templates/emails/new_user.html` и `password_reset.html`.
		"""
		словарь = get_all_translations("ru")
		строки = (
			"Reset your password",
			"Dear",
			"We received a request to reset the password for your account. "
			"Click the button below to set a new password.",
			"Reset Password",
			"If you did not request a password reset, please ignore this email. "
			"Your password will remain unchanged.",
			"Complete your registration",
			"Hello",
			"Your login ID is:",
			"Click the button below to complete your registration and set a new password.",
			"Complete Registration",
			"Or copy and paste this link:",
			"A new account has been created for you at {0}.",
			"Welcome to {0}",
			"Password Reset",
			"Thank you",
			"Thanks",
		)

		свои = {оригинал for оригинал, *_ in _строки()}
		self.assertEqual([с for с in строки if с not in свои], [])
		self.assertEqual([с for с in строки if not словарь.get(с)], [])
		# Перевод Frappe «Уважаемый» угадывает пол адресата.
		self.assertEqual(словарь["Dear"], "Здравствуйте,")

	def test_страница_входа_переведена_целиком(self):
		"""Вход, регистрация, «забыли пароль» и вход по ссылке — шаблон Frappe
		`frappe/www/login.html` и его скрипт `templates/includes/login/login.js`.

		`Why:` у Frappe часть его строк в `ru.po` пустая, и русскоязычный
		посетитель видел вход вперемешку: «Sign In», «Forgot password?»,
		«Create Account» рядом с «Почта» и «Продолжить». А «Sign up» Learning
		переводит «Войти» — ссылка на регистрацию звала войти
		(learning-services#563). Строки — в нашем файле, по той же причине,
		что и письма: переводы Frappe в CI и на стенде разные.
		"""
		словарь = get_all_translations("ru")
		строки = (
			"Sign In",
			"Welcome! Please sign in to continue.",
			"Email",
			"jane@example.com",
			"Password",
			"Continue",
			"Forgot Password?",
			"Send login link",
			"Login with {0}",
			"Signup Disabled",
			"Signups have been disabled for this website.",
			"Password is required.",
			"Invalid Email.",
			"Sent",
			"Forgot password?",
			"Don't have an account?",
			"Sign up",
			"Sign Up",
			"Let's setup your account.",
			"Create Account",
			"Please enter your email, we'll send you password reset link",
			"Send Link",
			"Back to sign in",
			"Please check your email.",
			"Didn't receive the link?",
			"Resend",
			"Login with password",
			"Login with Email Link",
			"Email is required.",
			"Please enter a valid email.",
			"Full name is required.",
			"Invalid credentials, try again.",
		)

		# Действует именно наш перевод: «где-то переведено» прошло бы и с чужим.
		свои = {оригинал: перевод for оригинал, перевод, *_ in _строки()}
		self.assertEqual([с for с in строки if с not in свои], [])
		self.assertEqual({с: словарь.get(с) for с in строки}, {с: свои[с] for с in строки})
		self.assertEqual(словарь["Sign up"], "Зарегистрироваться")
