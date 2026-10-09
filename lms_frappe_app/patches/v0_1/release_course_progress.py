# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Прогресс записанных на курсы из релиза — по урокам программы (learning-services#522).

Хук записи на курс и пересчёт после публикации (`course_progress`) держат долю
верной с этого выпуска; патч пересчитывает записанные раньше — честно по
программе, без удержания 100 (`пересчитать_курс(удерживать=False)`, решение
владельца). `Why:` формула Learning засчитывала пройденные уроки, снятые из
релиза, и записанные до выкатки 100 и больше могли быть завышены: удержание
закрепило бы их. Удержание действует со следующей записи после патча.

Пишется только расходящаяся доля: повторный запуск ничего не меняет. Итог —
по курсу: сколько записей поправлено.
"""

import frappe

from lms_frappe_app.agent_learning import course_progress


def execute():
	курсы = frappe.get_all(
		"LMS Course", filters={"active_release": ("is", "set")}, pluck="name", order_by="name"
	)
	for курс in курсы:
		print(
			f"release_course_progress: {курс} — поправлено записей: {course_progress.пересчитать_курс(курс, удерживать=False)}"
		)
	if not курсы:
		print("release_course_progress: курсов из релиза нет")
