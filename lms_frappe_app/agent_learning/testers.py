# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Тестеры курса: доступ до публикации (learning-services#393).

Тестер — обычная запись на курс с отметкой `agent_tester`. Доступ к занятиям
по-прежнему даёт сама запись (`access.доступен_курс`), второго основания
доступа нет. Отметка говорит только одно: запись сделал автор из кабинета,
а не человек из каталога.

Выдаёт доступ автор или администратор из кабинета автора. Агентам этого
действия нет: ни ученическому, ни авторскому.
"""

from __future__ import annotations

import re

import frappe

from lms_frappe_app.agent_learning import notices
from lms_frappe_app.agent_learning.reset import сбросить_прогресс

#: Причины, по которым адрес не стал тестером; их показывает кабинет.
НЕТ_ПОЛЬЗОВАТЕЛЯ = "user_not_found"
УЖЕ_ТЕСТЕР = "already_tester"
УЖЕ_ЗАПИСАН = "already_enrolled"

#: Разделители в поле адресов: запятая, точка с запятой, пробелы, переводы строк.
РАЗДЕЛИТЕЛИ = re.compile(r"[\s,;]+")


def адреса(значение) -> list[str]:
	"""Адреса из списка или из текста поля, без повторов и в нижнем регистре."""
	if isinstance(значение, str):
		части = РАЗДЕЛИТЕЛИ.split(значение)
	else:
		части = [str(х) for х in (значение or [])]
	итог = []
	for часть in части:
		адрес = часть.strip().lower()
		if адрес and адрес not in итог:
			итог.append(адрес)
	return итог


def тестеры(course: str) -> list[dict]:
	"""Тестеры курса в порядке добавления: кто, с какого дня, сколько прошёл."""
	записи = frappe.get_all(
		"LMS Enrollment",
		filters={"course": course, "agent_tester": 1},
		fields=["member", "member_name", "progress", "creation"],
		order_by="creation asc",
	)
	return [
		{
			"user": з.member,
			"name": з.member_name or з.member,
			"progress": round(з.progress or 0),
			"since": з.creation.date().isoformat() if з.creation else None,
		}
		for з in записи
	]


def добавить(course: str, список_адресов: list[str]) -> dict:
	"""Записывает тестерами тех, у кого есть учётная запись.

	Кого записать нельзя — в `skipped` с причиной: учётной записи нет, человек
	уже тестер или уже учится на курсе. Уже записанного ученика тестером не
	делаем: его занятия — настоящие, и отметка их бы исказила.
	"""
	добавлены, пропущены = [], []
	for адрес in список_адресов:
		пользователь = _пользователь(адрес)
		if not пользователь:
			пропущены.append({"user": адрес, "reason": НЕТ_ПОЛЬЗОВАТЕЛЯ})
			continue
		запись = frappe.db.get_value(
			"LMS Enrollment", {"member": пользователь, "course": course}, "agent_tester"
		)
		if запись is not None:
			пропущены.append({"user": пользователь, "reason": УЖЕ_ТЕСТЕР if запись else УЖЕ_ЗАПИСАН})
			continue
		frappe.get_doc(
			{
				"doctype": "LMS Enrollment",
				"member": пользователь,
				"course": course,
				"member_type": "Student",
				"agent_tester": 1,
			}
		).insert(ignore_permissions=True)
		добавлены.append(
			{"user": пользователь, "notified": notices.пригласить_тестера(course, пользователь)}
		)
	return {"added": добавлены, "skipped": пропущены}


def убрать(course: str, user: str, кто: str) -> bool:
	"""Снимает доступ тестера тем же сбросом, что `reset_student_progress`:
	занятия и документ уходят в архив, запись удаляется. Не тестера не трогает."""
	if not frappe.db.exists("LMS Enrollment", {"member": user, "course": course, "agent_tester": 1}):
		return False
	сбросить_прогресс(user, course, кто)
	return True


def тестер(user: str, course: str) -> bool:
	"""Записан ли человек на курс тестером."""
	return bool(
		frappe.db.exists("LMS Enrollment", {"member": user, "course": course, "agent_tester": 1})
	)


def _пользователь(адрес: str) -> str | None:
	"""Действующая учётная запись по адресу: имя пользователя Frappe или его почта."""
	for поле in ("name", "email"):
		найден = frappe.db.get_value("User", {поле: адрес, "enabled": 1}, "name")
		if найден and найден not in ("Guest", "Administrator"):
			return найден
	return None
