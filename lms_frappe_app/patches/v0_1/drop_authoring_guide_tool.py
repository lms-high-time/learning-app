# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Значение убранного поля `authoring_guide_tool` уходит из `tabSingles` (learning-services#484).

`Why:` Frappe, убирая поле из одиночного doctype, его значение не стирает:
строка осталась бы в базе и всплывала бы в `get_singles_dict` без поля в
схеме.
"""

import frappe


def execute():
	frappe.db.delete("Singles", {"doctype": "Agent Learning Settings", "field": "authoring_guide_tool"})
