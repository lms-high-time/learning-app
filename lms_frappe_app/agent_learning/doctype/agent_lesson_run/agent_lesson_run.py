# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AgentLessonRun(Document):
	"""Прохождение урока учеником: пункты и цели по ключам релиза (learning-services#504).

	Одно на «ученик, курс, ключ урока» — уникальный индекс
	(`install.обеспечить_индекс_прохождений`). Создаёт, сверяет с релизом и
	пересчитывает статусы `agent_learning.runs.service`.
	"""
