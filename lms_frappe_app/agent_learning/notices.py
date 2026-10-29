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

Тема и текст — на языке получателя (`languages.язык_пользователя`), и
письмо собирается для каждого получателя своё (learning-services#768):
отправку зовёт задача или запрос другого человека, и язык запроса тут чужой.
Название курса — его перевод на этот язык, если он есть.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_url, getdate, nowdate

from lms_frappe_app import languages
from lms_frappe_app.agent_learning import course_translations as переводы_курса
from lms_frappe_app.agent_learning.jobs import по_одной

НАЗНАЧЕН = "assigned"
СРОК = "deadline"


def ссылка(адрес: str, подпись: str) -> str:
	"""Ссылка для письма: адрес — в атрибут через `escape_html`.

	`Why:` адрес собирается из имён документов, а письмо — HTML без шаблона,
	и экранирует его только этот вызов. Подпись — текст письма, его пишет код.
	"""
	return f'<a href="{frappe.utils.escape_html(адрес)}">{подпись}</a>'


def начало_ссылки(адрес: str) -> str:
	"""Открывающий тег ссылки — для фразы, где подпись ссылки — её часть
	(`{0}Открыть курс{1} — …`): фраза переводится целиком. Адрес — как у
	`ссылка`."""
	return f'<a href="{frappe.utils.escape_html(адрес)}">'


def абзацы(*тексты: str) -> str:
	return "".join(f"<p>{текст}</p>" for текст in тексты)


def _как_заниматься(адрес: str, язык: str) -> str:
	return _(
		"{0}Open the course{1} — lessons run with a mentor: in the browser or with your agent.", lang=язык
	).format(начало_ссылки(адрес), "</a>")


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
				with по_одной("Напоминание о сроке не отправлено", "Course Allocation", назначение.name):
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
	адрес = get_url(f"/lms/courses/{course}")
	for подписка in подписки:
		язык = languages.язык_пользователя(подписка.user)
		курс = переводы_курса.название(course, languages.основной(язык))
		# Отметка — до отправки, в той же транзакции: очередь писем Frappe
		# отправляет только закоммиченное.
		frappe.db.set_value("LMS Course Interest", подписка.name, "email_sent", 1)
		frappe.sendmail(
			recipients=[подписка.user],
			subject=_("The course “{0}” is out", lang=язык).format(курс),
			message=абзацы(
				_("The course “{0}” you asked to be notified about is now open for enrollment.", lang=язык).format(
					frappe.utils.escape_html(курс)
				),
				_как_заниматься(адрес, язык),
			),
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
	язык = languages.язык_пользователя(user)
	курс = переводы_курса.название(course, languages.основной(язык))
	адрес = get_url(f"/lms/courses/{course}")
	frappe.sendmail(
		recipients=[user],
		subject=_("You've been invited to test the course “{0}”", lang=язык).format(курс),
		message=абзацы(
			_(
				"The author has opened the course “{0}” to you before publication: it isn't in the catalog yet, and you're the first to test it.",
				lang=язык,
			).format(frappe.utils.escape_html(курс)),
			_(
				"{0}Open the course{1} — lessons run with a mentor: in the browser or with your agent. If something seems wrong or inconvenient, tell the mentor right in the lesson: they'll pass it on to the author.",
				lang=язык,
			).format(начало_ссылки(адрес), "</a>"),
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
	тема, текст = _письмо(назначение, вид, languages.язык_пользователя(адресат))
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


def _письмо(назначение, вид: str, язык: str) -> tuple[str, str]:
	"""Тема и текст письма о назначении на языке получателя. Фраза со сроком
	и без — разные msgid: переводится целое предложение."""
	курс = переводы_курса.название(назначение.course, languages.основной(язык))
	команда = (
		frappe.db.get_value("Learning Organization", назначение.organization, "organization_name")
		or назначение.organization
	)
	адрес = get_url(f"/lms/courses/{назначение.course}")
	имена = (frappe.utils.escape_html(курс), frappe.utils.escape_html(команда))
	срок = frappe.utils.escape_html(languages.дата(назначение.deadline, язык))
	if вид == СРОК:
		тема = _("The course “{0}” is due soon", lang=язык).format(курс)
		первая = (
			_(
				"The course “{0}” assigned by the team “{1}” is due in a few days. Due date: {2}.", lang=язык
			).format(*имена, срок)
			if срок
			else _("The course “{0}” assigned by the team “{1}” is due in a few days.", lang=язык).format(*имена)
		)
	else:
		тема = _("You've been assigned the course “{0}”", lang=язык).format(курс)
		первая = (
			_("The team “{1}” has assigned you the course “{0}”. Due date: {2}.", lang=язык).format(
				*имена, срок
			)
			if срок
			else _("The team “{1}” has assigned you the course “{0}”.", lang=язык).format(*имена)
		)
	return тема, абзацы(первая, _как_заниматься(адрес, язык))
