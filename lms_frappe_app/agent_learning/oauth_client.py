# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""OAuth-клиенты агентов: роли и чей это агент.

Агент ученика или куратора регистрирует OAuth-клиента во Frappe сам
(динамическая регистрация) и авторизуется им от имени пользователя. Frappe
при этом разрешает клиент только `Desk User`, и ученик без desk-доступа
получал на странице авторизации «Invalid client_id parameter value» —
то есть не мог подключить агента вообще. Роли платформы добавляются каждому
клиенту; `Desk User` не отнимается.
"""

from urllib.parse import urlparse

РОЛИ_ПЛАТФОРМЫ = ("LMS Student", "Course Creator")


def разрешить_роли_платформы(doc, method=None) -> None:
	"""Хук `validate` OAuth Client: идёт после `add_default_role` Frappe."""
	есть = {р.role for р in doc.allowed_roles}
	for роль in РОЛИ_ПЛАТФОРМЫ:
		if роль not in есть:
			doc.append("allowed_roles", {"role": роль})


#: Агент — по домену адреса возврата клиента. `Why:` `app_name` клиент пишет
#: о себе сам при динамической регистрации, а адрес возврата — то, куда Frappe
#: отдаёт код авторизации: чужой домен агенту не подставить, не потеряв вход.
АГЕНТЫ_ПО_ДОМЕНУ = {"claude.ai": "Claude", "chatgpt.com": "ChatGPT"}
ВЕБ_ЧАТ = "Веб-чат"
ДРУГОЙ_АГЕНТ = "Другой"


def агент_клиента(клиент: str, redirect_uris: str | None, клиент_чата: str | None) -> str:
	"""Чей OAuth-клиент: Claude, ChatGPT, веб-чат платформы или другой агент.

	`клиент_чата` — имя клиента веб-чата из конфигурации сайта. Незнакомый
	клиент — «Другой», а не ошибка: подключить коннектор может любой агент.
	"""
	if клиент_чата and клиент == клиент_чата:
		return ВЕБ_ЧАТ
	for адрес in (redirect_uris or "").split():
		хост = (urlparse(адрес).hostname or "").lower()
		for домен, агент in АГЕНТЫ_ПО_ДОМЕНУ.items():
			if хост == домен or хост.endswith("." + домен):
				return агент
	return ДРУГОЙ_АГЕНТ
