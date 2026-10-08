# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

from frappe.model.document import Document
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.constants import ЗАКРЫТЫЕ_РЕПОРТЫ


class AgentCourseReport(Document):
	"""Сигнал агента о том, что мешает курсу работать.

	Своего поля «ученик» нет намеренно: репорт о курсе, а не отчётность по
	людям, а занятие всё равно ведёт к человеку, если разбор упрётся в
	контекст. `Why:` отдельное поле превратило бы список репортов во второй
	отчёт по сотрудникам, против чего и заведена сущность.

	Курс, урок и релиз заполняет метод из занятия: привязка от
	агента указала бы на чужой урок, а вторая копия разъехалась бы с занятием.
	Поэтому они `read_only` — править их в desk значит подделывать сигнал.

	Ученик видит статус и итог своих репортов, а `resolution` — ответ ему.
	`Why:` без ответа ученик не видит смысла писать репорты, а курс правят
	именно по ним (learning-services#286).
	"""

	def validate(self):
		self._отметить_итог()

	def _отметить_итог(self):
		"""Дата итога — при переходе в закрытый статус, сброс — при возврате
		в работу.

		Новый итог снимает и отметку «ученик узнал»: переоткрытый и снова
		закрытый репорт — другой итог, и ученик узнаёт о нём заново. Ставится
		здесь, а не в методе, чтобы статус, сменённый сотрудником в desk,
		доходил до ученика так же.
		"""
		if self.status not in ЗАКРЫТЫЕ_РЕПОРТЫ:
			self.resolved_at = None
			return
		прежний = self.get_doc_before_save()
		if self.resolved_at and прежний and прежний.status == self.status:
			return
		self.resolved_at = now_datetime()
		self.student_notified_at = None
