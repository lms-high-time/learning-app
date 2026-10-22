# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Хеш пароля: argon2id, хеш Frappe перехешируется при входе
(learning-services#676).

Frappe хранит пароль хешем контекста passlib `frappe.utils.password.passlibctx`
с умолчаниями passlib: pbkdf2_sha256 с 29 000 раундов. OWASP Password Storage
Cheat Sheet и ASVS 11.4.2 называют argon2id или pbkdf2_sha256 от 600 000
раундов. Здесь контекст перенастраивается на месте: argon2id по умолчанию,
pbkdf2_sha256 — устаревшая схема. Хеш Frappe по-прежнему проверяется, а
`check_password` Frappe, увидев устаревший хеш (`needs_update`), сам
перезаписывает его новым при успешном входе.

Перенастраивается тот же объект (`load`), а не подменяется имя модуля: кто
импортировал `passlibctx` по имени, видит те же настройки.

Где настраивается: `before_request` — до входа и смены пароля в вебе,
`before_job` — до фоновой задачи. Команды `bench` (`set-admin-password`) идут
мимо обоих и пишут хеш Frappe — его перезапишет следующий вход.

Библиотека argon2 (`argon2-cffi`) ставится в образ bench. Без неё контекст
остаётся на pbkdf2_sha256, но с 600 000 раундов: argon2 по умолчанию без
библиотеки уронил бы каждый вход, где хеш помечен к перезаписи. Откат образа
на версию без библиотеки, когда хеши уже argon2id, ломает вход: такой хеш без
неё не проверить.

Хеш в переменных назван латиницей (`hashed_password`) вопреки стилю. `Why:`
трассировку с переменными Frappe чистит по латинским подстрокам имён —
`password`, `secret`, `token`, `key` (learning-services#664).
"""

import frappe.utils.password
from passlib.hash import argon2

#: argon2id с параметрами второго рекомендованного набора RFC 9106 (§4):
#: 64 МиБ памяти, 3 прохода, 4 потока — они же умолчания argon2-cffi.
#: `Why:` набор с запасом над минимумом OWASP (19 МиБ, 2 прохода) при ~70 мс на
#: проверку (замер на образе bench, learning-services#676). Параметры
#: записаны явно: смена умолчаний passlib не должна менять хеш молча.
#: Поменяются здесь — прежние хеши argon2id тоже перезапишутся при входе.
ARGON2 = {
	"argon2__type": "ID",
	"argon2__memory_cost": 65536,
	"argon2__rounds": 3,
	"argon2__parallelism": 4,
}
ARGON2_PREFIX = "$argon2id$v=19$m=65536,t=3,p=4$"

#: Раунды pbkdf2_sha256, если argon2 недоступна, — порог OWASP.
PBKDF2_ROUNDS = 600_000

_настроен = False


def настройки(есть_argon2: bool) -> dict:
	"""Настройки контекста passlib: argon2id, а без библиотеки — pbkdf2_sha256
	с порогом раундов. В обоих случаях хеш Frappe проверяется и помечается к
	перезаписи."""
	if есть_argon2:
		return {
			"schemes": ["argon2", "pbkdf2_sha256"],
			"default": "argon2",
			"deprecated": ["pbkdf2_sha256"],
			**ARGON2,
		}
	return {
		"schemes": ["pbkdf2_sha256", "argon2"],
		"default": "pbkdf2_sha256",
		"pbkdf2_sha256__default_rounds": PBKDF2_ROUNDS,
		"pbkdf2_sha256__min_rounds": PBKDF2_ROUNDS,
	}


def настроить(**_kwargs) -> None:
	"""`before_request` и `before_job`: контекст Frappe — на настройки модуля,
	один раз за процесс. `before_job` передаёт имя задачи и её аргументы —
	они здесь не нужны."""
	global _настроен
	if _настроен:
		return
	frappe.utils.password.passlibctx.load(настройки(argon2.has_backend()))
	_настроен = True
