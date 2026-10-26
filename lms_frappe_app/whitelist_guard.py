# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Подменённые методы — только под своим HTTP-именем (learning-services#692).

У каждой функции, подменённой `override_whitelisted_methods`, оригинал снят
из реестров Frappe (`frappe.whitelisted`, `frappe.guest_methods`,
`frappe.xss_safe_methods`): под исходным именем запрос уходит в подмену, под
другим путём модуля — отказ «не whitelisted». `Why:` Frappe сверяет с реестром
объект функции, а подмена — строку вызова. Подмены зовут оригинал прямым
вызовом Python, реестр им не нужен.

Реестр живёт в памяти процесса и заполняется при импорте модулей, поэтому
снятие идёт на каждом запросе и задаче (`before_request`, `before_job`): оно
идемпотентно и сначала импортирует модуль оригинала — иначе тот
зарегистрировался бы уже после снятия.

Чего не делает: не разделяет сайты. Реестр общий на процесс, и на bench с
несколькими сайтами, где у части приложение не стоит, снятие задело бы и их:
там исходный метод перестал бы отвечать. На стенде cenda.app сайт один.
"""

from collections.abc import Callable

import frappe


def снять_подменённые(*args, **kwargs) -> None:
	"""Убирает из реестров whitelisted-методов оригиналы всех подменённых функций.

	Функцию, которая сама — цель какой-нибудь подмены, не трогает: иначе
	цепочка подмен (A → B, B → C) лишилась бы рабочего метода.
	"""
	цели, оригиналы = set(), []
	for исходное, подмены in frappe.get_hooks("override_whitelisted_methods").items():
		if подмены and (цель := _функция(подмены[-1])) is not None:
			цели.add(цель)
		if (оригинал := _функция(исходное)) is not None:
			оригиналы.append(оригинал)
	for функция in оригиналы:
		if функция in цели:
			continue
		frappe.whitelisted.discard(функция)
		frappe.guest_methods.discard(функция)
		frappe.xss_safe_methods.discard(функция)
		frappe.allowed_http_methods_for_whitelisted_func.pop(функция, None)


def _функция(путь: str) -> Callable | None:
	"""Функция по пути или `None`, если её в установке нет.

	Путь без точки Frappe ищет в `frappe.handler` (`handler.get_attr`), так же
	ищется и здесь.

	`frappe.get_attr` отказывает через `frappe.throw`, и сообщение осталось бы в
	ответе запроса — его убираем.
	"""
	if "." not in путь:
		from frappe import handler

		return getattr(handler, путь, None)
	журнал = getattr(frappe.local, "message_log", None)
	было = len(журнал) if журнал is not None else 0
	try:
		return frappe.get_attr(путь)
	except Exception:
		if журнал is not None:
			del журнал[было:]
		return None
