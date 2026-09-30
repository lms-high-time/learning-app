# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class HomeworkNotice(Document):
	"""Журнал писем домашки: одно письмо на ключ (learning-services#452).

	Ключ — `{сдача}:reminder:{срок}`, `{строка журнала}:returned`,
	`{получатель}:digest:{дата}`; уникальность держит база.
	"""
