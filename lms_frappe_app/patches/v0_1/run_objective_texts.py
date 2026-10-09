# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Тексты целей — в строках прохождений (learning-services#514).

Текст цели прохождения держит строка `Agent Lesson Run Objective.text`, а
пишет сверка (`runs.service._сверить_цели`). Патч заполняет пустые тексты у
всех прохождений, архивных тоже: по индексу релиза прохождения, а где его строки
цели нет — по действующему релизу курса, если цель есть там. Цель, которой
нет ни там, ни там, остаётся без текста. Пишет только в пустое поле:
повторный запуск ничего не меняет.

`Why:` по релизу прохождения, а не по действующему: снятая цель и прохождение,
которое фоновая сверка ещё не догнала, хранят текст той версии, с которой
сверены, — как название пункта.
"""

import frappe

from lms_frappe_app.agent_learning.releases import index

ЦЕЛЬ = "Agent Lesson Run Objective"
ПРОХОЖДЕНИЕ = "Agent Lesson Run"

#: Откуда берётся текст: релиз прохождения, затем действующий релиз курса.
ИСТОЧНИКИ = (
	("релиз прохождения", "", "r.release"),
	("действующий релиз", "join `tabLMS Course` c on c.name = r.course", "c.active_release"),
)


def execute():
	записано = заполнить()
	print(
		"run_objective_texts: текстов записано — "
		+ ", ".join(f"{откуда} {сколько}" for откуда, сколько in записано.items())
		+ f"; без текста осталось {пустых()}"
	)


def заполнить() -> dict[str, int]:
	"""Пустые тексты целей прохождений по источникам по очереди; сколько записано из каждого."""
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
		записано[откуда] = до - пустых()
	return записано


def пустых() -> int:
	"""Сколько целей прохождений без текста."""
	return frappe.db.sql(
		f"select count(*) from `tab{ЦЕЛЬ}` where parenttype = %s and ifnull(text, '') = ''",
		ПРОХОЖДЕНИЕ,
	)[0][0]
