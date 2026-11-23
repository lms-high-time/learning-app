# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class AgentCompetencyEvent(Document):
	"""Запись журнала карты компетенций (learning-services#808): свидетельство
	агента об освоении или пересмотр уровня.

	Журнал не переписывается: уровень ученика выводится из записей, а пересмотр
	— отдельная запись со ссылками на выведенные из расчёта свидетельства.
	Пишет только сервис карты компетенций (`competencies.evidence`) — от агента
	ученика, в транзакции метода. Читают администраторы платформы: в записях
	— работа ученика своими словами агента. Удаления нет ни у кого в правах;
	записи ученика стирает удаление пользователя (`user_data_fields`), как
	журнал квиза.
	"""

	def validate(self):
		# Запись — компетенция каталога и её версия: по версии журнал читается во всех курсах
		# ученика, а по её признакам поставлен уровень (learning-services#839).
		if (self.competency_version or 0) < 1:
			frappe.throw(
				frappe._("У записи журнала компетенций нет версии компетенции каталога"),
				frappe.ValidationError,
			)

	def before_insert(self):
		self.occurred_at = self.occurred_at or now_datetime()

	def before_save(self):
		if self.get_doc_before_save():
			frappe.throw(frappe._("Записи журнала карты компетенций не изменяются"), frappe.ValidationError)
