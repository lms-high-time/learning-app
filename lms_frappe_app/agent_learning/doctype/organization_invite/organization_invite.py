# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class OrganizationInvite(Document):
	"""Ссылка-приглашение в организацию (learning-services#363).

	Кто знает ключ — вступает участником, поэтому ключ длинный и случайный, а
	ссылку руководитель может отозвать. Одна ссылка — на любое число людей:
	руководитель отправляет её сам, писем платформа пока не шлёт. Заводят и
	отзывают её методы «Команды», а не desk: у руководителя desk закрыт.
	"""

	def before_insert(self):
		if not self.token:
			# Не имя записи: имя видно в журнале изменений и ссылках desk, а
			# ключ — пропуск в организацию.
			self.token = frappe.generate_hash(length=32)
