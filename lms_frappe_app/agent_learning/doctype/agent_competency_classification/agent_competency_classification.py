# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from lms_frappe_app.agent_learning.doctype.agent_competency.agent_competency import только_из_каталога


class AgentCompetencyClassification(Document):
	"""Схема классификации каталога компетенций: закрытый список классов (learning-services#832).

	Слаг — имя записи; классы — таблица `classes`. Компетенция ссылается на класс
	своей таблицей `classes` (`Agent Competency Class Assignment`)."""

	def validate(self):
		только_из_каталога(self, frappe._("Схема классификации"))

	def on_trash(self):
		frappe.throw(
			frappe._("Схема классификации не удаляется: ключи каталога постоянные"),
			title=frappe._("Каталог компетенций"),
		)
