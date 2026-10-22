# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Чтения Learning для страниц — по правилу витрины (learning-services#692).

Страницы Learning берут оглавление, урок и каталог методами `lms.lms.utils`,
открытыми и гостю. Состав курса вне витрины — неопубликованного или анонса —
открыт только тому, кто на него записан, и персоналу платформы
(`permissions.курс_открыт`); то же правило держат карта курса, вход в урок и
главы через `/api/resource`.

Методы подменяются хуком `override_whitelisted_methods`, а не правкой форка:
подмена сверяет строку вызова, и каждое HTTP-имя функции подменено (сторож —
`test_learning_reads.py`). Разрешённый вызов уходит в Learning как есть.

- Оглавление (`get_course_outline`) и урок (`get_lesson`) курса, который
  пользователю не открыт, — пустой ответ, как Learning отвечает при закрытом
  гостевом доступе.
- Каталог (`get_courses`, `get_course_count`) — только опубликованные курсы,
  анонсы в их числе: карточка анонса в каталоге и есть его витрина. Вкладка
  «мои курсы» (`enrolled`) — свои записи, как в Learning. Персоналу —
  без ограничения.

Чего не делает: внутренние вызовы этих функций из кода Learning не трогает —
подмена действует только на HTTP-имя.
"""

import frappe
from lms.lms import utils as learning

from lms_frappe_app.agent_learning import permissions


@frappe.whitelist(allow_guest=True)
def get_course_outline(course: str, progress: bool = False) -> list:
	"""Оглавление Learning — если курс пользователю открыт, иначе `[]`."""
	if not permissions.курс_открыт(course):
		return []
	return learning.get_course_outline(course, progress)


@frappe.whitelist(allow_guest=True)
def get_lesson(course: str, chapter: int, lesson: int) -> dict:
	"""Урок Learning — если курс пользователю открыт, иначе `{}`."""
	if not permissions.курс_открыт(course):
		return {}
	return learning.get_lesson(course, chapter, lesson)


@frappe.whitelist(allow_guest=True)
def get_courses(
	filters: dict | None = None, start: int = 0, limit_page_length: int | str | None = None
) -> list:
	"""Каталог Learning по фильтрам витрины (`_фильтры_каталога`)."""
	return learning.get_courses(_фильтры_каталога(filters), start, limit_page_length)


@frappe.whitelist(allow_guest=True)
def get_course_count(filters: dict | None = None) -> int:
	"""Число курсов каталога по тем же фильтрам, что `get_courses`."""
	return learning.get_course_count(_фильтры_каталога(filters))


def _фильтры_каталога(filters) -> dict:
	"""Фильтры вызывающего плюс `published`, кроме вкладки своих записей и персонала."""
	фильтры = dict(frappe.parse_json(filters) or {})
	if фильтры.get("enrolled") or permissions.курсы_без_ограничений(frappe.session.user):
		return фильтры
	фильтры["published"] = 1
	return фильтры
