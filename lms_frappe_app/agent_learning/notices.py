# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Письма по назначениям: о новом курсе и о приближении срока (learning-services#365).

Руководитель назначает курс на странице «Команда», а сотрудник об этом
узнаёт письмом: без него назначение дожидалось бы, пока человек сам откроет
платформу, и отчёт показывал бы просрочки, о которых сотрудник не знал.

Каждое письмо уходит человеку по назначению один раз — это держит журнал
`Allocation Notice`. На курс, который сотрудник взял сам из каталога
организации, писем нет: он о нём знает.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, get_url, getdate, nowdate

from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	настройка,
)

НАЗНАЧЕН = "assigned"
СРОК = "deadline"

#: Напомнить о сроке за столько дней, если настройка пуста.
ДНЕЙ_ДО_СРОКА = 3


def уведомить_о_назначении(назначение, адресаты: list[str]) -> int:
	"""Письмо о новом назначении тем адресатам, кому оно ещё не уходило."""
	if назначение.chosen_by_member:
		return 0
	отправлено = 0
	for адресат in адресаты:
		отправлено += _отправить(назначение, адресат, НАЗНАЧЕН)
	return отправлено


def напомнить_о_сроках() -> int:
	"""Ежедневная задача: напоминание тем, кто не прошёл курс, а срок близко.

	Срок уже прошёл — не напоминаем: письмо «срок через −2 дня» только злит, а
	просрочку видит руководитель в отчёте.
	"""
	from lms_frappe_app.agent_learning.doctype.course_allocation.course_allocation import (
		адресаты_назначений,
	)

	дней = настройка("deadline_reminder_days", ДНЕЙ_ДО_СРОКА)
	if not дней:
		return 0
	сегодня = getdate(nowdate())
	назначения = frappe.get_all(
		"Course Allocation",
		filters={
			"deadline": ("between", [сегодня, add_days(сегодня, дней)]),
			"chosen_by_member": 0,
		},
		fields=["name", "organization", "course", "audience", "deadline", "chosen_by_member"],
	)
	if not назначения:
		return 0
	адресаты = адресаты_назначений(назначения)
	отправлено = 0
	for назначение in назначения:
		прошли = set(
			frappe.get_all(
				"LMS Enrollment",
				filters={"course": назначение.course, "progress": (">=", 100)},
				pluck="member",
			)
		)
		for адресат in адресаты[назначение.name]:
			if адресат not in прошли:
				отправлено += _отправить(назначение, адресат, СРОК)
	return отправлено


def почта_есть() -> bool:
	"""Настроена ли исходящая почта по умолчанию.

	`Why:` без неё `frappe.sendmail` бросает исключение, и назначение не
	сохранилось бы вовсе. Нет почты — назначение живёт, письмо не уходит и в
	журнал не пишется: уйдёт при следующем сохранении, когда почту настроят.
	"""
	return bool(
		frappe.db.exists("Email Account", {"default_outgoing": 1, "enable_outgoing": 1})
	)


def _отправить(назначение, адресат: str, вид: str) -> int:
	"""Письмо одно на «назначение + человек + вид»; уже ушедшее — не повторяется."""
	if not почта_есть():
		return 0
	if frappe.db.exists(
		"Allocation Notice", {"allocation": назначение.name, "user": адресат, "kind": вид}
	):
		return 0
	тема, текст = _письмо(назначение, вид)
	# Запись — до отправки, в той же транзакции: не записалось — письмо не
	# уйдёт, очередь писем Frappe отправляет только закоммиченное.
	frappe.get_doc(
		{"doctype": "Allocation Notice", "allocation": назначение.name, "user": адресат, "kind": вид}
	).insert(ignore_permissions=True)
	frappe.sendmail(
		recipients=[адресат],
		subject=тема,
		message=текст,
		reference_doctype="Course Allocation",
		reference_name=назначение.name,
	)
	return 1


def _письмо(назначение, вид: str) -> tuple[str, str]:
	курс = frappe.db.get_value("LMS Course", назначение.course, "title") or назначение.course
	компания = (
		frappe.db.get_value("Learning Organization", назначение.organization, "organization_name")
		or назначение.organization
	)
	адрес = get_url(f"/lms/courses/{назначение.course}")
	срок = (
		f" Срок — {frappe.utils.formatdate(назначение.deadline, 'dd.MM.yyyy')}."
		if назначение.deadline
		else ""
	)
	if вид == СРОК:
		тема = f"Скоро срок курса «{курс}»"
		первая = f"До срока курса «{курс}», который назначила «{компания}», осталось несколько дней.{срок}"
	else:
		тема = f"Вам назначен курс «{курс}»"
		первая = f"«{компания}» назначила вам курс «{курс}».{срок}"
	текст = (
		f"<p>{frappe.utils.escape_html(первая)}</p>"
		f'<p><a href="{адрес}">Открыть курс</a> — занятие идёт с наставником: в браузере '
		f"или с вашим агентом.</p>"
	)
	return тема, текст
