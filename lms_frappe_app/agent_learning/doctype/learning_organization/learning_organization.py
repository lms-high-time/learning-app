# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class LearningOrganization(Document):
	"""Компания-клиент.

	Здесь же живёт политика квиза: требования к строгости зачёта у
	корпоративных клиентов разные, и задавать их глобально нельзя.
	Незаполненное поле означает «как в общих настройках» — так организация не
	обязана дублировать значения, которые её устраивают.
	"""

	def validate(self):
		if self.pass_threshold and not 0 < self.pass_threshold <= 1:
			frappe.throw(frappe._("Порог прохождения задаётся долей от 0 до 1"))
		# Ноль здесь читается как «наследовать», см. `политика_квиза`.
		if self.max_attempts is not None and self.max_attempts < 0:
			frappe.throw(frappe._("Число попыток не может быть отрицательным"))
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


#: Поля политики и их соответствие общим настройкам.
ПОЛЯ_ПОЛИТИКИ = ("pass_threshold", "max_attempts", "retry_delay_hours")


def политика_квиза(organization: str | None = None) -> dict:
	"""Действующая политика квиза для организации.

	Значение организации перекрывает общее; незаданное — берётся из настроек.
	Одна функция на всех потребителей: иначе правило «пусто значит как в
	настройках» разъедется по местам применения.
	"""
	настройки = frappe.get_cached_doc("Agent Learning Settings")
	политика = {
		"quiz_required": bool(настройки.quiz_required),
		"pass_threshold": настройки.pass_threshold or 0.8,
		"max_attempts": настройки.max_attempts if настройки.max_attempts is not None else 3,
		"retry_delay_hours": настройки.retry_delay_hours
		if настройки.retry_delay_hours is not None
		else 1,
	}
	if not organization:
		return политика

	организация = frappe.get_cached_doc("Learning Organization", organization)
	if организация.quiz_required:
		политика["quiz_required"] = организация.quiz_required == "Yes"
	for поле in ПОЛЯ_ПОЛИТИКИ:
		# Ноль у организации означает «как в общих настройках», а не «без
		# лимита». Frappe отдаёт незаполненный Int нулём, и отличить «не
		# трогали» от «выставили ноль» здесь невозможно; выбрано то значение,
		# которое безопаснее при недосмотре — унаследовать ограничение, а не
		# снять его. «Без лимита» задаётся в общих настройках.
		if значение := организация.get(поле):
			политика[поле] = значение
	return политика


def организации_пользователя(user: str, роли: tuple[str, ...] | None = None) -> list[str]:
	"""Организации, в которых пользователь состоит сейчас; ушедший — не состоит."""
	from lms_frappe_app.agent_learning.constants import ЧЛЕНСТВО_ДЕЙСТВУЕТ

	фильтры = {"user": user, "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ}
	if роли:
		фильтры["role"] = ("in", роли)
	return frappe.get_all("Organization Membership", filters=фильтры, pluck="organization")
