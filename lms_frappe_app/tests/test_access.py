# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Вход по почте с паролем рядом со входом через Google (learning-services#460)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from lms_frappe_app.access import (
	КЛЮЧ_ДЕЙСТВУЕТ,
	КЛЮЧ_НЕ_НАЙДЕН,
	КЛЮЧ_УСТАРЕЛ,
	ПРОВЕРЬТЕ_ПОЧТУ,
	sign_up,
	sign_up_learning,
	update_password,
	состояние_ключа,
)
from lms_frappe_app.tests.sample_data import создать_ученика

СРОК = "reset_password_link_expiry_duration"


def ученик() -> str:
	return создать_ученика(f"access-{frappe.generate_hash(length=8)}@example.com")


def со_сроком(секунды: int):
	"""Срок ссылки сброса только на время теста.

	`Why:` `get_system_settings` кэширует документ настроек в двух местах, и
	записанный в базу срок тест не видел — он получал срок предыдущего теста.
	"""
	настоящий = frappe.get_system_settings
	return patch(
		"frappe.get_system_settings", side_effect=lambda поле: секунды if поле == СРОК else настоящий(поле)
	)


def ключ(пользователь: str) -> str:
	"""Ключ из ссылки сброса — так же, как он приходит в письме."""
	return frappe.get_doc("User", пользователь)._reset_password().split("key=", 1)[1]


class IntegrationTestResetKey(IntegrationTestCase):
	"""Состояние ключа сброса: страница пароля решает по нему, что показать."""

	def test_свежий_ключ_действует(self):
		self.assertEqual(состояние_ключа(ключ(ученик())), КЛЮЧ_ДЕЙСТВУЕТ)

	def test_просроченный_ключ_устарел(self):
		пользователь = ученик()
		свой = ключ(пользователь)
		frappe.db.set_value(
			"User", пользователь, "last_reset_password_key_generated_on", add_to_date(now_datetime(), days=-1)
		)

		with со_сроком(3600):
			self.assertEqual(состояние_ключа(свой), КЛЮЧ_УСТАРЕЛ)

	def test_без_срока_ключ_не_стареет(self):
		пользователь = ученик()
		свой = ключ(пользователь)
		frappe.db.set_value(
			"User", пользователь, "last_reset_password_key_generated_on", add_to_date(now_datetime(), days=-30)
		)

		with со_сроком(0):
			self.assertEqual(состояние_ключа(свой), КЛЮЧ_ДЕЙСТВУЕТ)

	def test_использованный_и_выдуманный_ключ_не_найдены(self):
		"""Frappe очищает ключ после смены пароля: использованный неотличим от опечатки."""
		пользователь = ученик()
		свой = ключ(пользователь)
		frappe.db.set_value("User", пользователь, "reset_password_key", "")

		self.assertEqual(состояние_ключа(свой), КЛЮЧ_НЕ_НАЙДЕН)
		self.assertEqual(состояние_ключа("выдуманный"), КЛЮЧ_НЕ_НАЙДЕН)
		self.assertEqual(состояние_ключа(""), КЛЮЧ_НЕ_НАЙДЕН)


НОВЫЙ_ПАРОЛЬ = "Новый-пароль-460!"


class IntegrationTestPasswordChanged(IntegrationTestCase):
	"""Письмо «Пароль изменён» после смены пароля по ссылке."""

	def setUp(self):
		from frappe.auth import CookieManager, LoginManager
		from frappe.utils import set_request

		# `update_password` Frappe входит под пользователем — нужен запрос.
		set_request(method="POST", path="/")
		frappe.local.cookie_manager = CookieManager()
		frappe.local.login_manager = LoginManager()
		self.письма = patch("frappe.sendmail").start()
		patch("lms_frappe_app.access.почта_есть", return_value=True).start()

	def tearDown(self):
		patch.stopall()
		frappe.local.response.pop("http_status_code", None)
		frappe.set_user("Administrator")

	def test_смена_пароля_по_ссылке_присылает_письмо(self):
		пользователь = ученик()
		frappe.db.set_value("User", пользователь, "last_password_reset_date", "2026-01-01")

		update_password(НОВЫЙ_ПАРОЛЬ, key=ключ(пользователь))

		письмо = self.письма.call_args.kwargs
		self.assertEqual(письмо["recipients"], [пользователь])
		self.assertEqual(письмо["subject"], "Пароль изменён")

	def test_первое_задание_пароля_без_письма(self):
		"""После регистрации или входа через Google человек пароля не менял."""
		update_password(НОВЫЙ_ПАРОЛЬ, key=ключ(ученик()))

		self.письма.assert_not_called()

	def test_недействительный_ключ_без_письма(self):
		update_password(НОВЫЙ_ПАРОЛЬ, key="выдуманный")

		self.письма.assert_not_called()

	def test_перекрытие_подключено(self):
		self.assertEqual(
			frappe.get_hooks("override_whitelisted_methods")["frappe.core.doctype.user.user.update_password"][-1],
			"lms_frappe_app.access.update_password",
		)


class IntegrationTestSignUp(IntegrationTestCase):
	"""Регистрация: новый, занятый и отключённый адрес получают один ответ."""

	def setUp(self):
		from frappe.utils import set_request

		# `sanitize_redirect` и лимит частоты читают запрос и адрес клиента.
		set_request(method="POST", path="/")
		frappe.local.request_ip = "127.0.0.1"
		frappe.cache.delete_keys("rl:")
		self.письма = patch("frappe.sendmail").start()
		patch("lms_frappe_app.access.почта_есть", return_value=True).start()
		# Регистрация открыта, как на стенде; локальный сайт может быть закрыт.
		for модуль in ("lms_frappe_app.access", "frappe.core.doctype.user.user", "lms.lms.user"):
			patch(f"{модуль}.is_signup_disabled", return_value=False).start()

	def tearDown(self):
		patch.stopall()
		frappe.cache.delete_keys("rl:")

	def новый_адрес(self) -> str:
		return f"signup-{frappe.generate_hash(length=8)}@example.com"

	def test_новый_адрес_создаёт_аккаунт(self):
		адрес = self.новый_адрес()

		self.assertEqual(sign_up(адрес, "Новый Ученик", ""), (1, ПРОВЕРЬТЕ_ПОЧТУ))
		self.assertTrue(frappe.db.exists("User", адрес))

	def test_адрес_возврата_переживает_кэш(self):
		"""Кэш Redis может вытесниться, пока человек идёт к письму."""
		адрес = self.новый_адрес()

		sign_up(адрес, "Новый Ученик", "/lms/join/abc")

		self.assertTrue(frappe.db.get_value("User", адрес, "redirect_url").endswith("/lms/join/abc"))

	def test_занятый_адрес_получает_ссылку_на_пароль(self):
		"""«Already Registered» был тупиком для вошедшего через Google (learning-services#459)."""
		пользователь = ученик()

		self.assertEqual(sign_up(пользователь, "Кто-то", ""), (1, ПРОВЕРЬТЕ_ПОЧТУ))
		письмо = self.письма.call_args.kwargs
		self.assertEqual(письмо["recipients"], [пользователь])
		self.assertIn("/update-password?key=", письмо["message"])

	def test_отключённый_адрес_отвечает_так_же_и_без_письма(self):
		пользователь = ученик()
		frappe.db.set_value("User", пользователь, "enabled", 0)

		self.assertEqual(sign_up(пользователь, "Кто-то", ""), (1, ПРОВЕРЬТЕ_ПОЧТУ))
		self.письма.assert_not_called()

	def test_закрытая_регистрация_закрыта_и_для_занятого_адреса(self):
		with patch("lms_frappe_app.access.is_signup_disabled", return_value=True):
			with self.assertRaises(frappe.ValidationError):
				sign_up(ученик(), "Кто-то", "")
		self.письма.assert_not_called()

	def test_форма_learning_тоже_перекрыта(self):
		self.assertEqual(sign_up_learning(ученик(), "Кто-то", 1, ""), (1, ПРОВЕРЬТЕ_ПОЧТУ))
		перекрытия = frappe.get_hooks("override_whitelisted_methods")
		self.assertEqual(перекрытия["lms.lms.user.sign_up"][-1], "lms_frappe_app.access.sign_up_learning")
		self.assertEqual(
			перекрытия["frappe.core.doctype.user.user.sign_up"][-1], "lms_frappe_app.access.sign_up"
		)
