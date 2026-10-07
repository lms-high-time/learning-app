# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Анонс курса: курс виден в каталоге, но записаться на него нельзя
(learning-services#389).

Анонс — это `published` и `upcoming` у `LMS Course` одновременно, как в самом
Learning: его вкладка каталога «Предстоящие» отбирает курсы ровно так. Наружу у
анонса выходят только цели курса: из действующей директивы, а у курса из
релиза — названия глав. Уроки и документы остаются закрытыми до выхода курса.

Подписка «сообщить, когда выйдет» — запись `LMS Course Interest` из Learning.
Её отметка `email_sent` служит журналом письма о выходе: письмо уходит один
раз, как и письма о назначениях.
"""

from __future__ import annotations

import frappe

from lms_frappe_app.agent_learning import directives


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
	"""Цели из действующей директивы курса, по строке на цель.

	Из директивы наружу выходит только это поле. Как вести курс, кого учим и
	что запоминать об ученике остаётся на сервере.

	У курса из релиза цели курса — цели глав: их названия по порядку релиза
	(learning-services#500). Директивы у такого курса нет.
	"""
	if релиз := frappe.get_cached_value("LMS Course", course, "active_release"):
		return frappe.get_all(
			"Agent Release Chapter",
			filters={"parenttype": "Agent Course Release", "parent": релиз},
			pluck="title",
			order_by="idx asc",
		)
	найденная = directives.запись("Agent Course Directive", {"course": course}, ("objectives",))
	return directives.строки(найденная.objectives) if найденная else []


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
