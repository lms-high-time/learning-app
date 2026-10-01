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
from frappe import _
from frappe.core.doctype.user import user as frappe_user
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, escape_html, get_url, now_datetime
from frappe.utils.data import sha256_hash
from frappe.website.utils import is_signup_disabled

from lms_frappe_app.agent_learning.notices import почта_есть

КЛЮЧ_ДЕЙСТВУЕТ = "valid"
КЛЮЧ_УСТАРЕЛ = "expired"
КЛЮЧ_НЕ_НАЙДЕН = "not_found"

ПРОВЕРЬТЕ_ПОЧТУ = "Проверьте почту: мы отправили письмо со ссылкой."

#: Общий ключ счётчика регистраций. Frappe ключует счётчик вызванным методом,
#: и без общего ключа каждая форма и `/api/v2` считали бы попытки порознь.
ЛИМИТ_РЕГИСТРАЦИИ = "lms_frappe_app.access.sign_up"


def запись_ключа(key: str | None):
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
	запись = запись_ключа(key)
	if not запись:
		return КЛЮЧ_НЕ_НАЙДЕН
	срок = cint(frappe.get_system_settings("reset_password_link_expiry_duration"))
	выдан = запись.last_reset_password_key_generated_on
	# Без даты выдачи срок не проверить — такой ключ считаем устаревшим.
	if срок and (not выдан or now_datetime() > выдан + timedelta(seconds=срок)):
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
	пустой `last_password_reset_date`, как и у самого Frappe. Он ошибается в
	двух случаях: пароль, заданный в desk, даты не ставит, а включение
	`force_user_to_reset_password` проставляет её всем пустым.
	"""
	запись = запись_ключа(key)
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


@frappe.whitelist(allow_guest=True, methods=["POST"])
def sign_up(email: str, full_name: str, redirect_to: str | None = None) -> tuple[int, str]:
	"""Регистрация формы Frappe на `/login`."""
	адрес = _адрес(email)
	return _зарегистрировать(адрес, lambda: frappe_user.sign_up(адрес, full_name, redirect_to))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def sign_up_learning(email: str, full_name: str, verify_terms: bool, user_category: str) -> tuple[int, str]:
	"""Регистрация формы Learning: её показывают, если в `LMS Settings` есть свои поля."""
	from lms.lms.user import sign_up as learning_sign_up

	адрес = _адрес(email)
	return _зарегистрировать(адрес, lambda: learning_sign_up(адрес, full_name, verify_terms, user_category))


def _адрес(email: str | None) -> str:
	"""Адрес так, как его хранит `User`: Frappe приводит имя к `strip().lower()`.

	`Why:` поиск по сырому вводу проходил мимо занятого адреса с пробелом или
	в другом регистре, и вставка падала ошибкой «уже существует» — ответ формы
	снова выдавал, зарегистрирован ли адрес.
	"""
	return (email or "").strip().lower()


def _зарегистрировать(адрес: str, создать) -> tuple[int, str]:
	if is_signup_disabled():
		frappe.throw(_("Sign Up is disabled"), title=_("Not Allowed"))
	if not адрес:
		frappe.throw(_("Please enter a valid email."))
	# Лимит берёт адрес и метод из `form_dict`: кладём туда приведённый адрес
	# и общий ключ, иначе каждое написание адреса и каждая форма считались бы
	# отдельно.
	прежний = frappe.form_dict.cmd
	frappe.form_dict.update(email=адрес, cmd=ЛИМИТ_РЕГИСТРАЦИИ)
	try:
		return _зарегистрировать_в_лимите(адрес, создать)
	finally:
		frappe.form_dict.cmd = прежний


# По адресу, а не по IP: nginx образа `frontend` подставляет в
# `X-Forwarded-For` адрес контейнера `site` (`@webserver` в `frappe.conf`), и
# Frappe видит у всех клиентов один IP — лимит по нему был бы общим на сайт.
@rate_limit(key="email", limit=5, seconds=60 * 60, ip_based=False)
def _зарегистрировать_в_лимите(адрес: str, создать) -> tuple[int, str]:
	"""Один ответ на новый, занятый и отключённый адрес.

	`Why:` ответ «Already Registered» — тупик для того, кто входил через Google
	и не знает, что аккаунт уже есть (learning-services#459). Занятому адресу
	уходит письмо со ссылкой на пароль, а одинаковый ответ не выдаёт, есть ли
	на платформе чужой адрес. Лимит частоты — против рассылки писем на чужой
	ящик через форму.

	Когда почта не настроена, ответы расходятся: новому адресу Frappe ещё и
	пишет в `message_log` «настройте исходящую почту». Это авария, а не режим
	работы, и прятать её от формы незачем.
	"""
	занятый = frappe.db.get_value("User", {"email": адрес}, ["name", "enabled"], as_dict=True)
	if занятый:
		if not почта_есть():
			# Так Frappe отвечает новому адресу, когда письмо не ушло.
			return 2, _("Please ask your administrator to verify your sign-up")
		# Служебным пользователям ссылку не выдаёт и `reset_password` Frappe.
		if занятый.enabled and занятый.name not in frappe.STANDARD_USERS:
			_письмо_аккаунт_есть(занятый.name)
		return 1, ПРОВЕРЬТЕ_ПОЧТУ
	код, текст = создать()
	if код != 1:
		# Письмо-приглашение не ушло (почта не настроена) — честный ответ Frappe.
		return код, текст
	пользователь = frappe.db.get_value("User", {"email": адрес})
	# Адрес возврата Frappe держит в кэше Redis, а его может вытеснить, пока
	# человек идёт к письму. Поле `update_password` прочтёт и тогда.
	if возврат := frappe.cache.hget("redirect_after_login", пользователь):
		frappe.db.set_value("User", пользователь, "redirect_url", возврат)
	return 1, ПРОВЕРЬТЕ_ПОЧТУ


def _письмо_аккаунт_есть(пользователь: str) -> None:
	if not почта_есть():
		return
	документ = frappe.get_doc("User", пользователь)
	try:
		# Хук приложений, которым `reset_password` Frappe может запретить
		# сброс. Отказ, как и у Frappe, не меняет ответа формы — письма просто нет.
		документ.validate_reset_password()
	except Exception:
		frappe.clear_messages()
		frappe.log_error(title="Ссылка на пароль для занятого адреса не выдана")
		return
	ссылка = документ._reset_password()
	frappe.sendmail(
		recipients=[frappe.db.get_value("User", пользователь, "email")],
		subject=f"У вас уже есть аккаунт на {_название()}",
		message=(
			"<p>На этот адрес пытались зарегистрироваться, но аккаунт с ним уже есть.</p>"
			"<p>Если вы входили через Google — войдите так же. Или "
			f'<a href="{ссылка}">задайте пароль</a> и входите по почте.</p>'
			"<p>Если регистрировались не вы, ничего делать не нужно: без этой ссылки "
			"пароль не поменять.</p>"
		),
	)
