# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Тексты на языке читателя: даты (learning-services#767).

У приложения два языка интерфейса — русский и английский, — и всё, что не
русское, показывается по-английски, как у даты редакции на экране согласия
(`consent.дата_редакции`). Язык — явный (письмо: язык получателя) или языка
запроса (`frappe.local.lang`).
"""

import frappe
from babel.dates import format_date, format_time
from frappe.utils import get_datetime, getdate

РУССКИЙ = "ru"

#: Русская запись — прежняя, цифрами: страницы и письма её не меняют.
ДАТА_РУ = "dd.MM.yyyy"
ВРЕМЯ_РУ = "HH:mm"


def основной(язык: str | None = None) -> str:
	"""Язык без варианта: `en-US` → `en`; без языка — язык запроса."""
	return (язык or getattr(frappe.local, "lang", None) or "en").split("-")[0].lower()


def русский(язык: str | None = None) -> bool:
	return основной(язык) == РУССКИЙ


def дата(значение, язык: str | None = None) -> str:
	"""«29.10.2026» по-русски, «Oct 29, 2026» по-английски; пусто — пустая строка."""
	if not значение:
		return ""
	день = getdate(значение)
	if русский(язык):
		return format_date(день, ДАТА_РУ, locale=РУССКИЙ)
	return format_date(день, "medium", locale="en")


def дата_время(значение, язык: str | None = None) -> str:
	"""«29.10.2026 14:05» по-русски, «Oct 29, 2026, 2:05 PM» по-английски.

	Время — как записано, в поясе сайта: пересчёта пояса нет, как и у
	`frappe.utils.format_datetime`.
	"""
	if not значение:
		return ""
	момент = get_datetime(значение).replace(tzinfo=None)
	if русский(язык):
		return (
			f"{format_date(момент, ДАТА_РУ, locale=РУССКИЙ)} {format_time(момент, ВРЕМЯ_РУ, locale=РУССКИЙ)}"
		)
	return f"{format_date(момент, 'medium', locale='en')}, {format_time(момент, 'short', locale='en')}"
