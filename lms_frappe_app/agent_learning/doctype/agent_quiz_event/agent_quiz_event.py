# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class AgentQuizEvent(Document):
	"""Запись журнала проверки: выдан вопрос, принят ответ, попытку перенесла
	на новую версию курса или аннулировала публикация.

	Журнал для разбора курса: на квиз отвечает агент, и серверный вердикт не
	говорит, что ответил сам ученик. Поэтому рядом с ответом, как его прислал
	агент, лежат слова ученика — дословно, без проверки.

	Читают журнал только администраторы платформы: в нём ответы рядом с
	вердиктом, то есть готовые эталоны. Пишет только квиз, в той же
	транзакции, что и попытку, — и публикация, когда переносит попытки.
	"""

	def before_insert(self):
		self.occurred_at = self.occurred_at or now_datetime()

	def before_save(self):
		# Как у журнала занятия: прежняя редакция есть только у правки, а
		# журнал, который можно поправить, перестаёт быть журналом.
		if self.get_doc_before_save():
			frappe.throw(frappe._("Записи журнала проверки не изменяются"), frappe.ValidationError)
