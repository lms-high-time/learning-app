# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from lms_frappe_app.agent_learning.doctype.agent_course_artifact.agent_course_artifact import (
	нормализовать_ключ,
)


class AgentStudentArtifact(Document):
	"""Документ ученика по схеме курса: содержимое блоков по ключам.

	Экземпляр один на «ученик + курс + артефакт + пространство» — это держит
	уникальный индекс в базе (`install.обеспечить_индекс_артефактов`).
	Заводится при первом заполнении: пустых документов у платформы хватает и
	без этого.

	Блоки хранятся по ключу, а не по позиции: правка схемы автором не рушит
	написанного учеником. Убранный из схемы блок остаётся здесь и не
	показывается; вернётся в схему — вернётся и текст.
	"""

	def validate(self):
		self.artifact = нормализовать_ключ(self.artifact)
		if not self.artifact:
			frappe.throw(frappe._("Ключ артефакта обязателен"), frappe.ValidationError)
		ключи = set()
		for строка in self.blocks:
			строка.block_key = нормализовать_ключ(строка.block_key)
			if not строка.block_key:
				frappe.throw(frappe._("Ключ блока обязателен"), frappe.ValidationError)
			if строка.block_key in ключи:
				frappe.throw(
					frappe._("Блок {0} записан дважды").format(строка.block_key),
					frappe.ValidationError,
				)
			ключи.add(строка.block_key)

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
