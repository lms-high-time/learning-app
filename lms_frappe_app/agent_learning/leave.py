# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Уход из команды: копия документов — ушедшему (learning-services#585).

Решение владельца: команда видит документы, прогресс и домашние задания
только действующих участников; при уходе документы никуда не переносятся —
остаются в базе, команда их не видит, вернулся в команду — снова видны.
Видимость решают права по действующему членству (`permissions`), а здесь
ушедший получает письмом копию своих документов пространства команды,
файлами `.md`: из интерфейса пространство ушедшей команды ему не открыть.

`Why:` перенос документа в личное пространство столкнулся бы с личным
документом по тому же курсу, а отдельный раздел «из прошлых команд» стоил бы
нового интерфейса.
"""

import re

import frappe

from lms_frappe_app.agent_learning.artifacts.document import выгрузка
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.notices import почта_есть

#: Ошибки сборки одного документа, после которых уход продолжается. Ошибки
#: базы сюда не входят: после взаимоблокировки транзакция уже испорчена.
ОШИБКИ_ВЫГРУЗКИ = (Отказ, frappe.ValidationError, ValueError, KeyError, TypeError)
ТОЧКА_ПИСЬМА = "leave_copy"

ДОКУМЕНТ = "Agent Student Artifact"


def уйти(user: str, organization: str) -> int:
	"""Отправляет ушедшему копию его документов команды; возвращает число файлов.

	Нет почты — письма нет, уход не останавливается: документы остаются в
	базе, а вернувшийся в команду увидит их снова.
	"""
	документы = frappe.get_all(
		ДОКУМЕНТ,
		filters={"student": user, "organization": organization},
		fields=["course", "artifact"],
	)
	if not документы or not почта_есть():
		return 0
	файлы, пропущено = _файлы(user, organization, документы)
	_письмо(user, organization, файлы, пропущено)
	return len(файлы)


def _файлы(user: str, organization: str, документы: list) -> tuple[list[dict], int]:
	"""Документы файлами `.md` и сколько выгрузить не удалось.

	Схему документа могли снять с курса, данные — испортить: такой документ
	пропускается, а не роняет уход. В письме сказано, что его нет.
	"""
	названия = dict(
		frappe.get_all(
			"LMS Course",
			filters={"name": ("in", list({д.course for д in документы}))},
			fields=["name", "title"],
			as_list=True,
		)
	)
	файлы, пропущено = [], 0
	for документ in документы:
		try:
			имя, текст = выгрузка(user, документ.course, organization, документ.artifact, "md")
		except ОШИБКИ_ВЫГРУЗКИ:
			frappe.log_error(
				"Копия документа ушедшему не собрана",
				reference_doctype="LMS Course",
				reference_name=документ.course,
			)
			пропущено += 1
			continue
		курс = re.sub(r"[^\w.-]+", "_", названия.get(документ.course) or документ.course).strip("_")
		файлы.append({"fname": f"{курс}-{имя}", "fcontent": текст})
	return файлы, пропущено


def _письмо(user: str, organization: str, файлы: list[dict], пропущено: int) -> None:
	название = frappe.db.get_value("Learning Organization", organization, "organization_name") or organization
	команда = frappe.utils.escape_html(название)
	недостаёт = (
		"<p>Некоторые документы собрать не удалось — напишите нам, пришлём их отдельно.</p>"
		if пропущено
		else ""
	)
	# Письмо не держит уход: сбой постановки в очередь (адрес, ящик) — запись
	# в журнал ошибок, а не откат ухода.
	frappe.db.savepoint(ТОЧКА_ПИСЬМА)
	try:
		_поставить(user, organization, название, команда, файлы, недостаёт)
	except (frappe.OutgoingEmailError, frappe.ValidationError):
		frappe.db.rollback(save_point=ТОЧКА_ПИСЬМА)
		frappe.clear_last_message()
		frappe.log_error(
			"Копия документов ушедшему не отправлена",
			reference_doctype="Learning Organization",
			reference_name=organization,
		)


def _поставить(
	user: str, organization: str, название: str, команда: str, файлы: list[dict], недостаёт: str
) -> None:
	frappe.sendmail(
		recipients=[user],
		subject=f"Ваши документы из команды «{название}»",
		message=(
			f"<p>Вы больше не в команде «{команда}». Команда больше не видит ваши документы, "
			"прогресс и домашние задания.</p>"
			+ (
				"<p>Копия документов, которые вы собирали в её пространстве, — во вложении.</p>"
				if файлы
				else ""
			)
			+ f"{недостаёт}"
			+ "<p>Если вернётесь в команду, документы снова будут на месте.</p>"
		),
		attachments=файлы,
		reference_doctype="Learning Organization",
		reference_name=organization,
	)
