# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Прохождения уроков курса для автора — только чтение (learning-services#512).

Прохождения читаются как сохранены: без блокировки и сверки с действующим
релизом (см. `service.прогресс_глав`). Урок подписан названием записи урока
прохождения (`Course Lesson.title`): проекция держит его равным действующему
релизу, а у урока, снятого из релиза, — последнему, где урок был. Название
пункта — из строки прохождения: сверка пишет его из релиза, а снятый пункт
хранит последнее. Индекс релиза прохождения не читается: прохождение снятого
урока остаётся на прежней версии, а она содержимого не хранит
(learning-services#514).

Свидетельства здесь отдаются: страницу читают только авторские методы, и
доступ к курсу проверяет вызывающий.
"""

import frappe

from lms_frappe_app.agent_learning.runs.service import ПРОХОЖДЕНИЕ

ЦЕЛЬ = "Agent Lesson Run Objective"
ПУНКТ = "Agent Lesson Run Goal"


def страница(курс: str, ключ_урока: str | None, начало: int, предел: int) -> tuple[list[dict], bool]:
	"""Живые прохождения курса страницей: (прохождения, есть ли ещё).

	Архивные (сброс прогресса: `archived_at` задан, `student` пуст) не
	отдаются. `ключ_урока` — только прохождения урока с этим ключом. Порядок —
	свежее начало сверху: `started_at` по убыванию, не начатые — в конце, их
	между собой — свежие заведённые сверху; при равенстве — по имени записи,
	чтобы страницы не перекрывались. Цели и пункты — по порядку релиза
	прохождения, снятые — в конце.

	`Why:` три выборки на страницу при любом её размере и любом числе
	релизов: прохождения вместе с именами учеников и названиями уроков —
	одной, цели и пункты всех прохождений страницы — по одной. Строка сверх
	`предел` говорит, есть ли следующая страница, без отдельного счёта.
	"""
	отбор = "and r.lesson_key = %(lesson)s" if ключ_урока else ""
	строки = frappe.db.sql(
		f"""
		select r.name, r.student, u.full_name, r.lesson_key, l.title as lesson_title,
			r.status, r.accepted_at, r.started_at, r.passed_at, r.resume_from
		from `tab{ПРОХОЖДЕНИЕ}` r
		left join `tabUser` u on u.name = r.student
		left join `tabCourse Lesson` l on l.name = r.lesson
		where r.course = %(course)s and r.archived_at is null and r.student is not null {отбор}
		order by r.started_at is null, r.started_at desc, r.creation desc, r.name desc
		limit %(limit)s offset %(start)s
		""",
		{"course": курс, "lesson": ключ_урока, "limit": предел + 1, "start": начало},
		as_dict=True,
	)
	ещё = len(строки) > предел
	строки = строки[:предел]
	if not строки:
		return [], ещё
	имена = [с.name for с in строки]
	цели = _по_прохождениям(
		ЦЕЛЬ, имена, ["parent", "objective_key", "status", "quiz_confirmed", "removed"], _цель
	)
	пункты = _по_прохождениям(
		ПУНКТ,
		имена,
		[
			"parent",
			"objective_key",
			"goal_key",
			"title",
			"status",
			"evidence",
			"marked_at",
			"session",
			"removed",
		],
		_пункт,
	)
	return [
		{
			"student": {"id": с.student, "name": с.full_name or None},
			"lesson": {"key": с.lesson_key, "title": с.lesson_title},
			"status": с.status,
			"accepted_at": _дата(с.accepted_at),
			"started_at": _дата(с.started_at),
			"passed_at": _дата(с.passed_at),
			"resume_from": с.resume_from or None,
			"objectives": цели.get(с.name, []),
			"goals": пункты.get(с.name, []),
		}
		for с in строки
	], ещё


def _по_прохождениям(doctype: str, имена: list[str], поля: list[str], собрать) -> dict[str, list[dict]]:
	"""Строки дочерней таблицы прохождений `имена` одной выборкой: имя → строки по порядку."""
	итог: dict[str, list[dict]] = {}
	for строка in frappe.get_all(
		doctype,
		filters={"parenttype": ПРОХОЖДЕНИЕ, "parent": ("in", имена)},
		fields=поля,
		order_by="idx asc",
	):
		итог.setdefault(строка.parent, []).append(собрать(строка))
	return итог


def _цель(строка) -> dict:
	return {
		"objective_key": строка.objective_key,
		"status": строка.status,
		"quiz_confirmed": bool(строка.quiz_confirmed),
		"removed": bool(строка.removed),
	}


def _пункт(строка) -> dict:
	return {
		"objective_key": строка.objective_key,
		"goal_key": строка.goal_key,
		"title": строка.title,
		"status": строка.status,
		"evidence": строка.evidence or None,
		"marked_at": _дата(строка.marked_at),
		"session": строка.session or None,
		"removed": bool(строка.removed),
	}


def _дата(значение) -> str | None:
	return значение.isoformat() if значение else None
