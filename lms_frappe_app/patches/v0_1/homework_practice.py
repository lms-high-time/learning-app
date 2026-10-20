# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Признак обязательности у заведённых сдач домашки (learning-services#627).

Обязательная — сдача в пространстве организации, которая сама назначила
ученику курс (`homework.назначения_организации`); остальное — практика. Патч
ставит признак `required` обязательным и снимает срок у практики — с самой
сдачи и из строк её журнала, где он был записан при выдаче. `Why:` практика
идёт без срока, без «просрочено» и без напоминаний, и уже выданная — тоже:
так решил владелец.

Архивная сдача (сброс прогресса) считается по ученику до сброса. Повторный
запуск ничего не меняет: признак обязательной уже стоит, у практики срока нет.
"""

import frappe

from lms_frappe_app.agent_learning import homework as домашка

СОБЫТИЕ = "Agent Homework Event"


def execute():
	сдачи = frappe.get_all(
		домашка.СДАЧА,
		fields=["name", "lesson", "member", "archived_student", "organization", "required", "due_at"],
	)
	if not сдачи:
		print("homework_practice: сдач нет — делать нечего")
		return
	курсы = dict(
		frappe.get_all(
			"Course Lesson",
			filters={"name": ("in", list({с.lesson for с in сдачи}))},
			fields=["name", "course"],
			as_list=True,
		)
	)
	решено: dict[tuple, bool] = {}
	обязательные, практика = [], []
	for сдача in сдачи:
		ученик = сдача.member or сдача.archived_student
		курс = курсы.get(сдача.lesson)
		ключ = (ученик, курс, сдача.organization)
		if ключ not in решено:
			решено[ключ] = bool(
				сдача.organization
				and ученик
				and курс
				and домашка.назначения_организации(ученик, курс, сдача.organization)
			)
		if решено[ключ]:
			if not сдача.required:
				обязательные.append(сдача.name)
		elif сдача.required or сдача.due_at:
			практика.append(сдача.name)
	if обязательные:
		frappe.db.set_value(
			домашка.СДАЧА, {"name": ("in", обязательные)}, "required", 1, update_modified=False
		)
	if практика:
		frappe.db.set_value(
			домашка.СДАЧА,
			{"name": ("in", практика)},
			{"required": 0, "due_at": None},
			update_modified=False,
		)
		frappe.db.set_value(
			СОБЫТИЕ,
			{"parenttype": домашка.СДАЧА, "parent": ("in", практика)},
			{"due_at": None, "due_source": None},
			update_modified=False,
		)
	print(f"homework_practice: обязательных отмечено {len(обязательные)}, практике снят срок у {len(практика)}")
