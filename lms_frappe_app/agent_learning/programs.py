# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Программы Learning — цепочки курсов в заданном порядке (learning-services#405).

Программу, её курсы и участников хранит сам Learning (`LMS Program`,
`LMS Program Course`, `LMS Program Member`), а с флажком
`enforce_course_order` следующий курс открывается только после предыдущего.
Learning держит этот порядок одним интерфейсом страницы программы: запись на
курс и занятия его не видят, и агент повёл бы по второму курсу, не пройдя
первый. Здесь то же правило проверяется на сервере.

Порядок действует только для участников программы (решение владельца,
#405): второй курс напрямую из каталога берётся без условий, назначения
организаций не затрагиваются. Критерий тот же, что у Learning
(`get_program_details`): предыдущий курс пройден на 100 %.
"""

from __future__ import annotations

import frappe

from lms_frappe_app.agent_learning.errors import Отказ

#: Код отказа: курс программы заперт, пока не пройден предыдущий.
ПОРЯДОК_ПРОГРАММЫ = "program_order"

#: Прогресс записи, с которым курс считается пройденным, — как у Learning.
ПРОЙДЕН_ЦЕЛИКОМ = 100


def _курсы_программы(программа: str) -> list[str]:
	return frappe.get_all(
		"LMS Program Course",
		filters={"parent": программа, "parenttype": "LMS Program"},
		pluck="course",
		order_by="idx asc",
	)


def _пройден(ученик: str, курс: str) -> bool:
	прогресс = frappe.db.get_value("LMS Enrollment", {"member": ученик, "course": курс}, "progress")
	return (прогресс or 0) >= ПРОЙДЕН_ЦЕЛИКОМ


def _название_курса(курс: str) -> str | None:
	return frappe.db.get_value("LMS Course", курс, "title")


def программы_курса(курс: str, ученик: str | None) -> list[dict]:
	"""Программы, в которые входит курс, — опубликованные и те, где ученик участник.

	По каждой: место курса, число курсов, обязателен ли порядок, участник ли
	ученик и — если курс для него заперт — какой курс пройти раньше.
	"""
	программы = frappe.get_all(
		"LMS Program Course",
		filters={"course": курс, "parenttype": "LMS Program"},
		pluck="parent",
		distinct=True,
	)
	if not программы:
		return []
	участник_в = (
		set(
			frappe.get_all(
				"LMS Program Member",
				filters={"member": ученик, "parent": ("in", программы)},
				pluck="parent",
			)
		)
		if ученик and ученик != "Guest"
		else set()
	)
	итог = []
	for запись in frappe.get_all(
		"LMS Program",
		filters={"name": ("in", программы)},
		fields=["name", "title", "published", "enforce_course_order"],
		order_by="title asc",
	):
		участник = запись.name in участник_в
		if not (запись.published or участник):
			continue
		курсы = _курсы_программы(запись.name)
		место = курсы.index(курс)
		замок = None
		if участник and запись.enforce_course_order and место > 0:
			предыдущий = курсы[место - 1]
			if not _пройден(ученик, предыдущий):
				замок = {"id": предыдущий, "title": _название_курса(предыдущий)}
		итог.append(
			{
				"program": запись.name,
				"title": запись.title or запись.name,
				"number": место + 1,
				"total": len(курсы),
				"enforce_order": bool(запись.enforce_course_order),
				"member": участник,
				"previous": {"id": курсы[место - 1], "title": _название_курса(курсы[место - 1])}
				if место > 0
				else None,
				"locked_by": замок,
			}
		)
	return итог


def замок(ученик: str, курс: str) -> dict | None:
	"""Программа, которая не пускает ученика на курс, и курс, который пройти раньше.

	`None` — пускает: ученик не участник программы с обязательным порядком,
	курс в ней первый или предыдущий пройден.
	"""
	for программа in программы_курса(курс, ученик):
		if программа["locked_by"]:
			return {
				"program": программа["program"],
				"program_title": программа["title"],
				"previous": программа["locked_by"],
			}
	return None


def требовать_порядок(ученик: str, курс: str) -> None:
	"""Отказ `program_order`, если курс для ученика заперт программой."""
	заперт = замок(ученик, курс)
	if заперт:
		raise Отказ(
			ПОРЯДОК_ПРОГРАММЫ,
			f"Сначала пройдите курс «{заперт['previous']['title']}»: "
			f"программа «{заперт['program_title']}» идёт по порядку",
			course=курс,
			**заперт,
		)
