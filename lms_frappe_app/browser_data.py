# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Что сайт оставляет в браузере: выход и приватные файлы (learning-services#688).

Самооценка ASVS, learning-services#673: 14.3.1 — после выхода данные сайта
остаются в хранилищах браузера, 14.3.2 — приватные файлы кэшируются.
"""

import frappe

#: Что браузер стирает по ответу на выход. Cookie не входят: свои Frappe
#: удаляет сам, а гостевую сессию ставит этим же ответом.
CLEAR_SITE_DATA = '"cache", "storage"'


def запомнить_выход(login_manager=None) -> None:
	"""`on_logout`: кто выходит — для `заголовки_ответа`.

	Хук идёт до удаления сессии, и выходящий здесь — `frappe.session.user`.
	Но тот же хук срабатывает, когда администратор отключает или удаляет
	пользователя (`User.check_enable_disable`): сессия запроса тогда остаётся
	администратора. Поэтому решение — в конце запроса, по тому, стала ли
	сессия гостевой.
	"""
	frappe.local.flags.выход_из = frappe.session.user


def заголовки_ответа(response=None, request=None) -> None:
	"""`after_request`: очистка данных сайта после выхода, приватные файлы без кэша.

	`Clear-Site-Data` — только когда вышла сама сессия запроса. `Why:` без
	него кэш страниц, IndexedDB и localStorage учебного приложения переживали
	выход; при выходе «за другого» стирать браузер администратора незачем.

	`Cache-Control: no-store` у `/private/files/`. `Why:` Frappe отдаёт их с
	`private,max-age=3600`, и браузер держит копию после выхода. Хук идёт до
	`process_response`, а тот ставит свой заголовок только при отсутствии.
	"""
	if response is None:
		return
	запрос = request or frappe.request
	if запрос is not None and запрос.path.startswith("/private/files/"):
		response.headers["Cache-Control"] = "no-store"
	вышел = frappe.local.flags.get("выход_из")
	if вышел and вышел != "Guest" and frappe.session.user == "Guest":
		response.headers["Clear-Site-Data"] = CLEAR_SITE_DATA
