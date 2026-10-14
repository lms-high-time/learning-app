# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Запись согласия с документами при самостоятельной регистрации.

Подпись «Продолжая, вы принимаете Условия использования и Политику
конфиденциальности» стоит под кнопками входа и регистрации
(`public/js/consent-notice.js`). Принимает их тот, кто создаёт аккаунт сам —
формой регистрации или первым входом через Google, где кнопка «Войти» и есть
регистрация. Обычный вход ничего не записывает: аккаунт уже есть.

Аккаунт, который создал администратор или импорт, записи не получает: человек
этих страниц не видел, и согласие за него собирает тот, кто его заводит, —
например, компания-работодатель.
"""

import frappe
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	НАСТРОЙКИ,
)

РЕГИСТРАЦИЯ = "Sign Up"
ВНЕШНИЙ_ВХОД = "Social Login"


def редакция_документов() -> str:
	"""Редакция из настроек, а пустая — умолчание схемы.

	`Why:` поле обязательное, и пустая настройка уронила бы саму регистрацию:
	запись согласия вставляется в той же транзакции, что и пользователь.
	"""
	return (
		frappe.db.get_single_value(НАСТРОЙКИ, "legal_version")
		or frappe.get_meta(НАСТРОЙКИ).get_field("legal_version").default
	)


def записать_согласие(user, method=None):
	"""`after_insert` пользователя: самостоятельная регистрация — запись согласия.

	Самостоятельная — значит, аккаунт создаётся в гостевом запросе: и
	`sign_up`, и вход через провайдера вставляют пользователя до входа под
	ним. Под администратором или в фоне (импорт, `bench`) сессия не гостевая.
	"""
	if frappe.session.user != "Guest" or user.name in ("Guest", "Administrator"):
		return
	провайдеры = [строка.provider for строка in user.get("social_logins") or [] if строка.provider]
	frappe.get_doc(
		{
			"doctype": "Agent Consent",
			"user": user.name,
			"documents_version": редакция_документов(),
			"accepted_at": now_datetime(),
			"method": ВНЕШНИЙ_ВХОД if провайдеры else РЕГИСТРАЦИЯ,
			"provider": провайдеры[0] if провайдеры else "",
		}
	).insert(ignore_permissions=True)
