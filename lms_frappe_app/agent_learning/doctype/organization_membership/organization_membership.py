# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import nowdate

from lms_frappe_app.agent_learning.constants import ЧЛЕНСТВО_ДЕЙСТВУЕТ, ЧЛЕНСТВО_ЗАКРЫТО

#: Роль Frappe, которую даёт руководство организацией.
РОЛЬ_РУКОВОДИТЕЛЯ = "Organization Manager"


class OrganizationMembership(Document):
	"""Участие пользователя в организации.

	Отдельный DocType, а не строка в таблице организации: пользователь может
	состоять в нескольких организациях, и запросы по членству должны
	оставаться индексируемыми.
	"""

	def validate(self):
		self._проверить_повтор()
		self.left_on = (self.left_on or nowdate()) if self.status == ЧЛЕНСТВО_ЗАКРЫТО else None

	def after_insert(self):
		self.догнать_назначения()

	def on_update(self):
		# Вернувшийся догоняет курсы, назначенные без него, как новичок.
		if not self.flags.in_insert and self.has_value_changed("status"):
			self.догнать_назначения()
		обновить_роль_руководителя(self.user)

	def after_delete(self):
		обновить_роль_руководителя(self.user)

	def догнать_назначения(self) -> int:
		"""Выдаёт новому участнику курсы, назначенные организации раньше.

		`Why:` назначение считает состав на момент выдачи. Без этого сотрудник,
		вышедший на работу после назначения курса, остаётся незачисленным —
		и выясняется это в день дедлайна.
		"""
		from lms_frappe_app.agent_learning.doctype.course_allocation.course_allocation import (
			досрочные_назначения_организации,
		)

		if self.status != ЧЛЕНСТВО_ДЕЙСТВУЕТ:
			return 0
		if frappe.db.get_value("Learning Organization", self.organization, "status") != "Active":
			return 0

		создано = 0
		for имя in досрочные_назначения_организации(self.organization):
			создано += frappe.get_doc("Course Allocation", имя).выдать_зачисления(
				участники=[self.user]
			)
		return создано

	def _проверить_повтор(self) -> None:
		уже_есть = frappe.db.exists(
			self.doctype,
			{"user": self.user, "organization": self.organization, "name": ("!=", self.name)},
		)
		if уже_есть:
			frappe.throw(
				frappe._("{0} уже состоит в организации {1}").format(self.user, self.organization),
				frappe.DuplicateEntryError,
			)


def обновить_роль_руководителя(user: str) -> None:
	"""Роль Frappe `Organization Manager` — по действующему руководящему членству.

	`Why:` роль даёт саму возможность смотреть отчёты, членство — по каким
	организациям. Две независимые записи разъезжаются: ушедший руководитель
	сохранял роль, а назначенный забывал её получить и видел пустой отчёт.
	Роль без членства по-прежнему ничего не открывает — это страховка,
	а не единственная защита.
	"""
	from lms_frappe_app.agent_learning.permissions import РОЛИ_МЕНЕДЖЕРА

	руководит = frappe.db.exists(
		"Organization Membership",
		{"user": user, "role": ("in", РОЛИ_МЕНЕДЖЕРА), "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ},
	)
	есть_роль = РОЛЬ_РУКОВОДИТЕЛЯ in frappe.get_roles(user)
	if bool(руководит) == есть_роль:
		return
	пользователь = frappe.get_doc("User", user)
	пользователь.flags.ignore_permissions = True
	if руководит:
		пользователь.add_roles(РОЛЬ_РУКОВОДИТЕЛЯ)
	else:
		пользователь.remove_roles(РОЛЬ_РУКОВОДИТЕЛЯ)
