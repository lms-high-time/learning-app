# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Файлы блоков, загруженные первыми в документ, привязываются к нему.

`Why:` до исправления файл первой загрузки вставлялся раньше документа и
оставался без `attached_to_name` (learning-services#343). Права на такой
файл были только у загрузившего, а удаление документа его не убирало. Какой
документ его, известно по ссылке из строки блока.
"""

import frappe


def execute():
	for строка in frappe.get_all(
		"Agent Artifact Content",
		filters={"parenttype": "Agent Student Artifact", "file": ("is", "set")},
		fields=["parent", "file"],
	):
		# Пустой ответ и у непривязанного файла, и у пропавшего; обновление
		# пропавшего не затрагивает ни одной строки.
		if not frappe.db.get_value("File", строка.file, "attached_to_name"):
			frappe.db.set_value(
				"File",
				строка.file,
				{"attached_to_doctype": "Agent Student Artifact", "attached_to_name": строка.parent},
				update_modified=False,
			)
