# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Идентификатор вошедшего для аналитики — без email и имени (learning-services#582).

Решение владельца: события вошедшего, давшего согласие на аналитику,
привязываются к его идентификатору, без email и имени. Учётная запись Frappe —
это email, поэтому наружу уходит не она, а HMAC от неё на секрете сайта:
стабильный для одного человека на всех частях cenda.app и необратимый без
секрета. `Why:` хеш email без секрета подбирается по списку адресов, а
случайный идентификатор пришлось бы где-то хранить и выдавать ещё одним полем.

Метод зовёт скрипт согласия сайта (`site/static/analytics.js`), агент его не
зовёт.
"""

import hashlib
import hmac

import frappe
from frappe.utils.password import get_encryption_key

from lms_frappe_app.api import контракт, текущий_пользователь

#: Длина идентификатора в шестнадцатеричных знаках: 128 бит — без столкновений
#: на любом мыслимом числе учеников.
ДЛИНА = 32


def идентификатор(user: str) -> str:
	"""HMAC-SHA256 учётной записи на ключе, выведенном из секрета сайта."""
	ключ = hashlib.sha256(b"cenda-analytics-id:" + get_encryption_key().encode()).digest()
	return hmac.new(ключ, user.strip().lower().encode(), hashlib.sha256).hexdigest()[:ДЛИНА]


@frappe.whitelist(methods=["GET"])
@контракт
def analytics_id() -> dict:
	"""Идентификатор вошедшего для PostHog — один и тот же на всех страницах."""
	return {"id": идентификатор(текущий_пользователь())}
