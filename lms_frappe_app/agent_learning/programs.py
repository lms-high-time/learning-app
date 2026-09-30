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


def программы_курсов(курсы: list[str], ученик: str | None) -> dict[str, list[dict]]:
	"""Программы каждого курса — опубликованные и те, где ученик участник.

	По каждой: место курса, число курсов, обязателен ли порядок, участник ли
	ученик, предыдущий и следующий курс и — если курс для него заперт — какой
	курс пройти раньше. Курса без программ в ответе нет.

	Выборок постоянное число, сколько бы курсов ни пришло: каталог и список
	курсов агенту строятся одним вызовом, и запрос на курс сделал бы их N+1.
	Курс вне программ стоит одной выборки.
	"""
	запрошены = set(курсы)
	if not запрошены:
		return {}
	программы = frappe.get_all(
		"LMS Program Course",
		filters={"course": ("in", list(запрошены)), "parenttype": "LMS Program"},
		pluck="parent",
		distinct=True,
	)
	if not программы:
		return {}
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
	видимые = [
		запись
		for запись in frappe.get_all(
			"LMS Program",
			filters={"name": ("in", программы)},
			fields=["name", "title", "published", "enforce_course_order"],
			order_by="title asc",
		)
		if запись.published or запись.name in участник_в
	]
	if not видимые:
		return {}
	состав: dict[str, list[str]] = {}
	for строка in frappe.get_all(
		"LMS Program Course",
		filters={"parent": ("in", [запись.name for запись in видимые]), "parenttype": "LMS Program"},
		fields=["parent", "course"],
		order_by="parent asc, idx asc",
	):
		состав.setdefault(строка.parent, []).append(строка.course)

	# Место каждого запрошенного курса в каждой видимой программе.
	места = []
	for запись in видимые:
		курсы_программы = состав.get(запись.name, [])
		места += [
			(запись, курсы_программы, место)
			for место, курс in enumerate(курсы_программы)
			if курс in запрошены
		]
	соседи = {
		курсы_программы[сдвиг]
		for _, курсы_программы, место in места
		for сдвиг in (место - 1, место + 1)
		if 0 <= сдвиг < len(курсы_программы)
	}
	названия = dict(
		frappe.get_all(
			"LMS Course", filters={"name": ("in", list(соседи))}, fields=["name", "title"], as_list=True
		)
		if соседи
		else []
	)
	проверить = {
		курсы_программы[место - 1]
		for запись, курсы_программы, место in места
		if место > 0 and запись.enforce_course_order and запись.name in участник_в
	}
	пройдены = (
		set(
			frappe.get_all(
				"LMS Enrollment",
				filters={
					"member": ученик,
					"course": ("in", list(проверить)),
					"progress": (">=", ПРОЙДЕН_ЦЕЛИКОМ),
				},
				pluck="course",
			)
		)
		if проверить
		else set()
	)

	def курс_наружу(курс: str) -> dict:
		return {"id": курс, "title": названия.get(курс)}

	итог: dict[str, list[dict]] = {}
	for запись, курсы_программы, место in места:
		предыдущий = курсы_программы[место - 1] if место > 0 else None
		следующий = курсы_программы[место + 1] if место + 1 < len(курсы_программы) else None
		участник = запись.name in участник_в
		заперт = bool(участник and запись.enforce_course_order and предыдущий and предыдущий not in пройдены)
		итог.setdefault(курсы_программы[место], []).append(
			{
				"program": запись.name,
				"title": запись.title or запись.name,
				"number": место + 1,
				"total": len(курсы_программы),
				"enforce_order": bool(запись.enforce_course_order),
				"member": участник,
				"previous": курс_наружу(предыдущий) if предыдущий else None,
				"next": курс_наружу(следующий) if следующий else None,
				"locked_by": курс_наружу(предыдущий) if заперт else None,
			}
		)
	return итог


def программы_курса(курс: str, ученик: str | None) -> list[dict]:
	"""Программы одного курса — как у `программы_курсов`."""
	return программы_курсов([курс], ученик).get(курс, [])


def _замок_из(программы: list[dict]) -> dict | None:
	for программа in программы:
		if программа["locked_by"]:
			return {
				"program": программа["program"],
				"program_title": программа["title"],
				"previous": программа["locked_by"],
			}
	return None


def запертые(ученик: str, курсы: list[str]) -> set[str]:
	"""Какие из курсов ученику закрыты программой — одним пакетом выборок."""
	return {курс for курс, программы in программы_курсов(курсы, ученик).items() if _замок_из(программы)}


def замок(ученик: str, курс: str) -> dict | None:
	"""Программа, которая не пускает ученика на курс, и курс, который пройти раньше.

	`None` — пускает: ученик не участник программы с обязательным порядком,
	курс в ней первый или предыдущий пройден.
	"""
	return _замок_из(программы_курса(курс, ученик))


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
