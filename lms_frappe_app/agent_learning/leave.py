# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Уход из команды: её больше не касаются работы ушедшего (learning-services#585).

Решение владельца: команда видит документы, прогресс и домашние задания
только действующих участников. При уходе документы и сдачи домашек человека
в пространстве команды архивируются — так же, как при сбросе прогресса
(`reset.py`): автор переносится в `archived_student`, поле автора очищается,
и все экраны команды, ищущие по автору, архива не видят сами. Копию своих
документов человек получает письмом, файлами `.md`.

`Why:` перенос документа в личное пространство столкнулся бы с личным
документом по тому же курсу, а отдельный раздел «из прошлых команд» стоил бы
нового интерфейса. Вернувшийся в команду начинает её документ заново:
архив ухода не отличить от архива сброса, и восстановление подняло бы то,
что человек сбросил сам.

Занятия и попытки квиза не архивируются: прохождение урока у человека одно
на все пространства, а отчёт руководителю строится по действующим
участникам.
"""

import frappe
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.artifacts.document import выгрузка
from lms_frappe_app.agent_learning.errors import Отказ

ДОКУМЕНТ = "Agent Student Artifact"
СДАЧА = "Agent Homework Submission"


def уйти(user: str, organization: str) -> dict:
	"""Отправляет человеку копию его документов команды и архивирует их и сдачи."""
	документы = frappe.get_all(
		ДОКУМЕНТ,
		filters={"student": user, "organization": organization},
		fields=["name", "course", "artifact"],
	)
	# Файлы — до архивации: выгрузка ищет документ по автору.
	файлы = _файлы(user, organization, документы)
	if файлы:
		_письмо(user, organization, файлы)

	момент = now_datetime()
	for документ in документы:
		frappe.db.set_value(
			ДОКУМЕНТ,
			документ.name,
			{"student": None, "archived_student": user, "archived_at": момент},
			update_modified=False,
		)
	сдачи = frappe.get_all(СДАЧА, filters={"member": user, "organization": organization}, pluck="name")
	for имя in сдачи:
		frappe.db.set_value(
			СДАЧА,
			имя,
			{"member": None, "archived_student": user, "archived_at": момент},
			update_modified=False,
		)
	return {"documents": len(документы), "submissions": len(сдачи), "sent": len(файлы)}


def _файлы(user: str, organization: str, документы: list) -> list[dict]:
	"""Документы файлами `.md`; документ, чья схема снята с курса, пропускается."""
	названия = dict(
		frappe.get_all(
			"LMS Course",
			filters={"name": ("in", list({д.course for д in документы}) or [""])},
			fields=["name", "title"],
			as_list=True,
		)
	)
	файлы = []
	for документ in документы:
		try:
			имя, текст = выгрузка(user, документ.course, organization, документ.artifact, "md")
		except Отказ:
			continue
		курс = frappe.scrub(названия.get(документ.course) or документ.course)
		файлы.append({"fname": f"{курс}-{имя}", "fcontent": текст})
	return файлы


def _письмо(user: str, organization: str, файлы: list[dict]) -> None:
	команда = frappe.db.get_value("Learning Organization", organization, "organization_name") or organization
	frappe.sendmail(
		recipients=[user],
		subject=f"Ваши документы из команды «{команда}»",
		message=(
			f"<p>Вы больше не в команде «{frappe.utils.escape_html(команда)}». "
			"Команда больше не видит ваши документы, прогресс и домашние задания.</p>"
			"<p>Копия документов, которые вы собирали в её пространстве, — во вложении. "
			"Если вернётесь в команду, документ команды начнётся заново.</p>"
		),
		attachments=файлы,
		reference_doctype="Learning Organization",
		reference_name=organization,
	)
