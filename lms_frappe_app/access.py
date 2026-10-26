# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Вход по почте с паролем рядом со входом через Google (learning-services#460).

Регистрация и пароль — механика Frappe. Здесь то, что делает её путь
понятным: занятый адрес не тупик, ссылка проверяется до ввода пароля, смена
пароля подтверждается письмом. Пути пользователя — learning-services,
`docs/plans/2026-10-01-access-flows-design.md`.
"""

from datetime import timedelta
from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.auth import MAX_PASSWORD_SIZE
from frappe.core.doctype.user import user as frappe_user
from frappe.rate_limiter import rate_limit
from frappe.twofactor import should_run_2fa
from frappe.utils import cint, escape_html, get_url, now_datetime
from frappe.utils.data import sha256_hash
from frappe.website.utils import is_signup_disabled

from lms_frappe_app.agent_learning.notices import почта_есть, ссылка
from lms_frappe_app.passwords import проверить_пароль, сведения_пользователя
from lms_frappe_app.agent_learning.oauth_client import отозвать_токены_пользователя

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
	"""`update_password` Frappe, письмо «Пароль изменён» и отзыв токенов агентов.

	`Why:` Frappe шлёт такое письмо только при смене пароля в desk, а смену по
	ссылке не подтверждает ничем: человек не знает, сработала ли она, и не
	узнает, если пароль сменил кто-то другой. Первое задание пароля — после
	регистрации по почте или входа через Google — письма не получает: человек
	ничего не менял, а тревога на ровном месте пугает. Признак первого раза —
	пустой `last_password_reset_date`, как и у самого Frappe. Он ошибается в
	двух случаях: пароль, заданный в desk, даты не ставит, а включение
	`force_user_to_reset_password` проставляет её всем пустым.

	Пароль проверяется требованиями платформы до Frappe (`passwords.py`,
	learning-services#666): Frappe пишет пароль мимо документа `User`, и хук
	документа его не видит. Предел длины Frappe — раньше наших проверок:
	zxcvbn и хеш для сервиса утечек не должны получать мегабайт. Пользователь
	не найден — ключ недействителен, и Frappe ответит 410 сам.

	Токены OAuth пользователя отзываются при любой удачной смене, и первой
	тоже (learning-services#665): `update_password` Frappe завершает только
	сессии браузера, а агент жил на токене обновления, выданном до смены.
	"""
	if len(new_password) > MAX_PASSWORD_SIZE:
		frappe.throw(_("Password size exceeded the maximum allowed size."))
	запись = запись_ключа(key)
	пользователь = запись.name if запись else (None if key else frappe.session.user)
	if пользователь and пользователь != "Guest":
		проверить_пароль(new_password, сведения_пользователя(пользователь))
	впервые = not пользователь or not frappe.db.get_value("User", пользователь, "last_password_reset_date")
	ответ = frappe_user.update_password(
		new_password, logout_all_sessions=logout_all_sessions, key=key, old_password=old_password
	)
	сменён = frappe.local.response.get("http_status_code") != 410
	if not пользователь or пользователь == "Guest" or not сменён:
		return ответ
	отозвать_токены_пользователя(пользователь)
	if not впервые:
		_письмо_пароль_изменён(пользователь)
	if key and сменён and should_run_2fa(пользователь):
		return _на_вход_со_вторым_фактором(ответ)
	return ответ


def _на_вход_со_вторым_фактором(куда: str | None) -> str:
	"""Выход из сессии, которую открыл `update_password` Frappe, и адрес входа.

	`Why:` после смены пароля по ссылке Frappe входит под пользователем сам
	(`login_as`), мимо второго фактора, — и ссылка из письма заменяла бы
	код из приложения (learning-services#666). Пользователю со вторым
	фактором пароль меняется, но входит он обычным входом, где спросят код;
	адрес, куда Frappe вёл после смены, становится адресом возврата.
	"""
	frappe.local.login_manager.logout()
	return f"/login?{urlencode({'redirect-to': куда})}" if куда else "/login"


def _письмо_пароль_изменён(пользователь: str) -> None:
	if not почта_есть():
		return
	frappe.sendmail(
		recipients=[frappe.db.get_value("User", пользователь, "email")],
		subject="Пароль изменён",
		message=(
			f"<p>Пароль от вашего аккаунта на {escape_html(_название())} изменён.</p>"
			f"<p>Если это сделали не вы, {ссылка(get_url('/login#forgot'), 'задайте новый пароль')}: "
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
	# Ссылка несёт ключ сброса пароля. `Why:` латиница вопреки стилю —
	# трассировку с переменными (Error Log, Sentry) Frappe чистит по латинским
	# подстрокам имён (`key`, `token`, `password`), кириллическое имя ушло бы в
	# журнал как есть (learning-services#664).
	reset_key_link = документ._reset_password()
	frappe.sendmail(
		recipients=[frappe.db.get_value("User", пользователь, "email")],
		subject=f"У вас уже есть аккаунт на {_название()}",
		message=(
			"<p>На этот адрес пытались зарегистрироваться, но аккаунт с ним уже есть.</p>"
			"<p>Если вы входили через Google — войдите так же. Или "
			f"{ссылка(reset_key_link, 'задайте пароль')} и входите по почте.</p>"
			"<p>Если регистрировались не вы, ничего делать не нужно: без этой ссылки "
			"пароль не поменять.</p>"
		),
	)
