# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from lms_frappe_app.agent_learning.constants import ВИДЫ_ОТВЕТА, ВИДЫ_СРОКА
from lms_frappe_app.agent_learning.errors import Отказ

НЕВЕРНЫЙ_ВИД_ОТВЕТА = "invalid_answer_mode"
НЕВЕРНЫЙ_СРОК = "invalid_due"


class AgentLessonHomework(Document):
	"""Домашнее задание урока от автора: одно на урок, одинаковое для всех.

	Сдачи ссылаются на задание, а не копируют его: исправленная формулировка
	видна всем сразу, прошлые редакции хранит `track_changes`. Проверка полей
	живёт здесь — одна на desk и на авторинг (learning-services#439).
	"""

	def validate(self):
		if self.answer_mode not in ВИДЫ_ОТВЕТА:
			raise Отказ(НЕВЕРНЫЙ_ВИД_ОТВЕТА, "Вид ответа: text, files или text_and_files", answer_mode=self.answer_mode)
		if self.due_mode not in ВИДЫ_СРОКА:
			raise Отказ(НЕВЕРНЫЙ_СРОК, "Срок: none, relative или absolute", due_mode=self.due_mode)
		if self.due_mode == "relative" and not (self.due_days and int(self.due_days) > 0):
			raise Отказ(НЕВЕРНЫЙ_СРОК, "Относительному сроку нужно число дней больше нуля", due_mode=self.due_mode)
		if self.due_mode == "absolute" and not self.due_date:
			raise Отказ(НЕВЕРНЫЙ_СРОК, "Абсолютному сроку нужна дата", due_mode=self.due_mode)
		# Why: лишнее поле другого режима читалось бы как действующий срок.
		if self.due_mode != "relative":
			self.due_days = None
		if self.due_mode != "absolute":
			self.due_date = None
