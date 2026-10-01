# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Пункт сайдбара «Подключить агента» — «Подключить ассистента» (learning-services#471).

`Why:` подпись пункта — заголовок его заглушки Web Page, и строка сайдбара
хранит его копию (`fetch_from`). `after_migrate` подписи не трогает, чтобы
правка админа переживала деплой, поэтому переименование — разово, здесь.
Подпись, которую админ уже сменил, остаётся как есть.
"""

import frappe

МАРШРУТ = "agent-sidebar"
ПРЕЖНЯЯ = "Подключить агента"
НОВАЯ = "Подключить ассистента"


def execute():
	for заглушка in frappe.get_all("Web Page", filters={"route": МАРШРУТ}, fields=["name", "title"]):
		подпись = заглушка.title
		if подпись == ПРЕЖНЯЯ:
			подпись = НОВАЯ
			frappe.db.set_value("Web Page", заглушка.name, "title", подпись)
		# Копия в строке обновилась бы только при следующем сохранении настроек —
		# пишем её сразу, из заголовка самой заглушки: так строка не разойдётся
		# и с подписью, которую админ задал, но настроек после не сохранял.
		for строка in frappe.get_all(
			"LMS Sidebar Item",
			filters={"parenttype": "LMS Settings", "web_page": заглушка.name},
			fields=["name", "title"],
		):
			if строка.title != подпись:
				frappe.db.set_value("LMS Sidebar Item", строка.name, "title", подпись)
