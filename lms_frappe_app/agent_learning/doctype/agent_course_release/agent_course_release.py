# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AgentCourseRelease(Document):
	"""Опубликованный релиз курса: снимок и индекс по ключам (learning-services#500).

	Неизменяем. `Why:` на релиз будут ссылаться прохождения учеников (этап 2):
	правка снимка задним числом переписала бы, по чему ученик занимался.
	Исправление — новый релиз. Отказ — на любом сохранении существующей
	записи: дочерние строки индекса тоже часть релиза, и поштучная сверка
	полей была бы дырявой.
	"""

	def validate(self):
		if self.is_new():
			return
		frappe.throw(frappe._("Релиз не правится: опубликуйте новый"), title=frappe._("Релиз неизменяем"))
