# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Журнал проверки сброшенных раньше учеников — в архив (learning-services#510).

Сброс прогресса до этой правки архивировал попытки квиза, а их события
(`Agent Quiz Event`, в нём слова ученика `student_words`) оставлял с учеником.
Патч переносит в архив события тех попыток, что уже в архиве: ученик события
совпадает с учеником попытки до сброса, дата архива — дата архива попытки.
`Why:` сброс обещает «как будто не записывался» и для сброшенных раньше.
Повторный запуск ничего не меняет: у архивных событий ученик пуст.
"""

import frappe

СОБЫТИЕ = "Agent Quiz Event"
ПОПЫТКА = "Agent Quiz Attempt"


def execute():
	for доктайп in (СОБЫТИЕ, ПОПЫТКА):
		if not frappe.db.table_exists(доктайп, cached=False):
			print("archive_reset_quiz_events: таблиц квиза нет — делать нечего")
			return
	frappe.db.sql(
		f"""
		update `tab{СОБЫТИЕ}` e
		join `tab{ПОПЫТКА}` a on a.name = e.attempt
		set e.archived_student = e.student, e.archived_at = a.archived_at, e.student = null
		where ifnull(a.student, '') = ''
			and ifnull(a.archived_student, '') != ''
			and e.student = a.archived_student
		"""
	)
	print("archive_reset_quiz_events: события архивных попыток перенесены в архив")
