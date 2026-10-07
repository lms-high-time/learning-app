# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AgentCourseRelease(Document):
	"""Опубликованный релиз курса: снимок и индекс по ключам (learning-services#500)."""
