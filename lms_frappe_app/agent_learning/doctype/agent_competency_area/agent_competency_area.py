# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from lms_frappe_app.agent_learning.doctype.agent_competency.agent_competency import только_из_каталога


class AgentCompetencyArea(Document):
	"""Область каталога компетенций — значение закрытого списка (learning-services#825)."""

	def validate(self):
		только_из_каталога(self, frappe._("Область"))

	def on_trash(self):
		frappe.throw(
			frappe._("Область каталога не удаляется: ключи каталога постоянные"),
			title=frappe._("Каталог компетенций"),
		)
