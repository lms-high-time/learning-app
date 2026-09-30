# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Письма по назначениям: о новом курсе и о приближении срока (learning-services#365),
письмо о выходе анонсированного курса (learning-services#389) и приглашение
тестеру курса (learning-services#393).

Руководитель назначает курс на странице «Команда», а сотрудник об этом
узнаёт письмом: без него назначение дожидалось бы, пока человек сам откроет
платформу, и отчёт показывал бы просрочки, о которых сотрудник не знал.

Каждое письмо уходит человеку по назначению один раз — это держит журнал
`Allocation Notice`. На курс, который сотрудник взял сам из каталога
организации, писем нет: он о нём знает.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, cint, get_url, getdate, nowdate

НАЗНАЧЕН = "assigned"
СРОК = "deadline"


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

	дней = дней_напоминания()
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


def дней_напоминания() -> int:
	"""За сколько дней до срока напоминать — о курсе и о домашке; `0` — не напоминать.

	Поле читается напрямую, а не через `настройка()`. `Why:` `настройка()`
	заменяет пустое и `0` запасным значением, а `0` здесь — осмысленное «не
	напоминать» (learning-services#452). Пустым поле не бывает: у него
	значение по умолчанию.
	"""
	return cint(frappe.db.get_single_value("Agent Learning Settings", "deadline_reminder_days", cache=False))


def уведомить_о_выходе(course: str) -> int:
	"""Письмо о выходе анонсированного курса всем, кто просил сообщить.

	Журнал — отметка `email_sent` у подписки: второй выход того же курса
	(сняли и открыли снова) писем не повторяет. Письмо Learning для этого не
	годится: его тема не переводится. Почты нет — подписки остаются
	неотмеченными, писем не будет.
	"""
	if not почта_есть():
		return 0
	подписки = frappe.get_all(
		"LMS Course Interest",
		filters={"course": course, "email_sent": 0},
		fields=["name", "user"],
	)
	if not подписки:
		return 0
	курс = frappe.db.get_value("LMS Course", course, "title") or course
	адрес = get_url(f"/lms/courses/{course}")
	тема = f"Курс «{курс}» вышел"
	текст = (
		f"<p>Курс «{frappe.utils.escape_html(курс)}», о выходе которого вы просили сообщить, "
		f"открыт для записи.</p>"
		f'<p><a href="{адрес}">Открыть курс</a> — занятие идёт с наставником: в браузере '
		f"или с вашим агентом.</p>"
	)
	for подписка in подписки:
		# Отметка — до отправки, в той же транзакции: очередь писем Frappe
		# отправляет только закоммиченное.
		frappe.db.set_value("LMS Course Interest", подписка.name, "email_sent", 1)
		frappe.sendmail(
			recipients=[подписка.user],
			subject=тема,
			message=текст,
			reference_doctype="LMS Course",
			reference_name=course,
		)
	return len(подписки)


def пригласить_тестера(course: str, user: str) -> bool:
	"""Письмо человеку, которого автор сделал тестером курса (learning-services#393).

	Без него тестер не узнал бы, что курс ему открыт: неопубликованного курса
	нет в каталоге. Возвращает, ушло ли письмо; почты нет — кабинет скажет
	автору сообщить самому.
	"""
	if not почта_есть():
		return False
	курс = frappe.db.get_value("LMS Course", course, "title") or course
	адрес = get_url(f"/lms/courses/{course}")
	frappe.sendmail(
		recipients=[user],
		subject=f"Вас пригласили проверить курс «{курс}»",
		message=(
			f"<p>Автор открыл вам курс «{frappe.utils.escape_html(курс)}» до публикации: "
			f"его ещё нет в каталоге, и вы проверяете его первым.</p>"
			f'<p><a href="{адрес}">Открыть курс</a> — занятие идёт с наставником: в браузере '
			f"или с вашим агентом. Что показалось неверным или неудобным, скажите наставнику "
			f"прямо на занятии: он передаст это автору.</p>"
		),
		reference_doctype="LMS Course",
		reference_name=course,
	)
	return True


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
