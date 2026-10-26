# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Версии приложений сайта — только тому, кому открыт Desk (learning-services#692).

`frappe.utils.change_log.get_versions` зовёт окно «О программе» в Desk, и
Frappe отдаёт его любому вошедшему. Ученику и руководителю организации точные
версии Frappe и приложений ни к чему. Метод подменяется хуком
`override_whitelisted_methods`: сотрудник с Desk (`System User`) получает ответ
Frappe как есть, остальные — отказ в правах. Сведения о версиях в загрузке
Desk (`boot`) Frappe собирает прямым вызовом, мимо подмены, и только для Desk.
"""

import frappe
from frappe.utils import change_log


@frappe.whitelist()
def get_versions() -> dict:
	"""Ответ Frappe для пользователя Desk, иначе `PermissionError`."""
	if frappe.get_cached_value("User", frappe.session.user, "user_type") != "System User":
		frappe.throw(frappe._("Not permitted"), frappe.PermissionError)
	return change_log.get_versions()
