# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Вход по почте с паролем рядом со входом через Google (learning-services#460).

Регистрация и пароль — механика Frappe. Здесь то, что делает её путь
понятным: занятый адрес не тупик, ссылка проверяется до ввода пароля, смена
пароля подтверждается письмом. Пути пользователя — learning-services,
`docs/plans/2026-10-01-access-flows-design.md`.
"""

from datetime import timedelta

import frappe
from frappe.utils import cint, now_datetime
from frappe.utils.data import sha256_hash

КЛЮЧ_ДЕЙСТВУЕТ = "valid"
КЛЮЧ_УСТАРЕЛ = "expired"
КЛЮЧ_НЕ_НАЙДЕН = "not_found"


def _запись_ключа(key: str | None):
	if not key:
		return None
	return frappe.db.get_value(
		"User",
		{"reset_password_key": sha256_hash(key)},
		["name", "last_reset_password_key_generated_on"],
		as_dict=True,
	)


def состояние_ключа(key: str | None) -> str:
	"""Действует ли ключ из ссылки сброса — по правилам `update_password` Frappe.

	Использованный ключ и опечатка неразличимы: после смены пароля Frappe
	очищает ключ, и искать нечего в обоих случаях.
	"""
	запись = _запись_ключа(key)
	if not запись:
		return КЛЮЧ_НЕ_НАЙДЕН
	срок = cint(frappe.get_system_settings("reset_password_link_expiry_duration"))
	if срок and now_datetime() > запись.last_reset_password_key_generated_on + timedelta(seconds=срок):
		return КЛЮЧ_УСТАРЕЛ
	return КЛЮЧ_ДЕЙСТВУЕТ
