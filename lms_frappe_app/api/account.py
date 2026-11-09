# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Страница «Агенты и сессии» `/connections` (learning-services#669): отключить
агента, завершить сессию браузера.

Методы зовёт страница из сессии браузера; агент — нет. Логика — в
`lms_frappe_app.connections`, здесь — кто и как может её позвать.
"""

import hmac

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from lms_frappe_app import connections
from lms_frappe_app.agent_learning.consent import от_имени
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.api import контракт, текущий_пользователь

#: Вызов не из браузера пользователя: токен агента или вход «от имени».
ТОЛЬКО_САМ = "account_by_user_only"

#: Вызовов с паролем за час с одного адреса — как у `clear_session` Frappe.
#: Подбор пароля держит ещё и счётчик неверных попыток пользователя
#: (`connections.подтвердить_паролем`).
ПОПЫТОК_ЗА_ЧАС = 10


def только_сам() -> str:
	"""Пользователь из сессии браузера — сам, своим входом.

	`Why:` агент с токеном ученика иначе отключал бы других агентов и видел
	сессии с адресами, а вошедший «от имени» действовал бы за пользователя.
	"""
	пользователь = текущий_пользователь()
	# Вне запроса (задача, консоль) заголовков нет, и `get_request_header` упал бы.
	if (frappe.request and frappe.get_request_header("Authorization")) or от_имени():
		raise Отказ(ТОЛЬКО_САМ, _("Only the user themselves can do this, on the “Agents and sessions” page"))
	return пользователь


def проверить_csrf() -> None:
	"""Токен CSRF в заголовке — всегда, а не только когда он есть у сессии.

	`Why:` Frappe сверяет токен, только если сессия его уже получила
	(`HTTPRequest.validate_csrf_token`): сессия, которая ни разу не открыла
	страницу с токеном, приняла бы POST без него. Страница токен выдаёт и шлёт
	его заголовком `X-Frappe-CSRF-Token`.
	"""
	if not frappe.request:
		return
	# Латиница вопреки стилю: трассировку с переменными (Error Log, Sentry)
	# Frappe чистит по латинским подстрокам имён (learning-services#664).
	own_token = (frappe.session.data or {}).get("csrf_token") or ""
	sent_token = frappe.get_request_header("X-Frappe-CSRF-Token") or ""
	if not own_token or not hmac.compare_digest(sent_token.encode(), own_token.encode()):
		frappe.throw(_("Invalid Request"), frappe.CSRFTokenError)


def _сам_со_страницы() -> str:
	пользователь = только_сам()
	проверить_csrf()
	return пользователь


@frappe.whitelist(methods=["POST"])
@контракт
def disconnect_agent(connection: str) -> dict:
	"""Отзывает подключение агента: токены его входа и неиспользованные коды."""
	connections.отключить(_сам_со_страницы(), connection)
	return {"connection": connection}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=ПОПЫТОК_ЗА_ЧАС, seconds=60 * 60, methods=["POST"])
@контракт
def end_session(session: str, password: str | None = None) -> dict:
	"""Завершает другую свою сессию браузера — после повторного ввода пароля."""
	пользователь = _сам_со_страницы()
	connections.подтвердить_паролем(пользователь, password)
	connections.завершить_сессию(пользователь, session, frappe.session.sid)
	return {"ended": 1}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=ПОПЫТОК_ЗА_ЧАС, seconds=60 * 60, methods=["POST"])
@контракт
def end_other_sessions(password: str | None = None) -> dict:
	"""Завершает все свои сессии, кроме текущей, — после повторного ввода пароля."""
	пользователь = _сам_со_страницы()
	connections.подтвердить_паролем(пользователь, password)
	return {"ended": connections.завершить_другие(пользователь, frappe.session.sid)}
