# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Идентификатор вошедшего для аналитики — без email и имени (learning-services#582).

Решение владельца: события вошедшего, давшего согласие на аналитику,
привязываются к его идентификатору, без email и имени. Учётная запись Frappe —
это email, поэтому наружу уходит не она, а HMAC от неё на секрете сайта:
стабильный для одного человека на всех частях cenda.app и необратимый без
секрета. `Why:` хеш email без секрета подбирается по списку адресов, а
случайный идентификатор пришлось бы где-то хранить и выдавать ещё одним полем.

Секрет — свой ключ `analytics_id_secret` в `site_config.json`, у него одна
цель (learning-services#690). `Why:` ключ, выведенный из `encryption_key`,
связывал бы аналитику с шифрованием паролей: смена одного меняла бы другое.
Секрет заводит миграция, если его нет; восстановление из копии возвращает его
вместе с `encryption_key`. Сменится секрет или учётная запись (смена email) —
сменится и идентификатор, и в аналитике это будет новый человек.

Метод зовёт скрипт согласия сайта (`site/static/analytics.js`), агент его не
зовёт.
"""

import hashlib
import hmac
import secrets

import frappe

from lms_frappe_app.api import контракт, текущий_пользователь

#: Длина идентификатора в шестнадцатеричных знаках: 128 бит — без столкновений
#: на любом мыслимом числе учеников.
ДЛИНА = 32

#: Ключ `site_config.json` с секретом идентификатора.
КЛЮЧ_СЕКРЕТА = "analytics_id_secret"


def обеспечить_секрет() -> None:
	"""Секрет идентификатора в конфигурации сайта; нет — заводится случайный.

	Зовётся при установке и каждой миграции. Есть — не трогается: новый секрет
	сменил бы идентификаторы всех вошедших.
	"""
	if frappe.conf.get(КЛЮЧ_СЕКРЕТА):
		return
	# Установщик Frappe тянет за собой много модулей — только когда пишем.
	from frappe.installer import update_site_config

	secret = secrets.token_hex(32)
	update_site_config(КЛЮЧ_СЕКРЕТА, secret)
	frappe.conf[КЛЮЧ_СЕКРЕТА] = secret


def идентификатор(user: str) -> str:
	"""HMAC-SHA256 учётной записи на секрете идентификатора сайта."""
	secret = frappe.conf.get(КЛЮЧ_СЕКРЕТА)
	if not secret:
		raise RuntimeError(f"нет {КЛЮЧ_СЕКРЕТА} в конфигурации сайта — его заводит bench migrate")
	return hmac.new(secret.encode(), user.strip().lower().encode(), hashlib.sha256).hexdigest()[:ДЛИНА]


@frappe.whitelist(methods=["GET"])
@контракт
def analytics_id() -> dict:
	"""Идентификатор вошедшего для PostHog — один и тот же на всех страницах."""
	return {"id": идентификатор(текущий_пользователь())}
