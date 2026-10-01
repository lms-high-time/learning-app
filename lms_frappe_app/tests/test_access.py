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
