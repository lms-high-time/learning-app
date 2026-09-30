# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AgentHomeworkSubmission(Document):
	"""Сдача домашки: одна живая на «задание + ученик + пространство».

	Уникальность держит индекс в базе (`install.обеспечить_индекс_домашек`),
	а все изменения идут через `agent_learning.homework`: он пишет версии и
	журнал. Запись в схеме — только у System Manager (learning-services#439).
	"""
