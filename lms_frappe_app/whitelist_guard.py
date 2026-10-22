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
"""

import frappe


def снять_подменённые(*args, **kwargs) -> None:
	"""Убирает из реестров whitelisted-методов оригиналы всех подменённых функций."""
	for исходное, подмены in frappe.get_hooks("override_whitelisted_methods").items():
		try:
			функция = frappe.get_attr(исходное)
		except Exception:
			# Подмена метода, которого в установке нет, — снимать нечего.
			continue
		if подмены and функция is frappe.get_attr(подмены[-1]):
			continue
		frappe.whitelisted.discard(функция)
		frappe.guest_methods.discard(функция)
		frappe.xss_safe_methods.discard(функция)
		frappe.allowed_http_methods_for_whitelisted_func.pop(функция, None)
