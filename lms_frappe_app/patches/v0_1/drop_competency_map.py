# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Колонка снятого поля `Agent Course Release.competency_map` уходит из базы
(learning-services#839).

Определения компетенций курса — только в каталоге; миграция Frappe колонку
снятого поля не удаляет, как и у `drop_removed_columns`. `DROP COLUMN IF
EXISTS` — только если колонка есть в базе сайта: повторный запуск и свежий сайт
ничего не делают.
"""

import frappe

from lms_frappe_app.patches.v0_1.drop_removed_columns import колонки

ДОКТАЙП, КОЛОНКА = "Agent Course Release", "competency_map"


def execute():
	if КОЛОНКА not in колонки(ДОКТАЙП):
		print(f"drop_competency_map: колонки {КОЛОНКА} нет")
		return
	frappe.db.sql_ddl(f"ALTER TABLE `tab{ДОКТАЙП}` DROP COLUMN IF EXISTS `{КОЛОНКА}`")
	frappe.client_cache.delete_value(f"table_columns::tab{ДОКТАЙП}")
	print(f"drop_competency_map: {ДОКТАЙП} — удалена колонка {КОЛОНКА}")
