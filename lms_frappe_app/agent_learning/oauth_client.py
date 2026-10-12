# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Роли OAuth-клиентов агентов.

Агент ученика или куратора регистрирует OAuth-клиента во Frappe сам
(динамическая регистрация) и авторизуется им от имени пользователя. Frappe
при этом разрешает клиент только `Desk User`, и ученик без desk-доступа
получал на странице авторизации «Invalid client_id parameter value» —
то есть не мог подключить агента вообще. Роли платформы добавляются каждому
клиенту; `Desk User` не отнимается.
"""

РОЛИ_ПЛАТФОРМЫ = ("LMS Student", "Course Creator")


def разрешить_роли_платформы(doc, method=None) -> None:
	"""Хук `validate` OAuth Client: идёт после `add_default_role` Frappe."""
	есть = {р.role for р in doc.allowed_roles}
	for роль in РОЛИ_ПЛАТФОРМЫ:
		if роль not in есть:
			doc.append("allowed_roles", {"role": роль})

