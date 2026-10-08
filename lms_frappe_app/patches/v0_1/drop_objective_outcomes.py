# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Отметки целей по занятию уходят из базы (learning-services#506).

Статус целей урока живёт в прохождении (`Agent Lesson Run`). Уходят доктайп
`Agent Objective Outcome` с таблицей, колонка `brief_start` у занятия и события
журнала `Material Issued` и `Checkpoint Reported`. Данные отметок не
переносятся.

`Why:` миграция убирает только описание: запись доктайпа без папки снимает
уборка сирот, а таблицу и колонку убранного поля оставляет, и события с видом
вне `options` остались бы в журнале значениями, которых схема не знает. Патч
идёт после синхронизации схем: занятие к этому моменту на доктайп уже не
ссылается.
"""

import frappe

ОТМЕТКИ = "Agent Objective Outcome"
ЗАНЯТИЕ = "Agent Learning Session"
ВИДЫ_СОБЫТИЙ = ("Material Issued", "Checkpoint Reported")


def execute():
	отметок = (
		frappe.db.sql(f"SELECT COUNT(*) FROM `tab{ОТМЕТКИ}`")[0][0]
		if frappe.db.table_exists(ОТМЕТКИ, cached=False)
		else 0
	)
	событий = frappe.db.count("Agent Session Event", {"kind": ("in", ВИДЫ_СОБЫТИЙ)})
	# Записи — до DDL: изменение схемы фиксирует транзакцию, и удалённое
	# уходит вместе с ним.
	frappe.db.delete("Agent Session Event", {"kind": ("in", ВИДЫ_СОБЫТИЙ)})
	frappe.delete_doc("DocType", ОТМЕТКИ, force=True, ignore_missing=True, delete_permanently=True)
	frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{ОТМЕТКИ}`")
	frappe.db.sql_ddl(f"ALTER TABLE `tab{ЗАНЯТИЕ}` DROP COLUMN IF EXISTS `brief_start`")
	frappe.client_cache.delete_value(f"table_columns::tab{ЗАНЯТИЕ}")
	print(f"drop_objective_outcomes: отметок целей — {отметок}, событий журнала — {событий}")
