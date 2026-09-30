# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Домашние задания к урокам (learning-services#439).

Задание — `Agent Lesson Homework`, одно на урок, от автора. Сдача —
`Agent Homework Submission`, одна живая на «задание + ученик + пространство».
Все изменения сдачи идут через этот модуль: он пишет журнал и версии, а схема
доктайпа запись разрешает только System Manager.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import frappe
from frappe.utils import getdate, now_datetime

from lms_frappe_app.agent_learning.constants import ДОМАШКА_ВЫДАНА

ЗАДАНИЕ = "Agent Lesson Homework"
СДАЧА = "Agent Homework Submission"

ПОЛЯ_ЗАДАНИЯ = ["name", "lesson", "title", "description", "answer_mode", "due_mode", "due_days", "due_date"]


def задание_урока(lesson: str) -> frappe._dict | None:
	return frappe.db.get_value(ЗАДАНИЕ, {"lesson": lesson}, ПОЛЯ_ЗАДАНИЯ, as_dict=True)


def найти_сдачу(homework: str, ученик: str, организация: str | None) -> str | None:
	return frappe.db.get_value(
		СДАЧА,
		{"homework": homework, "member": ученик, "organization": организация or ("is", "not set")},
		"name",
	)


def срок(задание, от: datetime) -> datetime | None:
	"""Срок по правилу автора. Абсолютный — конец дня по времени платформы."""
	if задание.due_mode == "absolute" and задание.due_date:
		return datetime.combine(getdate(задание.due_date), time(23, 59, 59))
	if задание.due_mode == "relative" and задание.due_days:
		return от + timedelta(days=int(задание.due_days))
	return None


def _организация_записи(запись) -> str | None:
	"""Пространство занятия; у попытки квиза — через её занятие."""
	if запись.doctype == "Agent Quiz Attempt":
		return frappe.db.get_value("Agent Learning Session", запись.session, "organization") or None
	return getattr(запись, "organization", None) or None


def выдать(запись) -> None:
	"""Выдать домашку урока при его закрытии — занятием или сданным квизом.

	Повторное закрытие и закрытие после сохранения ничего не ломают: уже
	выданная сдача не трогается, а сохранённая до закрытия получает время
	выдачи и относительный срок. `Why:` сроки не пересчитываются задним
	числом — ученик не получает просрочку за чужую правку правила.
	"""
	задание = задание_урока(запись.lesson)
	if not задание:
		return
	организация = _организация_записи(запись)
	сейчас = now_datetime()
	if имя := найти_сдачу(задание.name, запись.student, организация):
		документ = frappe.get_doc(СДАЧА, имя)
		if документ.assigned_at:
			return
	else:
		документ = frappe.get_doc(
			{
				"doctype": СДАЧА,
				"homework": задание.name,
				"lesson": запись.lesson,
				"member": запись.student,
				"organization": организация,
				"status": ДОМАШКА_ВЫДАНА,
				"version": 0,
			}
		)
	документ.assigned_at = сейчас
	if not документ.due_at:
		документ.due_at = срок(задание, сейчас)
	документ.append(
		"history",
		{
			"event": "assigned",
			"by_user": запись.student,
			"at": сейчас,
			"due_at": документ.due_at,
			"due_source": "author" if документ.due_at else None,
		},
	)
	документ.save(ignore_permissions=True)
