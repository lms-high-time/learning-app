# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Значения снятых полей настроек уходят из `tabSingles` (learning-services#512).

- `lesson_segment_limit` — предел куска урока при сборке курса по кусочку;
- `pass_threshold` — порог зачёта платформы: зачёт идёт по `pass_percentage`
  урока в релизе.

`Why:` миграция, убирая поле из одиночного доктайпа, его значение не стирает:
строка жила бы в базе и всплывала в `get_singles_dict` без поля в схеме.
Повторный запуск ничего не находит.
"""

import frappe

НАСТРОЙКИ = "Agent Learning Settings"
ПОЛЯ = ("lesson_segment_limit", "pass_threshold")


def execute():
	строки = frappe.db.sql(
		"SELECT field FROM `tabSingles` WHERE doctype = %s AND field IN %s", (НАСТРОЙКИ, ПОЛЯ), pluck=True
	)
	frappe.db.delete("Singles", {"doctype": НАСТРОЙКИ, "field": ("in", ПОЛЯ)})
	frappe.clear_document_cache(НАСТРОЙКИ, НАСТРОЙКИ)
	print(f"drop_removed_settings: удалены значения: {', '.join(sorted(строки)) or 'нет'}")
