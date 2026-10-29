# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Подписи пунктов сайдбара платформы — английский исходник (learning-services#767).

`Why:` подпись пункта — заголовок его заглушки Web Page, а SPA Learning
переводит её `__()` на язык пользователя: с русским заголовком перевода не
находилось, и английский ученик видел пункты по-русски. Исходник —
английский, русский — его перевод (`translations/ru.csv` и переводы
Learning), и русский сайдбар не меняется. `after_migrate` подписи не трогает,
чтобы правка админа переживала деплой, поэтому переименование — разово,
здесь. Подпись, которую админ уже сменил, остаётся как есть.
"""

import frappe

#: Маршрут заглушки → (прежняя подпись, новая).
ПОДПИСИ = {
	"study-in-browser": ("Заниматься в браузере", "Study in the browser"),
	"agent-sidebar": ("Подключить ассистента", "Connect an assistant"),
	"artifacts-sidebar": ("Мои документы", "My documents"),
	"connections-sidebar": ("Агенты и сессии", "Agents and sessions"),
}


def execute():
	for маршрут, (прежняя, новая) in ПОДПИСИ.items():
		for заглушка in frappe.get_all("Web Page", filters={"route": маршрут}, fields=["name", "title"]):
			подпись = заглушка.title
			if подпись == прежняя:
				подпись = новая
				frappe.db.set_value("Web Page", заглушка.name, "title", подпись)
			# Копия в строке обновилась бы только при следующем сохранении
			# настроек — пишем её сразу, из заголовка самой заглушки, как
			# `assistant_sidebar_title`.
			for строка in frappe.get_all(
				"LMS Sidebar Item",
				filters={"parenttype": "LMS Settings", "web_page": заглушка.name},
				fields=["name", "title"],
			):
				if строка.title != подпись:
					frappe.db.set_value("LMS Sidebar Item", строка.name, "title", подпись)
