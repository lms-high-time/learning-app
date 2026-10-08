# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Анонс курса: курс виден в каталоге, но записаться на него нельзя
(learning-services#389).

Анонс — это `published` и `upcoming` у `LMS Course` одновременно, как в самом
Learning: его вкладка каталога «Предстоящие» отбирает курсы ровно так. Наружу у
анонса выходят только цели курса: у курса из релиза — названия глав
действующего релиза, у курса без релиза — поле `announce_objectives`
(learning-services#512). Уроки и документы остаются закрытыми до выхода курса.

Подписка «сообщить, когда выйдет» — запись `LMS Course Interest` из Learning.
Её отметка `email_sent` служит журналом письма о выходе: письмо уходит один
раз, как и письма о назначениях.
"""

from __future__ import annotations

import json

import frappe

#: Поле курса с целями анонса: текст, по строке на цель.
ПОЛЕ_ЦЕЛЕЙ = "announce_objectives"


def анонсирован(course: str) -> bool:
	"""Курс виден в каталоге как анонс."""
	сведения = frappe.db.get_value("LMS Course", course, ["published", "upcoming"], as_dict=True)
	return bool(сведения and сведения.published and сведения.upcoming)


def анонсы(courses: list[str]) -> set[str]:
	"""Какие из курсов — анонсы, одним запросом на весь список."""
	if not courses:
		return set()
	return set(
		frappe.get_all(
			"LMS Course",
			filters={"name": ("in", courses), "published": 1, "upcoming": 1},
			pluck="name",
		)
	)


def цели_курса(course: str) -> list[str]:
	"""Цели курса по порядку: главы действующего релиза, иначе цели анонса.

	У курса из релиза цели — названия его глав по порядку релиза
	(learning-services#500); поле анонса у него не читается.
	"""
	релиз, цели = frappe.get_cached_value("LMS Course", course, ["active_release", ПОЛЕ_ЦЕЛЕЙ]) or (None, None)
	if релиз:
		return frappe.get_all(
			"Agent Release Chapter",
			filters={"parenttype": "Agent Course Release", "parent": релиз},
			pluck="title",
			order_by="idx asc",
		)
	return строки(цели)


def строки(значение) -> list[str]:
	"""Цели — списком: из списка, строки JSON со списком или текста по строке на цель.

	Пустые строки и пробелы по краям отбрасываются; элемент списка с
	переводами строк — несколько целей: поле хранит цель на строку. Строка
	JSON — потому что Frappe отдаёт тело формы строками.
	"""
	if isinstance(значение, str) and значение.strip().startswith("["):
		# Текст, который лишь начинается со скобки, — тоже цели, а не ошибка.
		try:
			значение = json.loads(значение)
		except ValueError:
			pass
	if isinstance(значение, str):
		части = [значение]
	else:
		части = [str(часть) for часть in (значение or []) if часть is not None]
	return [строка.strip() for часть in части for строка in часть.splitlines() if строка.strip()]


def подписан(user: str, course: str) -> bool:
	"""Ждёт ли человек письма о выходе курса."""
	return bool(frappe.db.exists("LMS Course Interest", {"user": user, "course": course}))


def подписки(user: str, courses: list[str]) -> set[str]:
	"""На какие из курсов человек подписан, одним запросом."""
	if not courses:
		return set()
	return set(
		frappe.get_all(
			"LMS Course Interest",
			filters={"user": user, "course": ("in", courses)},
			pluck="course",
		)
	)


def подписать(user: str, course: str) -> None:
	"""Подписывает на письмо о выходе. Повторная подписка ничего не меняет."""
	if подписан(user, course):
		return
	frappe.get_doc({"doctype": "LMS Course Interest", "user": user, "course": course}).insert(
		ignore_permissions=True
	)


def отписать(user: str, course: str) -> None:
	"""Снимает подписку на письмо о выходе. Нет подписки — ничего не меняет."""
	frappe.db.delete("LMS Course Interest", {"user": user, "course": course})
