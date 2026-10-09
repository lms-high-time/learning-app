# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from lms_frappe_app.agent_learning import notes
from lms_frappe_app.agent_learning.errors import Отказ


class AgentAuthorNote(Document):
	"""Замечание автора на месте курса и нить ответов к нему.

	Не содержание курса: его пишут человек в кабинете и агент куратора, чтобы
	петля «увидел → агент поправил → принял» не шла через пересказ в чате.
	Правила адреса и переходов — в `agent_learning/notes.py`; пишут заметку
	авторские методы. Course Creator её только читает, Moderator и System
	Manager правят и в Desk — переходы статуса держит `validate`. Ученику
	недоступно: роль `LMS Student` прав на этот DocType не имеет.
	"""

	def validate(self):
		self._проверить_переход()

	def _проверить_переход(self):
		"""Статус меняется по `notes.ПЕРЕХОДЫ` на любом пути записи.

		Кто переводит и что сказал — последний ответ, дописанный этим же
		сохранением; без ответа переводит автор: «сделано» с текстом отмечает
		только агент, а без текста идут лишь переходы автора. Новая заметка
		рождается открытой — сохранённая сразу в другом статусе проходит тот же
		переход из `open`.
		"""
		прежний = self.get_doc_before_save()
		было = прежний.status if прежний else "open"
		if self.status == было:
			return
		прежние = {ответ.name for ответ in прежний.replies} if прежний else set()
		новые = [ответ for ответ in self.replies if ответ.name not in прежние]
		via, текст = (новые[-1].via, новые[-1].text) if новые else ("author", None)
		try:
			notes.проверить_переход(было, self.status, via, текст)
		except Отказ as отказ:
			frappe.throw(отказ.сообщение, exc=отказ, title=frappe._("Заметка автора"))
