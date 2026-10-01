# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AgentScenarioState(Document):
	"""Разговор сценария боковой панели вне урока — одна запись на ученика и ключ.

	Урочный разговор живёт в `Agent Chat State`: его ключ — занятие. Здесь
	сценарии без занятия (интервью о профиле), поэтому ключ — ученик и
	сценарий. Уникальность пары держит индекс из `install.py`.

	Чистое хранение, как у урочного: формат пишет и разбирает MCP-сервис.
	Истории изменений нет намеренно — каждое сохранение замещает блоб
	целиком, и версии копили бы копии всей переписки.
	"""
