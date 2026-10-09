# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning import reports
from lms_frappe_app.agent_learning.constants import ЗАКРЫТЫЕ_РЕПОРТЫ, ИМЯ_СТАТУСА_РЕПОРТА
from lms_frappe_app.agent_learning.errors import Отказ

#: Поля, которые ставит `report_issue` из занятия и жалобы агента. У
#: существующего репорта они не меняются ни на каком пути записи.
ПОЛЯ_ПРИВЯЗКИ = ("session", "course", "lesson", "release", "kind", "question_key", "objective", "text")


class AgentCourseReport(Document):
	"""Сигнал агента о том, что мешает курсу работать.

	Своего поля «ученик» нет намеренно: репорт о курсе, а не отчётность по
	людям, а занятие всё равно ведёт к человеку, если разбор упрётся в
	контекст. `Why:` отдельное поле превратило бы список репортов во второй
	отчёт по сотрудникам, против чего и заведена сущность.

	Курс, урок и релиз заполняет метод из занятия: привязка от
	агента указала бы на чужой урок, а вторая копия разъехалась бы с занятием.
	Поэтому они `read_only` — править их в desk значит подделывать сигнал.
	`read_only` сервер не охраняет, и поля привязки держит `validate`
	(learning-services#528).

	Ученик видит статус и итог своих репортов, а `resolution` — ответ ему.
	`Why:` без ответа ученик не видит смысла писать репорты, а курс правят
	именно по ним (learning-services#286).
	"""

	def validate(self):
		прежний = self.get_doc_before_save()
		if прежний:
			self._привязка_не_меняется(прежний)
		try:
			self._проверить_разбор(прежний)
		except Отказ as отказ:
			frappe.throw(отказ.сообщение, exc=отказ, title=frappe._("Репорт агента"))
		self._отметить_итог()

	def _привязка_не_меняется(self, прежний):
		"""Отказ, если у существующего репорта сменилось поле привязки.

		`Why:` ученик видит в `my_reports` репорты своих занятий, и `session`,
		переставленный на занятие другого ученика, показал бы тому чужой текст
		и ответ. Остальные поля привязки — сам сигнал: подменённые, они
		указывают куратору не на тот урок и не на ту жалобу.
		"""
		for поле in ПОЛЯ_ПРИВЯЗКИ:
			if (self.get(поле) or None) != (прежний.get(поле) or None):
				frappe.throw(
					frappe._("Поле «{0}» ставит жалоба из занятия, его не правят").format(
						self.meta.get_label(поле)
					),
					title=frappe._("Репорт агента"),
				)

	def _проверить_разбор(self, прежний):
		"""Правила `resolve_report` на любом пути записи (`agent_learning.reports`).

		Статус меняется по переходам, `fixed` и `rejected` не остаются без
		ответа ученику, а оригинал дубля — репорт того же курса. `Why:` Desk
		правит статус, ответ и `duplicate_of` по отдельности, и оригинал из
		чужого курса показал бы ученику в `my_reports` ответ о курсе, которого
		он не проходил.
		"""
		стало = ИМЯ_СТАТУСА_РЕПОРТА.get(self.status)
		if прежний and прежний.status != self.status:
			reports.проверить_переход(ИМЯ_СТАТУСА_РЕПОРТА.get(прежний.status), стало)
		reports.проверить_ответ(стало, self.resolution)
		if стало == "duplicate" or self.duplicate_of:
			reports.проверить_оригинал(self.name, self.course, self.duplicate_of)

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
