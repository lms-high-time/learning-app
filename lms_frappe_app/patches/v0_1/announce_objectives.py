# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Цели анонсов — в поле курса `announce_objectives` (learning-services#512).

Цели курса без релиза жили в действующей директиве курса (`Agent Course
Directive.objectives`); директивы уходят, цели анонса — поле самого курса.
Переносит только курсам без действующего релиза (у них цели — главы релиза) и
только в пустое поле: повторный запуск ничего не меняет.

`Why:` поле патч заводит сам — патчи `post_model_sync` идут раньше
синхронизации фикстур, и без этого запись ушла бы в несуществующую колонку.
Директивы читаются сырым SQL за `table_exists`: на свежем сайте доктайпа к
моменту запуска может уже не быть.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

from lms_frappe_app.agent_learning import announcements

КУРС = "LMS Course"
ДИРЕКТИВА = "Agent Course Directive"
#: Поле — как в фикстуре (`fixtures/custom_field.json`): синхронизация фикстур
#: потом лишь сверит его.
ПОЛЕ = {
	"fieldname": announcements.ПОЛЕ_ЦЕЛЕЙ,
	"fieldtype": "Small Text",
	"label": "Цели анонса",
	"no_copy": 1,
	"description": "Цели курса-анонса, по строке на цель. Задаёт announce_course; "
	"у курса из релиза цели — названия глав релиза.",
	"insert_after": "course_attribution",
	"module": "Agent Learning",
}


def execute():
	create_custom_field(КУРС, ПОЛЕ)
	if not frappe.db.table_exists(ДИРЕКТИВА, cached=False):
		print("announce_objectives: директив курса нет — переносить нечего")
		return
	# Действующая — самая свежая из `is_active`: так её выбирает и сервер.
	директивы = frappe.db.sql(
		f"SELECT course, objectives FROM `tab{ДИРЕКТИВА}` WHERE is_active = 1 ORDER BY creation DESC",
		as_dict=True,
	)
	цели_курсов: dict[str, list[str]] = {}
	for директива in директивы:
		цели_курсов.setdefault(директива.course, announcements.строки(директива.objectives))
	if not цели_курсов:
		print("announce_objectives: директив курса нет — переносить нечего")
		return
	курсы = frappe.get_all(
		КУРС,
		filters={"name": ("in", list(цели_курсов))},
		fields=["name", "active_release", announcements.ПОЛЕ_ЦЕЛЕЙ],
	)
	for курс in курсы:
		цели = цели_курсов[курс.name]
		if курс.active_release or (курс.get(announcements.ПОЛЕ_ЦЕЛЕЙ) or "").strip() or not цели:
			print(f"announce_objectives: {курс.name} — целей перенесено 0")
			continue
		frappe.db.set_value(
			КУРС, курс.name, announcements.ПОЛЕ_ЦЕЛЕЙ, "\n".join(цели), update_modified=False
		)
		print(f"announce_objectives: {курс.name} — целей перенесено {len(цели)}")
