# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Экран согласия с документами (learning-services#624).

Сюда `consent.потребовать_согласие` ведёт вошедшего без явного согласия с
действующей редакцией. После согласия — назад, на `redirect-to`: только свой
адрес (`consent.куда_вернуть`).
"""

from urllib.parse import quote

import frappe
from frappe import _
from frappe.core.doctype.navbar_settings.navbar_settings import get_app_logo

from lms_frappe_app.agent_learning import consent

no_cache = 1

#: Куда вернуть, если адрес не передан или чужой.
ДОМОЙ = "/lms"


def _уйти(адрес: str):
	# 302, а не 301 Frappe по умолчанию: закэшированный 301 после смены
	# редакции увёл бы мимо экрана. Адрес — в кодировке заголовка: кириллица
	# в пути уронила бы ответ на latin-1.
	frappe.local.flags.redirect_location = quote(адрес, safe="/?&=%#:+,;@")
	raise frappe.Redirect(302)


def get_context(context):
	if frappe.session.user == "Guest":
		_уйти("/login")
	назад = consent.куда_вернуть(frappe.form_dict.get("redirect-to"), ДОМОЙ)
	if consent.есть_явное(frappe.session.user):
		_уйти(назад)
	context.no_breadcrumbs = True
	if consent.принимал_прежде(frappe.session.user):
		context.title = _("We've updated our terms")
		дата = consent.дата_редакции()
		context.subtitle = (
			_("The new Terms of Use and Privacy Policy take effect on {0}.").format(дата)
			if дата
			else _("The new Terms of Use and Privacy Policy are now in effect.")
		)
	else:
		context.title = _("Complete your sign-up")
		context.subtitle = _("All that's left is to confirm your age and accept the terms.")
	context.logo = get_app_logo()
	context.redirect_to = назад
	context.csrf_token = frappe.sessions.get_csrf_token()
	return context
