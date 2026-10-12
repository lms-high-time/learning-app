# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from lms_frappe_app.agent_learning.doctype.agent_course_artifact.agent_course_artifact import (
	нормализовать_ключ,
)


class AgentStudentArtifact(Document):
	"""Документ ученика по схеме курса: строки и поля разделов по ключам.

	Экземпляр один на «ученик + курс + артефакт + пространство» — это держит
	уникальный индекс в базе (`install.обеспечить_индекс_артефактов`).
	Заводится при первом заполнении: пустых документов у платформы хватает и
	без этого.

	Данные хранятся по ключам разделов и колонок, а не по позиции: правка
	схемы не рушит написанного учеником. Убранный из схемы раздел остаётся
	здесь и не показывается; вернётся в схему — вернутся и данные.
	"""

	def validate(self):
		self.artifact = нормализовать_ключ(self.artifact)
		if not self.artifact:
			frappe.throw(frappe._("Ключ артефакта обязателен"), frappe.ValidationError)

	def on_update(self):
		# Открытая страница документа перечитывает его сама, когда в него
		# пишет агент — из чата или по MCP (learning-services#348). Событие —
		# только ученику и только после коммита: страница, получившая его
		# раньше, прочла бы старые данные.
		frappe.publish_realtime(
			event="artifact_updated",
			user=self.student,
			message={
				"course": self.course,
				"artifact": self.artifact,
				"organization": self.organization,
			},
			after_commit=True,
		)
