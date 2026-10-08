# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class LearningOrganization(Document):
	"""Компания-клиент.

	Своих порога, лимита попыток и паузы у организации нет: зачёт один на
	платформе (решение владельца, learning-services#353). Урок, сданный лично,
	тогда годится любой компании — прогресс у человека общий. Организация
	решает только, обязателен ли квиз её сотрудникам.
	"""

	def validate(self):
		self.email_domains = _нормализовать_домены(self.email_domains)

	def разрешает_курс(self, course: str) -> bool:
		"""Курс доступен организации.

		Пустой список разрешённых курсов означает «весь каталог»: у большинства
		клиентов ограничений нет, и заставлять их перечислять курс за курсом —
		лишняя работа, которая рано или поздно разъедется с реальностью.
		"""
		разрешённые = [строка.course for строка in self.allowed_courses]
		return not разрешённые or course in разрешённые


def _нормализовать_домены(значение: str | None) -> str:
	"""Домены к нижнему регистру, по одному в строке, без пустых и '@'."""
	if not значение:
		return ""
	домены = []
	for строка in значение.splitlines():
		домен = строка.strip().lstrip("@").lower()
		if домен and домен not in домены:
			домены.append(домен)
	return "\n".join(домены)


def политика_квиза(organization: str | None = None) -> dict:
	"""Действующая политика квиза: лимит и пауза — платформы, обязательность —
	организации, если она её задала. Порог зачёта — у урока в релизе
	(`pass_percentage`), не здесь.

	Одна функция на всех потребителей: иначе правило «пусто значит как в
	настройках» разъедется по местам применения. Незаполненная настройка —
	значение по умолчанию: без лимита, пауза 10 минут (learning-services#353).
	"""
	настройки = frappe.get_cached_doc("Agent Learning Settings")
	политика = {
		"quiz_required": bool(настройки.quiz_required),
		# Ноль — «без лимита».
		"max_attempts": настройки.max_attempts or 0,
		"retry_delay_minutes": настройки.retry_delay_minutes
		if настройки.retry_delay_minutes is not None
		else 10,
	}
	if organization:
		организация = frappe.get_cached_doc("Learning Organization", organization)
		if организация.quiz_required:
			политика["quiz_required"] = организация.quiz_required == "Yes"
	return политика


def организации_пользователя(user: str, роли: tuple[str, ...] | None = None) -> list[str]:
	"""Организации, в которых пользователь состоит сейчас; ушедший — не состоит."""
	from lms_frappe_app.agent_learning.constants import ЧЛЕНСТВО_ДЕЙСТВУЕТ

	фильтры = {"user": user, "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ}
	if роли:
		фильтры["role"] = ("in", роли)
	return frappe.get_all("Organization Membership", filters=фильтры, pluck="organization")
