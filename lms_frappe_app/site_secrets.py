# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Секреты сайта с одной целью (learning-services#690).

Каждый — свой ключ в `site_config.json`, а не производное от
`encryption_key`. `Why:` ключ, выведенный из `encryption_key`, связывал бы
его цель с шифрованием паролей: смена или утечка одного задевала бы другое.

Секрет заводит установка и каждая миграция, если его нет; восстановление из
копии возвращает его вместе с `encryption_key` (`deploy/restore.sh`
learning-services). Лениво на запросе он не заводится: веб-воркеры завели бы
наперегонки разные значения.
"""

import secrets

import frappe

#: Идентификатор вошедшего для аналитики (`api/analytics.py`).
АНАЛИТИКА = "analytics_id_secret"
#: Отпечаток несуществующего имени входа в журнале событий (`security_log.py`).
ЖУРНАЛ_ВХОДА = "security_log_secret"

ВСЕ = (АНАЛИТИКА, ЖУРНАЛ_ВХОДА)


def обеспечить() -> None:
	"""Заводит отсутствующие секреты; заведённые не трогает — новый значил бы
	новые идентификаторы и отпечатки."""
	недостаёт = [ключ for ключ in ВСЕ if not frappe.conf.get(ключ)]
	if not недостаёт:
		return
	# Установщик Frappe тянет за собой много модулей — только когда пишем.
	from frappe.installer import update_site_config

	for ключ in недостаёт:
		secret = secrets.token_hex(32)
		update_site_config(ключ, secret)
		frappe.conf[ключ] = secret
