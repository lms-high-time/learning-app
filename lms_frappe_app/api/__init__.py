# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Контракт приложения — методы этого пакета (`CONTRACT.md`).

Они и есть граница лицензии — контракт общего назначения, который в принципе
мог бы реализовать другой backend. Внутренние структуры Frappe через него не
текут: `id` вместо `name`, никаких `doctype`, `docstatus` и `owner`.
`/api/resource` Frappe отвечает тому же токену, но контрактом не является: его
держат права DocType — раздел «Сырой REST» контракта.
"""

import functools
import json
from collections.abc import Callable

import frappe

from lms_frappe_app import security_log
from lms_frappe_app.agent_learning.errors import Отказ


def список(значение) -> list:
	"""Аргумент, который мог приехать строкой JSON.

	`Why:` Frappe отдаёт тело запроса как форму, и список превращается в
	строку. Без разбора метод получал бы строку и молча считал её
	посимвольно.
	"""
	if isinstance(значение, str):
		значение = json.loads(значение)
	return list(значение or [])


def успех(данные: dict | None = None) -> dict:
	return {"ok": True, "data": данные or {}}


def отказ(код: str, сообщение: str, **подробности) -> dict:
	return {"ok": False, "error": {"code": код, "message": сообщение, **подробности}}


def контракт(метод: Callable) -> Callable:
	"""Оборачивает результат метода в форму контракта.

	Ожидаемые отказы возвращаются успешным HTTP с машинным кодом. Ошибки прав
	наверх не перехватываются: чужая сессия обязана отклоняться правами
	Frappe, а не нашей проверкой, и агент должен увидеть именно 403.

	Отказ о доступе пишется в журнал событий безопасности: ответ успешный, и
	иначе его не видно нигде (`security_log`).
	"""

	@functools.wraps(метод)
	def обёртка(*args, **kwargs):
		try:
			return успех(метод(*args, **kwargs))
		except Отказ as причина:
			security_log.отказ_контракта(метод, причина.код, причина.подробности)
			return отказ(причина.код, причина.сообщение, **причина.подробности)

	return обёртка


def текущий_пользователь() -> str:
	"""Пользователь, от имени которого пришёл вызов."""
	пользователь = frappe.session.user
	if not пользователь or пользователь == "Guest":
		frappe.throw(frappe._("Требуется вход"), frappe.AuthenticationError)
	return пользователь
