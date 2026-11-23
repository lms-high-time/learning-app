# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Колонка снятого поля `Agent Course Release.competency_map` уходит из базы
(learning-services#839).

Определения компетенций курса — только в каталоге; миграция Frappe колонку
снятого поля не удаляет, как и у `drop_removed_columns`. `DROP COLUMN IF
EXISTS` — только если колонка есть в базе сайта: повторный запуск и свежий сайт
ничего не делают.

Колонка ждёт патча-проверки `competency_event_catalog_keys`: он ищет релизы с
непустой картой в ней. Миграция с `--skip-failing` продолжает после упавшего
патча, и без этого условия колонка ушла бы вместе с картой, которую проверка
нашла; пока проверка не выполнена (`patch_log.выполнен`), колонка остаётся, и
патч печатает почему.
"""

import frappe

from lms_frappe_app.patches.v0_1.drop_removed_columns import колонки
from lms_frappe_app.patches.v0_1.patch_log import выполнен

ДОКТАЙП, КОЛОНКА = "Agent Course Release", "competency_map"
#: Патч, который читает колонку и должен отработать до её удаления.
ЖДЁТ = "competency_event_catalog_keys"


def execute():
	if КОЛОНКА not in колонки(ДОКТАЙП):
		print(f"drop_competency_map: колонки {КОЛОНКА} нет")
		return
	if not выполнен(ЖДЁТ):
		print(f"drop_competency_map: {ДОКТАЙП}.{КОЛОНКА} оставлена — патч {ЖДЁТ} ещё не выполнен")
		return
	frappe.db.sql_ddl(f"ALTER TABLE `tab{ДОКТАЙП}` DROP COLUMN IF EXISTS `{КОЛОНКА}`")
	frappe.client_cache.delete_value(f"table_columns::tab{ДОКТАЙП}")
	print(f"drop_competency_map: {ДОКТАЙП} — удалена колонка {КОЛОНКА}")
