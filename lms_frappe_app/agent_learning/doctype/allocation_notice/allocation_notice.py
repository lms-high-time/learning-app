# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AllocationNotice(Document):
	"""Письмо по назначению, которое уже ушло (learning-services#365).

	Журнал «один раз»: письмо о назначении и напоминание о сроке уходят
	человеку по назначению однократно, а имя записи из полей не даёт базе
	принять второе. Пишет его только `agent_learning.notices`.
	"""
