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

from lms_frappe_app.access import КЛЮЧ_ДЕЙСТВУЕТ, запись_ключа, состояние_ключа

no_cache = 1

#: Гостю без ключа задавать пароль нечем — ведём туда, где ссылку просят.
ВОССТАНОВЛЕНИЕ = "/login#forgot"
#: Вошедший без ключа просит ссылку здесь же: `/login` увёл бы его на главную.
ЗАПРОС_ССЫЛКИ = "request"


def сведения(key: str) -> dict:
	"""Что показать по ключу. Адрес — для менеджера паролей: без логина он
	сохранит пароль ни к чему."""
	состояние = состояние_ключа(key)
	if состояние != КЛЮЧ_ДЕЙСТВУЕТ:
		return {"state": состояние, "key": "", "email": ""}
	return {"state": состояние, "key": key, "email": _почта(запись_ключа(key).name)}


def _почта(пользователь: str) -> str:
	return frappe.db.get_value("User", пользователь, "email") or ""


def get_context(context):
	key = frappe.form_dict.get("key")
	вошедший = frappe.session.user != "Guest"
	if not key and not вошедший:
		frappe.local.flags.redirect_location = ВОССТАНОВЛЕНИЕ
		raise frappe.Redirect
	context.no_breadcrumbs = True
	context.title = "Задайте пароль"
	context.logo = get_app_logo()
	context.password_expired = bool(frappe.form_dict.get("password_expired"))
	# Гостевые вызовы токен не проверяют; вошедшему без него POST отклонят.
	context.csrf_token = frappe.sessions.get_csrf_token() if вошедший else ""
	if key:
		context.update(сведения(key))
	else:
		# Ссылка «Reset Password» на `/me` Frappe ведёт сюда без ключа.
		context.update({"state": ЗАПРОС_ССЫЛКИ, "key": "", "email": _почта(frappe.session.user)})
	return context
