# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AgentNoteReply(Document):
	"""Ответ в нити замечания: кто — автор или агент — и что сказал.

	Строку пишет и снимает только сохранение заметки: сама по себе она не
	вставляется, не правится и не удаляется (learning-services#528).

	`Why:` `POST /api/resource` со строкой (`parent`, `parenttype`,
	`parentfield`) вставляет её по праву на заметке, `PUT` правит, а `DELETE` и
	`delete_items` Desk удаляют — мимо `validate` заметки, который держит
	переходы по последнему ответу нити. Строки, которые пишет сохранение
	заметки, этот `validate` не вызывают, удаление заметки — `on_trash`.
	"""

	def validate(self):
		_отказать()

	def on_trash(self):
		_отказать()


def _отказать() -> None:
	frappe.throw(
		frappe._("Ответ пишется в нить заметки: строку саму по себе не правят"),
		title=frappe._("Заметка автора"),
	)
