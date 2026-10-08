# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Методы админки: кнопки на карточках Desk, а не инструменты агента.

`Why:` сброс прогресса — действие автора и администратора над чужими данными;
в MCP ему не место (решение владельца, learning-services#313).
"""

import frappe
from lms.lms.utils import can_modify_course

from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.reset import сбросить_прогресс
from lms_frappe_app.api import контракт, текущий_пользователь

ЗАПИСЬ_НЕ_НАЙДЕНА = "enrollment_not_found"

#: Администраторы платформы. Автор курса — преподаватель курса или модератор,
#: как решает Learning (`can_modify_course`).
АДМИНИСТРАТОРЫ = frozenset({"System Manager", "Administrator"})


@frappe.whitelist(methods=["POST"])
@контракт
def reset_student_progress(enrollment: str) -> dict:
	"""Сбрасывает прогресс ученика по курсу записи — как будто он не записывался.

	Занятия, попытки квиза, документ курса, заметки по курсу, сдачи домашки и
	прохождения уроков архивируются, отметки пройденного и запись на курс
	удаляются; по назначению организации запись выдаётся заново. Отказ
	правами — не автору курса и не администратору.
	"""
	кто = текущий_пользователь()
	запись = frappe.db.get_value("LMS Enrollment", enrollment, ["member", "course"], as_dict=True)
	if not запись:
		raise Отказ(ЗАПИСЬ_НЕ_НАЙДЕНА, "Такой записи на курс нет", enrollment=enrollment)
	if not (set(frappe.get_roles(кто)) & АДМИНИСТРАТОРЫ or can_modify_course(запись.course)):
		frappe.throw(
			frappe._("Сбросить прогресс может автор курса или администратор"),
			frappe.PermissionError,
		)
	return сбросить_прогресс(запись.member, запись.course, кто)
