# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Требования к паролю поверх политики Frappe (learning-services#666)."""

import hashlib
from unittest.mock import Mock, patch

import frappe
import requests
from frappe.tests import UnitTestCase

from lms_frappe_app import passwords
from lms_frappe_app.passwords import (
	АДРЕС_УТЕЧЕК,
	КОРОТКИЙ,
	МИНИМУМ_ДЛИНЫ,
	ПРОСТОЙ,
	С_НАЗВАНИЕМ,
	УТЁК,
	есть_в_диапазоне,
	пароль_к_проверке,
	причина_отказа,
	проверить_пароль_пользователя,
	утёк,
)

ХОРОШИЙ = "корова-забор-синий-трамвай"


class UnitTestPasswordRules(UnitTestCase):
	def test_причина_отказа(self):
		"""Длина, название платформы, контекстные и личные слова, утечка.
		Утечка проверяется последней: сеть — только для пароля, прошедшего
		остальное."""
		случаи = {
			"короче минимума": ("Ab3$" * 2 + "xyz", (), False, КОРОТКИЙ),
			"название платформы": ("мой-cenda-пароль-2026", (), False, С_НАЗВАНИЕМ),
			"название с цифрами вместо букв и разделителем": ("Super-C3nd4-Pass", (), False, С_НАЗВАНИЕМ),
			"название через точку": ("High.Time.Forever!", (), False, С_НАЗВАНИЕМ),
			"название по-русски": ("Мой-ХайТайм-пароль", (), False, С_НАЗВАНИЕМ),
			"контекстное слово и простые цифры": ("lms123456789", (), False, ПРОСТОЙ),
			"своё имя дважды": ("ivanovivanov", ("Ivanov", None), False, ПРОСТОЙ),
			"утёкший пароль": (ХОРОШИЙ, (), True, УТЁК),
			"годный пароль": (ХОРОШИЙ, (), False, None),
		}
		for что, (пароль, сведения, в_утечках, причина) in случаи.items():
			with self.subTest(что), patch.object(passwords, "утёк", return_value=в_утечках) as проверка:
				self.assertEqual(причина_отказа(пароль, сведения), причина)
				self.assertEqual(проверка.called, причина in (УТЁК, None))
		self.assertEqual(len("Ab3$" * 2 + "xyz"), МИНИМУМ_ДЛИНЫ - 1)

	def test_есть_в_диапазоне(self):
		"""Строки дополнения (`Add-Padding`) с числом 0 — не утечка."""
		ответ = "0018A45C4D1DEF81644B54AB7F969B88D65:10\r\nABCDEF:0\n00D4F6E8FA6EECAD2A3AA415EEC418D38EC:2"
		случаи = {
			"есть": ("00D4F6E8FA6EECAD2A3AA415EEC418D38EC", True),
			"без учёта регистра": ("0018a45c4d1def81644b54ab7f969b88d65".upper(), True),
			"строка дополнения": ("ABCDEF", False),
			"нет": ("FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF", False),
		}
		for что, (хвост, итог) in случаи.items():
			with self.subTest(что):
				self.assertEqual(есть_в_диапазоне(ответ, хвост), итог)

	def test_утёк_по_диапазону_и_без_сервиса(self):
		"""Сервису уходят только 5 знаков хеша, с дополнением ответа и
		таймаутом. Сервис недоступен или отвечает ошибкой — пароль не утёк:
		смену пароля сбой чужого сервиса не закрывает."""
		хеш = hashlib.sha1(ХОРОШИЙ.encode()).hexdigest().upper()
		ответ = Mock(text=f"{хеш[5:]}:3", raise_for_status=Mock())
		with patch.object(requests, "get", return_value=ответ) as запрос:
			self.assertTrue(утёк(ХОРОШИЙ))
		self.assertEqual(запрос.call_args.args, (АДРЕС_УТЕЧЕК + хеш[:5],))
		self.assertEqual(запрос.call_args.kwargs["headers"]["Add-Padding"], "true")
		self.assertTrue(запрос.call_args.kwargs["timeout"])

		сбой = Mock(text="", raise_for_status=Mock(side_effect=requests.HTTPError("503")))
		for что, поведение in {
			"таймаут": {"side_effect": requests.Timeout()},
			"нет связи": {"side_effect": requests.ConnectionError()},
			"ответ 503": {"return_value": сбой},
		}.items():
			with self.subTest(что), patch.object(requests, "get", **поведение):
				self.assertFalse(утёк(ХОРОШИЙ))

	def test_пароль_документа_пользователя(self):
		"""Случайный пароль регистрации (`ignore_password_policy`) не проверяется:
		человек его не знает. Хук вне тестов отклоняет пароль, заданный в desk."""

		def документ(пароль, без_политики=False):
			return frappe._dict(new_password=пароль, flags=frappe._dict(ignore_password_policy=без_политики))

		self.assertEqual(пароль_к_проверке(документ("короткий")), "короткий")
		self.assertIsNone(пароль_к_проверке(документ("короткий", без_политики=True)))
		self.assertIsNone(пароль_к_проверке(документ("")))

		with patch.object(frappe, "in_test", False), self.assertRaises(frappe.ValidationError) as отказ:
			проверить_пароль_пользователя(документ("короткий"))
		self.assertIn(КОРОТКИЙ, str(отказ.exception))

	def test_хук_пользователя_подключён(self):
		"""Пароль из desk и REST проверяется до `validate` Frappe: там пароль
		уходит из `new_password` в приватное поле контроллера."""
		хуки = frappe.get_hooks("doc_events")["User"]["before_validate"]
		self.assertIn("lms_frappe_app.passwords.проверить_пароль_пользователя", хуки)
