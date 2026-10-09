# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Тексты целей — в строках прохождений (learning-services#514).

Текст цели прохождения держит строка `Agent Lesson Run Objective.text`, а
пишет сверка (`runs.service._сверить_цели`). Патч заполняет пустые тексты у
всех прохождений, архивных тоже: по индексу релиза прохождения, а где его строки
цели нет — по последнему релизу курса, где цель урока есть. Так снятая цель
получает последний текст, как и при сверке. Цель, которой нет ни в одном
релизе курса, остаётся без текста. Пишет только в пустое поле: повторный
запуск ничего не меняет. Итог — по курсу.

`Why:` по релизу прохождения, а не по действующему: снятая цель и прохождение,
которое фоновая сверка ещё не догнала, хранят текст той версии, с которой
сверены, — как название пункта. Индексы всех версий курса до освобождения
содержимого (`free_release_content`) ещё на месте.

Второй источник берёт `max(version)` по курсу, и эта версия может быть новее
релиза прохождения. `Why:` это безвредно: туда попадает только цель, которой
нет в индексе релиза прохождения (его строки уже удалены или цели там нет), —
она снята и в статусы не идёт, а текст нужен лишь как подпись; сверка
прохождения с действующим релизом и так перепишет текст живой цели
(`runs.service._сверить_цели`).
"""

import frappe

from lms_frappe_app.agent_learning.releases import index

ЦЕЛЬ = "Agent Lesson Run Objective"
ПРОХОЖДЕНИЕ = "Agent Lesson Run"

#: Откуда берётся текст: (название, соединение, релиз-источник) — по очереди.
ИСТОЧНИКИ = (
	("релиз прохождения", "", "r.release"),
	(
		"последний релиз с целью",
		f"""
		join (
			select c.course, t.lesson_key, t.objective_key, max(c.version) as version
			from `tab{index.ЦЕЛЬ}` t
			join `tab{index.РЕЛИЗ}` c on c.name = t.parent
			where t.parenttype = %(release_doctype)s and ifnull(t.text, '') != ''
			group by c.course, t.lesson_key, t.objective_key
		) v on v.course = r.course and v.lesson_key = r.lesson_key and v.objective_key = o.objective_key
		join `tab{index.РЕЛИЗ}` c on c.course = v.course and c.version = v.version
		""",
		"c.name",
	),
)


def execute():
	до = пустых()
	записано = заполнить()
	после = пустых()
	for курс in sorted(до):
		print(
			f"run_objective_texts: {курс} — текстов записано: "
			+ ", ".join(f"{откуда} {сколько.get(курс, 0)}" for откуда, сколько in записано.items())
			+ f"; без текста осталось {после.get(курс, 0)}"
		)
	if not до:
		print("run_objective_texts: целей без текста нет")


def заполнить() -> dict[str, dict[str, int]]:
	"""Пустые тексты целей прохождений по источникам по очереди; источник → курс → сколько записано."""
	записано = {}
	for откуда, соединение, релиз in ИСТОЧНИКИ:
		до = пустых()
		frappe.db.sql(
			f"""
			update `tab{ЦЕЛЬ}` o
			join `tab{ПРОХОЖДЕНИЕ}` r on r.name = o.parent
			{соединение}
			join `tab{index.ЦЕЛЬ}` t
				on t.parenttype = %(release_doctype)s and t.parent = {релиз}
				and t.lesson_key = r.lesson_key and t.objective_key = o.objective_key
			set o.text = t.text
			where o.parenttype = %(run_doctype)s and ifnull(o.text, '') = ''
				and ifnull(t.text, '') != ''
			""",
			{"release_doctype": index.РЕЛИЗ, "run_doctype": ПРОХОЖДЕНИЕ},
		)
		после = пустых()
		записано[откуда] = {
			курс: до[курс] - после.get(курс, 0) for курс in до if до[курс] != после.get(курс, 0)
		}
	return записано


def пустых() -> dict[str, int]:
	"""Курс → сколько целей его прохождений без текста."""
	return dict(
		frappe.db.sql(
			f"""
			select r.course, count(*)
			from `tab{ЦЕЛЬ}` o
			join `tab{ПРОХОЖДЕНИЕ}` r on r.name = o.parent
			where o.parenttype = %s and ifnull(o.text, '') = ''
			group by r.course
			""",
			ПРОХОЖДЕНИЕ,
		)
	)
