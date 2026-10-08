# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Сброс прогресса ученика по курсу: как будто он на курс ещё не записывался.

Решения владельца (learning-services#313):

- **Архив, а не удаление.** Занятия, попытки квиза, документ курса, заметки по
  курсу, сдачи домашки и прохождения уроков остаются в базе. Ученик
  переносится в `archived_student`, а поле «Ученик» очищается. Все запросы
  учебного потока ищут по ученику и архив не видят сами: первое занятие,
  перенос незакрытых целей, повтор урока, лимит попыток, прогресс, отчёт
  руководителя. `Why:` отметка «сброшено» потребовала бы фильтра в каждом из
  десятков мест, где читаются занятия, и пропущенное стало бы тихим багом —
  например, лимит попыток по «сброшенным» попыткам.
- **Запись на курс снимается** — курс виден как ещё не начатый. Если курс
  назначен организацией, запись выдаётся заново, как после назначения: иначе
  обязательный курс пропал бы до пересохранения назначения.
- **Репорты не трогаются.** Их видно ученику через архивное поле занятия.

Архивные записи меняются через `db.set_value`, мимо `validate`: у живых записей
«Ученик» остаётся обязательным, а архивные больше не сохраняются.

Что есть только у Learning — отметки пройденного — удаляется: своего архива у
Learning нет.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.constants import (
	ЗАНЯТИЕ_БРОШЕНО,
	ОТКРЫТЫЕ,
	ПОПЫТКА_БРОШЕНА,
	ПОПЫТКА_ИДЁТ,
)
from lms_frappe_app.agent_learning.doctype.course_allocation.course_allocation import (
	записать_зачисление,
	назначения_пользователя,
)
from lms_frappe_app.agent_learning.structure import уроки_курса


def сбросить_прогресс(ученик: str, курс: str, кто: str) -> dict:
	"""Архивирует данные ученика по курсу и снимает запись. Возвращает, что сделано."""
	момент = now_datetime()
	архив = {"student": None, "archived_student": ученик, "archived_at": момент}
	уроки = уроки_курса(курс)

	занятия = _занятия(ученик, курс, уроки)
	for имя, статус in занятия:
		изменения = dict(архив)
		# Открытое занятие закрывается: иначе его подберёт задача «брошенных»
		# и сохранит целиком — уже без ученика.
		if статус in ОТКРЫТЫЕ:
			изменения.update(status=ЗАНЯТИЕ_БРОШЕНО, finished_at=момент)
		frappe.db.set_value("Agent Learning Session", имя, изменения, update_modified=False)

	попытки = _попытки(ученик, курс, уроки, [имя for имя, _ in занятия])
	for попытка in попытки:
		изменения = dict(архив)
		if попытка.status == ПОПЫТКА_ИДЁТ:
			изменения.update(status=ПОПЫТКА_БРОШЕНА, finished_at=момент)
		frappe.db.set_value("Agent Quiz Attempt", попытка.name, изменения, update_modified=False)

	документы = frappe.get_all(
		"Agent Student Artifact", filters={"student": ученик, "course": курс}, pluck="name"
	)
	for имя in документы:
		frappe.db.set_value("Agent Student Artifact", имя, архив, update_modified=False)

	# Заметки без курса — общие факты об ученике, не прогресс по курсу.
	заметки = frappe.get_all(
		"Agent Student Note", filters={"student": ученик, "course": курс}, pluck="name"
	)
	for имя in заметки:
		frappe.db.set_value("Agent Student Note", имя, архив, update_modified=False)

	# Сдачи домашки — как занятия: повторное прохождение начинается с новой
	# сдачи, а задание со сдачами автор не удалит (learning-services#439).
	сдачи = (
		frappe.get_all(
			"Agent Homework Submission", filters={"member": ученик, "lesson": ("in", уроки)}, pluck="name"
		)
		if уроки
		else []
	)
	for имя in сдачи:
		frappe.db.set_value(
			"Agent Homework Submission",
			имя,
			{"member": None, "archived_student": ученик, "archived_at": момент},
			update_modified=False,
		)

	# Прохождения уроков (learning-services#504) — как занятия: урок после сброса
	# проходится заново, с открытыми пунктами. Уникальный индекс прохождений
	# архивное не ловит — `student` пуст.
	прохождения = frappe.get_all(
		"Agent Lesson Run", filters={"student": ученик, "course": курс}, pluck="name"
	)
	for имя in прохождения:
		frappe.db.set_value("Agent Lesson Run", имя, архив, update_modified=False)

	пройдено = frappe.get_all(
		"LMS Course Progress", filters={"member": ученик, "course": курс}, pluck="name"
	)
	for имя in пройдено:
		frappe.delete_doc("LMS Course Progress", имя, ignore_permissions=True, force=True)

	записи = frappe.get_all(
		"LMS Enrollment", filters={"member": ученик, "course": курс}, pluck="name"
	)
	for имя in записи:
		frappe.delete_doc("LMS Enrollment", имя, ignore_permissions=True, force=True)

	по_назначению = bool(назначения_пользователя(ученик, курс))
	if по_назначению:
		записать_зачисление(ученик, курс)

	итог = {
		"student": ученик,
		"course": курс,
		"sessions_archived": len(занятия),
		"attempts_archived": len(попытки),
		"artifacts_archived": len(документы),
		"notes_archived": len(заметки),
		"homework_archived": len(сдачи),
		"runs_archived": len(прохождения),
		"progress_deleted": len(пройдено),
		"enrolled_again": по_назначению,
	}
	# След в карточке ученика: кто и когда сбросил. Карточки записи на курс
	# уже нет, а у пользователя она есть всегда.
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "User",
			"reference_name": ученик,
			"content": frappe._("Прогресс по курсу {0} сброшен пользователем {1}: {2}").format(
				курс, кто, frappe.as_json(итог, indent=None)
			),
		}
	).insert(ignore_permissions=True)
	return итог


def _занятия(ученик: str, курс: str, уроки: list[str]) -> list[tuple[str, str]]:
	"""Занятия ученика по курсу. У старых занятий курс мог быть не записан —
	их находит урок."""
	найдено = {
		з.name: з.status
		for з in frappe.get_all(
			"Agent Learning Session",
			filters={"student": ученик, "course": курс},
			fields=["name", "status"],
		)
	}
	if уроки:
		for з in frappe.get_all(
			"Agent Learning Session",
			filters={"student": ученик, "lesson": ("in", уроки)},
			fields=["name", "status"],
		):
			найдено[з.name] = з.status
	return list(найдено.items())


def _попытки(ученик: str, курс: str, уроки: list[str], занятия: list[str]) -> list:
	поля = ["name", "status"]
	найдено = {
		п.name: п
		for п in frappe.get_all(
			"Agent Quiz Attempt", filters={"student": ученик, "course": курс}, fields=поля
		)
	}
	for фильтр in (
		{"session": ("in", занятия)} if занятия else None,
		{"lesson": ("in", уроки)} if уроки else None,
	):
		if фильтр:
			for п in frappe.get_all(
				"Agent Quiz Attempt", filters={"student": ученик, **фильтр}, fields=поля
			):
				найдено[п.name] = п
	return list(найдено.values())
