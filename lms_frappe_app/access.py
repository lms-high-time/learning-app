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
from frappe.core.doctype.user import user as frappe_user
from frappe.utils import cint, escape_html, get_url, now_datetime
from frappe.utils.data import sha256_hash

from lms_frappe_app.agent_learning.notices import почта_есть

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


def _название() -> str:
	return frappe.db.get_single_value("Website Settings", "app_name") or get_url()


@frappe.whitelist(allow_guest=True, methods=["POST"])
def update_password(
	new_password: str,
	logout_all_sessions: int = 0,
	key: str | None = None,
	old_password: str | None = None,
):
	"""`update_password` Frappe и письмо «Пароль изменён».

	`Why:` Frappe шлёт такое письмо только при смене пароля в desk, а смену по
	ссылке не подтверждает ничем: человек не знает, сработала ли она, и не
	узнает, если пароль сменил кто-то другой. Первое задание пароля — после
	регистрации по почте или входа через Google — письма не получает: человек
	ничего не менял, а тревога на ровном месте пугает. Признак первого раза —
	пустой `last_password_reset_date`.
	"""
	запись = _запись_ключа(key)
	пользователь = запись.name if запись else (None if key else frappe.session.user)
	впервые = not пользователь or not frappe.db.get_value("User", пользователь, "last_password_reset_date")
	ответ = frappe_user.update_password(
		new_password, logout_all_sessions=logout_all_sessions, key=key, old_password=old_password
	)
	if not впервые and пользователь != "Guest" and frappe.local.response.get("http_status_code") != 410:
		_письмо_пароль_изменён(пользователь)
	return ответ


def _письмо_пароль_изменён(пользователь: str) -> None:
	if not почта_есть():
		return
	frappe.sendmail(
		recipients=[frappe.db.get_value("User", пользователь, "email")],
		subject="Пароль изменён",
		message=(
			f"<p>Пароль от вашего аккаунта на {escape_html(_название())} изменён.</p>"
			f'<p>Если это сделали не вы, <a href="{get_url("/login#forgot")}">задайте новый пароль</a>: '
			"ссылка придёт на этот адрес.</p>"
		),
	)
