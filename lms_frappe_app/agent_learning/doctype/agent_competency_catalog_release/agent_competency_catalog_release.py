# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from lms_frappe_app.agent_learning.doctype.agent_competency.agent_competency import только_из_каталога


class AgentCompetencyCatalogRelease(Document):
	"""Выпуск каталога компетенций: версия, снимок и правки против прежнего (learning-services#825).

	Неизменяем и не удаляется. `Why:` на выпуск ссылаются компетенции (с какого
	выпуска их текущая версия) и выпуски курсов (по какому каталогу курс
	собран): правка снимка задним числом переписала бы, по каким признакам
	поставлен уровень. Исправление — новый выпуск (`catalog.service`).

	Вставка — только публикацией: права `create` у доктайпа нет ни у кого, но
	администратор обходит права, а выпуск мимо публикации разошёлся бы с
	записями компетенций.
	"""

	def validate(self):
		if self.is_new():
			только_из_каталога(self, frappe._("Выпуск каталога"))
			return
		frappe.throw(
			frappe._("Выпуск каталога не правится: опубликуйте новый"), title=frappe._("Выпуск неизменяем")
		)

	def on_trash(self):
		frappe.throw(
			frappe._("Выпуск каталога не удаляется: выпуски — история версий компетенций"),
			title=frappe._("Выпуск неизменяем"),
		)
