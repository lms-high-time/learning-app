# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Данные сайта в браузере: очистка по концу сессии, приватные файлы без кэша.

ASVS 14.3.1 и 14.3.2 (learning-services#688).
"""

import frappe

#: Что браузер стирает, когда сессия кончилась. Cookie не входят: свои Frappe
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
	"""`after_request`: очистка данных сайта по концу сессии, приватные файлы без кэша.

	`Clear-Site-Data` — когда кончилась сама сессия запроса:
	- вышла (`on_logout` запомнил, и сессия стала гостевой); при выходе «за
	  другого» стирать браузер администратора незачем;
	- истекла или завершена с другого устройства: пришёл `sid`, которого уже
	  нет, — Frappe тогда ставит `session_expired` в ответ
	  (`Session.get_session_record`).
	`Why:` кэш страниц, IndexedDB и localStorage учебного приложения иначе
	переживают конец сессии.

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
	вышла_сама = вышел and вышел != "Guest" and frappe.session.user == "Guest"
	if вышла_сама or frappe.local.response.get("session_expired"):
		response.headers["Clear-Site-Data"] = CLEAR_SITE_DATA
