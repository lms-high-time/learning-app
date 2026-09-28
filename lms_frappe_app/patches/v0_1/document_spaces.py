# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Документы и занятия, заведённые до пространств, получают пространство.

Правило то же, что у новых (`access.организация_курса`): курс назначен
организацией ученика — пространство её, иначе личное. Документ по курсу,
который дала компания, — её рабочий документ (learning-services#341).

Патч, а не `after_migrate`: пустое пространство после переноса означает
«личное» осознанно, и повторная разметка переспорила бы это.
"""

import frappe

from lms_frappe_app.agent_learning.access import организация_курса
from lms_frappe_app.agent_learning.constants import ЧЛЕНСТВО_ДЕЙСТВУЕТ


def execute():
	# Колонка статуса добавлена со значением по умолчанию, но строка,
	# вставленная в обход ORM, могла остаться пустой.
	frappe.db.sql(
		"update `tabOrganization Membership` set status = %s where ifnull(status, '') = ''",
		ЧЛЕНСТВО_ДЕЙСТВУЕТ,
	)

	пространства: dict[tuple[str, str], str | None] = {}
	for doctype in ("Agent Student Artifact", "Agent Learning Session"):
		for запись in frappe.get_all(
			doctype,
			filters={"organization": ("is", "not set"), "student": ("is", "set")},
			fields=["name", "student", "course"],
		):
			ключ = (запись.student, запись.course)
			if ключ not in пространства:
				пространства[ключ] = организация_курса(*ключ)
			if пространства[ключ]:
				frappe.db.set_value(
					doctype, запись.name, "organization", пространства[ключ], update_modified=False
				)
