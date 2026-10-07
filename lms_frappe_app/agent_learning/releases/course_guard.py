# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Поля курса Learning, которые ставит публикация релиза (learning-services#500).

Хук `validate` у `LMS Course`: правка из desk и любой путь мимо сервиса
публикации проходят здесь же. Ключ курса и действующий релиз ставит только
сервис публикации (`ИЗ_РЕЛИЗА` во флагах документа), а действующий релиз —
релиз этого курса.

`Why:` на ключе держится поиск курса публикацией, на действующем релизе —
программа курса. Ключ, поправленный в desk, увёл бы следующий релиз в новый
курс, чужой релиз подменил бы программу.
"""

import frappe

РЕЛИЗ = "Agent Course Release"
#: Флаг документа курса, которым сервис публикации помечает свою запись.
ИЗ_РЕЛИЗА = "from_release"
ПОЛЯ_РЕЛИЗА = ("course_key", "active_release")


def проверить_курс(doc, method=None) -> None:
	прежний = doc.get_doc_before_save()
	if not doc.flags.get(ИЗ_РЕЛИЗА):
		for поле in ПОЛЯ_РЕЛИЗА:
			было = прежний.get(поле) if прежний else None
			if (doc.get(поле) or None) != (было or None):
				frappe.throw(
					frappe._("Поле «{0}» ставит публикация релиза, а не правка курса").format(поле),
					title=frappe._("Курс из релиза"),
				)
	релиз = doc.get("active_release")
	if релиз and frappe.db.get_value(РЕЛИЗ, релиз, "course") != doc.name:
		frappe.throw(frappe._("Действующий релиз — релиз другого курса"), title=frappe._("Курс из релиза"))
