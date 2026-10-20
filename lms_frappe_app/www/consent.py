# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Экран согласия с документами (learning-services#624).

Сюда `consent.потребовать_согласие` ведёт вошедшего без явного согласия с
действующей редакцией. После согласия — назад, на `redirect-to`; адрес
принимается только свой, относительный: чужой превратил бы экран в
переадресацию на любой сайт.
"""

import frappe
from frappe.core.doctype.navbar_settings.navbar_settings import get_app_logo

from lms_frappe_app.agent_learning import consent

no_cache = 1

#: Куда вернуть, если адрес не передан или чужой.
ДОМОЙ = "/lms"


def куда_вернуть(адрес: str | None) -> str:
	"""Свой относительный адрес или `ДОМОЙ`."""
	адрес = (адрес or "").strip()
	if (
		not адрес.startswith("/")
		or адрес.startswith("//")
		or "\\" in адрес
		or адрес.startswith(consent.СТРАНИЦА)
	):
		return ДОМОЙ
	return адрес


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login"
		raise frappe.Redirect
	назад = куда_вернуть(frappe.form_dict.get("redirect-to"))
	if consent.есть_явное(frappe.session.user):
		frappe.local.flags.redirect_location = назад
		raise frappe.Redirect
	context.no_breadcrumbs = True
	context.title = "Условия использования"
	context.logo = get_app_logo()
	context.redirect_to = назад
	context.csrf_token = frappe.sessions.get_csrf_token()
	return context
