# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CourseAllocationHomeworkDue(Document):
	"""Срок домашки урока для группы назначения — вместо срока автора (learning-services#452)."""
