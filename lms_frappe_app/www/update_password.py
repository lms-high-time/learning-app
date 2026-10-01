# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Страница «Задайте пароль» вместо страницы Frappe (learning-services#460).

`Why:` страница Frappe проверяет ссылку только после ввода пароля, об успехе
говорит окном на две секунды, а без ключа превращается в смену пароля по
старому — её принимали за продолжение сброса (learning-services#459). Frappe
ищет страницы в обратном порядке установки приложений, поэтому эта
перекрывает его собственную.
"""

import frappe
from frappe.core.doctype.navbar_settings.navbar_settings import get_app_logo

from lms_frappe_app.access import КЛЮЧ_ДЕЙСТВУЕТ, состояние_ключа

no_cache = 1

#: Без ключа задавать пароль нечем — ведём туда, где его просят.
ВОССТАНОВЛЕНИЕ = "/login#forgot"


def сведения(key: str) -> dict:
	состояние = состояние_ключа(key)
	return {"state": состояние, "key": key if состояние == КЛЮЧ_ДЕЙСТВУЕТ else ""}


def get_context(context):
	key = frappe.form_dict.get("key")
	if not key:
		frappe.local.flags.redirect_location = ВОССТАНОВЛЕНИЕ
		raise frappe.Redirect
	context.no_breadcrumbs = True
	context.title = "Задайте пароль"
	context.logo = get_app_logo()
	context.password_expired = bool(frappe.form_dict.get("password_expired"))
	# Гостевые вызовы токен не проверяют; вошедшему без него POST отклонят.
	context.csrf_token = frappe.sessions.get_csrf_token() if frappe.session.user != "Guest" else ""
	context.update(сведения(key))
	return context
