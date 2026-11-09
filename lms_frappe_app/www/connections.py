# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""«Агенты и сессии»: подключённые агенты и сессии браузера пользователя
(learning-services#669).

Данные — из `lms_frappe_app.connections`, действия — методами
`lms_frappe_app.api.account`. Страница открыта любому вошедшему, в том числе
ученику без desk: свои подключения и сессии есть у каждого.
"""

from urllib.parse import urlencode

import frappe
from frappe import _

from lms_frappe_app import connections, languages
from lms_frappe_app.site_navigation import шапка_платформы

no_cache = 1

СТРАНИЦА = "/connections"
#: Задать пароль по ссылке из письма — тому, кто его не знает (вход через Google).
ЗАДАТЬ_ПАРОЛЬ = "/update-password"
ПОДКЛЮЧИТЬ_АГЕНТА = "/lms/agent"
МЕТОДЫ = {
	"disconnect": "lms_frappe_app.api.account.disconnect_agent",
	"end": "lms_frappe_app.api.account.end_session",
	"end_others": "lms_frappe_app.api.account.end_other_sessions",
}


def тексты_скрипта() -> dict[str, str]:
	"""Тексты скрипта страницы — на языке запроса. `{0}` подставляет скрипт."""
	return {
		"offline": _("Can't reach the server. Please try again."),
		"too_many": _("Too many attempts. Wait a moment and try again."),
		"stale": _("This page is out of date. Refresh it and try again."),
		"failed": _("Something went wrong. Please try again."),
		"disconnect_confirm": _("Disconnect “{0}”? The agent will lose access to Cenda."),
		"disconnected": _("“{0}” has been disconnected."),
		"no_agents": _("No connected agents."),
		"end_confirm": _("End the session “{0}”?"),
		"end_others_confirm": _("End all sessions except this one?"),
		"password_required": _("Enter a password."),
		"others_ended": _("Other sessions ended: {0}."),
		"no_others": _("There were no other sessions."),
		"already_ended": _("This session had already been ended."),
		"ended": _("Session ended."),
	}


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?" + urlencode({"redirect-to": СТРАНИЦА})
		raise frappe.Redirect(302)
	# Агент с токеном пользователя страницу не читает: на ней адреса и
	# устройства его сессий.
	if frappe.request and frappe.get_request_header("Authorization"):
		raise frappe.PermissionError
	context.no_breadcrumbs = True
	шапка_платформы(context)
	context.title = _("Agents and sessions")
	context.дата_время = languages.дата_время
	context.user = frappe.session.user
	context.agents = connections.подключения(frappe.session.user)
	context.sessions = connections.сессии(frappe.session.user, frappe.session.sid)
	context.other_sessions = sum(1 for с in context.sessions if not с["current"])
	context.set_password_url = ЗАДАТЬ_ПАРОЛЬ
	context.connect_url = ПОДКЛЮЧИТЬ_АГЕНТА
	context.page_data = {
		"csrf": frappe.sessions.get_csrf_token(),
		"methods": МЕТОДЫ,
		"texts": тексты_скрипта(),
	}
	return context
