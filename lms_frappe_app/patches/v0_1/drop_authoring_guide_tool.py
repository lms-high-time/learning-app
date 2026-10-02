# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Значение убранного поля `authoring_guide_tool` уходит из `tabSingles` (learning-services#484).

`Why:` миграция, убирая поле из одиночного doctype, его значение не стирает:
строка жила бы в базе и всплывала в `get_singles_dict` без поля в схеме,
пока админ не сохранит форму настроек.
"""

import frappe


def execute():
	frappe.db.delete("Singles", {"doctype": "Agent Learning Settings", "field": "authoring_guide_tool"})
