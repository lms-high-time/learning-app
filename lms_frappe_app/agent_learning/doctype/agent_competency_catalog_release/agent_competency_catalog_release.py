# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AgentCompetencyCatalogRelease(Document):
	"""Выпуск каталога компетенций: версия, снимок и правки против прежнего (learning-services#825).

	Неизменяем и не удаляется. `Why:` на выпуск ссылаются компетенции (с какого
	выпуска их текущая версия) и выпуски курсов (по какому каталогу курс
	собран): правка снимка задним числом переписала бы, по каким признакам
	поставлен уровень. Исправление — новый выпуск (`catalog.service`).
	"""

	def validate(self):
		if self.is_new():
			return
		frappe.throw(
			frappe._("Выпуск каталога не правится: опубликуйте новый"), title=frappe._("Выпуск неизменяем")
		)

	def on_trash(self):
		frappe.throw(
			frappe._("Выпуск каталога не удаляется: выпуски — история версий компетенций"),
			title=frappe._("Выпуск неизменяем"),
		)
