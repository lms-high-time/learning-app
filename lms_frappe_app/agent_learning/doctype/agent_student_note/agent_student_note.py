# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from lms_frappe_app.agent_learning.constants import ЗАМЕТКА_ФАКТ


class AgentStudentNote(Document):
	"""Заметка агента об ученике, живущая дольше одного занятия.

	Ключ уникален в пределах ученика и курса: повторная запись замещает
	текст, а не заводит вторую строку.

	Наружу поле зовётся `key`, в схеме — `note_key`: `key` — зарезервированное
	слово SQL, и экранирует его Frappe не везде. Имя колонки за границу
	контракта не выходит, как и прочие внутренние имена.
	"""

	def validate(self):
		self.note_key = (self.note_key or "").strip().lower()
		if not self.note_key:
			frappe.throw(frappe._("Ключ заметки обязателен"), frappe.ValidationError)
		if self.kind == ЗАМЕТКА_ФАКТ:
			# Пустая строка, а не None: Frappe хранит незаполненный Link как
			# `''`, и уникальный индекс с NULL не сработал бы вовсе — MariaDB
			# считает NULL-ы различными и пропустила бы дубли фактов.
			self.course = ""
