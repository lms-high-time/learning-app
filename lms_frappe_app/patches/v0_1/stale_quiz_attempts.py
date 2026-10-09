# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Открытые попытки квиза — с порогом и на действующем релизе (learning-services#514).

Попытка несёт порог зачёта (`Agent Quiz Attempt.pass_percentage`), а
публикация новой версии переносит открытые попытки на неё или аннулирует
(`release_quiz.перенести_попытки`). Попытки, открытые до выкатки, ни того ни
другого не получили. Патч:

- пишет порог **всем** попыткам `In Progress` — из урока их релиза, без
  условия. `Why:` у поля Float нет «не записано» (столбец `NOT NULL DEFAULT
  0`), а попытку, начатую прежним кодом в окне выкатки, отличить не по чему:
  после переключения кода патч прогоняется ещё раз (`bench execute`), и
  повтор пишет те же значения;
- переносит или аннулирует открытые попытки не на действующем релизе курса
  тем же правилом, что публикация, — сравнением с квизом урока в действующем
  релизе. У курса без действующего релиза урока нет нигде — аннулирование
  `lesson_removed`.

Порог пишется раньше переноса: перенесённая попытка оценивается порогом
релиза, с которым начата. Повторный запуск ничего не меняет, кроме того же
порога: перенесённые и аннулированные на действующем релизе или закрыты.
Итог — по курсу. Идёт до `free_release_content`: сравнению нужен индекс
релиза попытки.
"""

import frappe

from lms_frappe_app.agent_learning import release_quiz
from lms_frappe_app.agent_learning.constants import ПОПЫТКА_ИДЁТ
from lms_frappe_app.agent_learning.releases import index

ПОПЫТКА = "Agent Quiz Attempt"


def execute():
	пороги = записать_пороги()
	итоги = перенести_отставшие()
	for курс in sorted(set(пороги) | set(итоги)):
		итог = итоги.get(курс, {"moved": 0, "cancelled": 0})
		print(
			f"stale_quiz_attempts: {курс} — порог записан открытым попыткам: {пороги.get(курс, 0)}; "
			f"перенесено {итог['moved']}, аннулировано {итог['cancelled']}"
		)
	if not пороги and not итоги:
		print("stale_quiz_attempts: открытых попыток нет")


def записать_пороги() -> dict[str, int]:
	"""Порог урока релиза попытки — всем открытым попыткам; курс → у скольких попыток порог из урока."""
	frappe.db.sql(
		f"""
		update `tab{ПОПЫТКА}` a
		join `tab{index.УРОК}` l
			on l.parenttype = %(release_doctype)s and l.parent = a.release and l.lesson_key = a.lesson_key
		set a.pass_percentage = l.pass_percentage
		where a.status = %(open)s
		""",
		{"release_doctype": index.РЕЛИЗ, "open": ПОПЫТКА_ИДЁТ},
	)
	return dict(
		frappe.db.sql(
			f"""
			select a.course, count(*)
			from `tab{ПОПЫТКА}` a
			join `tab{index.УРОК}` l
				on l.parenttype = %(release_doctype)s and l.parent = a.release and l.lesson_key = a.lesson_key
			where a.status = %(open)s
			group by a.course
			""",
			{"release_doctype": index.РЕЛИЗ, "open": ПОПЫТКА_ИДЁТ},
		)
	)


def перенести_отставшие() -> dict[str, dict[str, int]]:
	"""Открытые попытки не на действующем релизе курса — перенос или аннулирование; курс → итог."""
	попытки = frappe.db.sql(
		f"""
		select {", ".join(f"a.`{поле}`" for поле in release_quiz.ПОЛЯ_ОТКРЫТОЙ)}, c.active_release
		from `tab{ПОПЫТКА}` a
		join `tabLMS Course` c on c.name = a.course
		where a.status = %s and not (a.release <=> c.active_release)
		order by a.course, a.creation
		""",
		ПОПЫТКА_ИДЁТ,
		as_dict=True,
	)
	по_курсам: dict[str, list] = {}
	for попытка in попытки:
		по_курсам.setdefault(попытка.course, []).append(попытка)
	итоги = {}
	for курс, список in по_курсам.items():
		действующий = список[0].active_release
		итоги[курс] = release_quiz.перенести_попытки(список, действующий, _строки(действующий))
	return итоги


def _строки(релиз: str | None) -> dict:
	"""Уроки и вопросы действующего релиза — в форме `index.строки`, какая нужна переносу."""
	if not релиз:
		return {"lessons": [], "questions": []}
	фильтр = {"parenttype": index.РЕЛИЗ, "parent": релиз}
	return {
		"lessons": frappe.get_all(index.УРОК, filters=фильтр, fields=["lesson_key"], order_by="idx asc"),
		"questions": frappe.get_all(
			index.ВОПРОС,
			filters=фильтр,
			fields=[
				"lesson_key",
				"question_key",
				"objective_key",
				"text",
				"option_list",
				"correct",
				"explanation",
			],
			order_by="idx asc",
		),
	}
