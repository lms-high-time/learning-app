# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Содержимое прежних версий курсов — освобождено (learning-services#514).

Публикация освобождает прежние версии курса сама (`retention.освободить_прежние`);
патч освобождает накопленное до неё: у каждого курса с действующим релизом —
все версии, кроме действующей, с непустым снимком. Освобождённые не
выбираются: повторный запуск ничего не меняет. Итог — по курсу и по каждой
освобождённой версии: номер, дайджест, дата публикации и коммит. Всё это
остаётся и в записи релиза (`course_releases`); печать — для удобства:
освобождённое видно в логе миграции, без запроса к сайту.

Освобождение необратимо, а заполняющие патчи этапа читают индекс прежних
версий — `release_record_keys` (ключи на записях Learning),
`run_objective_texts` (тексты целей прохождений), `note_lesson_keys` (ключ
урока заметки по адресу в индексе её релиза), `stale_quiz_attempts` (порог и
перенос открытых попыток). Пока хоть один из них не выполнен
(`patch_log.выполнен`), патч ничего не трогает и печатает, какие не выполнены.
`Why:` миграция с `--skip-failing` идёт дальше упавшего патча. Восстановление
— после исправления `bench --site <сайт> run-patch --force` для пропущенного,
затем для `free_release_content`.
"""

import frappe

from lms_frappe_app.agent_learning.releases import index, retention
from lms_frappe_app.patches.v0_1.patch_log import выполнен

#: Заполняющие патчи, которым нужен индекс прежних версий, — по порядку `patches.txt`.
ЖДЁТ = ("release_record_keys", "run_objective_texts", "note_lesson_keys", "stale_quiz_attempts")


def execute():
	if не_выполнены := [патч for патч in ЖДЁТ if not выполнен(патч)]:
		print(
			"free_release_content: прежние версии не освобождены — не выполнены патчи "
			+ ", ".join(не_выполнены)
		)
		return
	курсы = frappe.db.sql_list(
		f"""
		select distinct r.course
		from `tab{index.РЕЛИЗ}` r
		join `tabLMS Course` c on c.name = r.course
		where ifnull(c.active_release, '') != '' and r.name != c.active_release
			and r.snapshot is not null
		order by r.course
		"""
	)
	for курс in курсы:
		освобождены = retention.освободить_прежние(курс)
		print(f"free_release_content: {курс} — освобождено версий: {len(освобождены)}")
		for версия in освобождены:
			print(
				f"free_release_content: {курс} — {версия.name}: версия {версия.version}, "
				f"digest {версия.digest}, опубликована {версия.published_at}, "
				f"коммит {версия.source_commit or 'нет'}"
			)
	if not курсы:
		print("free_release_content: освобождать нечего")
