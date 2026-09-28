# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Зачёт за 100%, без лимита попыток, пауза перед повтором — 10 минут.

Решение владельца (learning-services#353): урок, сданный лично, годится любой
компании, и порог организации для лично пройденного не нужен. Цена — перебор
при безлимите; разницу «понял / перебрал» руководитель видит по номеру
попытки в отчёте.

Патч, а не значения по умолчанию: на развёрнутом стенде поля уже заполнены
прежними 0.8 и тремя попытками, а `after_migrate` заполненное не переписывает.
"""

import frappe

НАСТРОЙКИ = "Agent Learning Settings"


def execute():
	frappe.db.set_single_value(
		НАСТРОЙКИ, {"pass_threshold": 1, "max_attempts": 0, "retry_delay_minutes": 10}
	)
	frappe.clear_document_cache(НАСТРОЙКИ, НАСТРОЙКИ)
