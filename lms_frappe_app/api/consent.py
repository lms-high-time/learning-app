# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Явное согласие с документами с экрана `/consent` (learning-services#624).

Экран видит вошедший, у которого нет явного согласия с действующей редакцией:
пришёл через Google, зарегистрировался до отметок или редакция сменилась.
Метод зовёт сам экран, агент — нет.
"""

import frappe

from lms_frappe_app.agent_learning import consent
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.api import контракт, текущий_пользователь

#: Не отмечено «мне есть 18 лет» или «принимаю документы».
НУЖНЫ_ОТМЕТКИ = "consent_marks_required"
#: Вызов не из браузера пользователя: токен агента или вход «от имени».
ТОЛЬКО_САМ = "consent_by_user_only"
#: Учётная запись сотрудника (`System User`) закрывается администратором.
СОТРУДНИК = "consent_staff_account"


def _да(значение) -> bool:
	# Флаг приходит и булевым из JSON, и строкой из формы.
	return значение in (True, 1, "1", "true")


@frappe.whitelist(methods=["POST"])
@контракт
def accept(age_confirmed=False, documents_accepted=False) -> dict:
	"""Записывает явное согласие: пользователю есть 18 лет, документы он принимает."""
	пользователь = текущий_пользователь()
	# Отметки ставит сам человек в браузере: не агент по токену и не тот, кто
	# вошёл «от имени» ученика. `Why:` иначе «явное» согласие давал бы не он.
	if frappe.get_request_header("Authorization") or consent.от_имени():
		raise Отказ(ТОЛЬКО_САМ, "Согласие даётся только самим пользователем на экране согласия")
	if not (_да(age_confirmed) and _да(documents_accepted)):
		raise Отказ(
			НУЖНЫ_ОТМЕТКИ,
			"Чтобы продолжить, подтвердите возраст и примите условия",
		)
	consent.принять(пользователь)
	return {"version": consent.редакция_документов()}


@frappe.whitelist(methods=["POST"])
@контракт
def decline_underage() -> dict:
	"""Пользователь сообщил, что ему нет 18 лет: аккаунт удаляется или закрывается.

	`result`: `deleted` — аккаунт и контакт удалены; `closed` — на аккаунт
	ссылаются данные, он закрыт, данные удалит администратор. Сессии
	завершаются в обоих случаях.
	"""
	пользователь = текущий_пользователь()
	if frappe.get_request_header("Authorization") or consent.от_имени():
		raise Отказ(ТОЛЬКО_САМ, "Сообщить о возрасте может только сам пользователь на экране согласия")
	if frappe.db.get_value("User", пользователь, "user_type") != "Website User":
		raise Отказ(
			СОТРУДНИК, "Учётную запись сотрудника закрывает администратор: напишите на privacy@cenda.app"
		)
	return {"result": consent.убрать_аккаунт(пользователь, consent.НЕТ_18)}
