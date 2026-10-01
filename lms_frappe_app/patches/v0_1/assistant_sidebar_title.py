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
	for заглушка in frappe.get_all("Web Page", filters={"route": МАРШРУТ, "title": ПРЕЖНЯЯ}, pluck="name"):
		frappe.db.set_value("Web Page", заглушка, "title", НОВАЯ)
	# Копия в строке — напрямую: `fetch_from` при сохранении настроек читает
	# заголовок через кэш значений запроса и в той же транзакции вернул бы
	# прежний.
	for строка in frappe.get_all(
		"LMS Sidebar Item",
		filters={"parenttype": "LMS Settings", "route": МАРШРУТ, "title": ПРЕЖНЯЯ},
		pluck="name",
	):
		frappe.db.set_value("LMS Sidebar Item", строка, "title", НОВАЯ)
