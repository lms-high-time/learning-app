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

Где настраивается: `before_login` — до проверки пароля (вход паролем Frappe
проверяет ещё при разборе запроса, раньше `before_request`), `before_request`
— до смены пароля в вебе, `before_job` — до фоновой задачи. Команды `bench`
(`set-admin-password`) идут мимо всех и пишут хеш Frappe — его перезапишет
следующий вход.

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

#: argon2id с набором OWASP Password Storage Cheat Sheet: 19 МиБ памяти,
#: 2 прохода, 1 поток. `Why:` память берёт каждая проверка, и неверный пароль
#: тоже, а память воркеров backend ограничена — набор RFC 9106 с 64 МиБ и
#: 4 потоками перебором входа съедал бы память и процессор воркеров
#: (learning-services#676). Параметры записаны явно: смена
#: умолчаний passlib не должна менять хеш молча. Поменяются здесь — прежние
#: хеши argon2id тоже перезапишутся при входе.
ARGON2 = {
	"argon2__type": "ID",
	"argon2__memory_cost": 19456,
	"argon2__rounds": 2,
	"argon2__parallelism": 1,
}
ARGON2_PREFIX = "$argon2id$v=19$m=19456,t=2,p=1$"

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
	"""`before_login`, `before_request` и `before_job`: контекст Frappe — на
	настройки модуля, один раз за процесс. Хуки передают менеджер входа или
	имя задачи с аргументами — они здесь не нужны."""
	global _настроен
	if _настроен:
		return
	frappe.utils.password.passlibctx.load(настройки(_есть_argon2()))
	_настроен = True


def _есть_argon2() -> bool:
	"""Доступна ли библиотека argon2. Сбой самой проверки — как её отсутствие.

	`Why:` хук стоит на каждом запросе, пока не отработает: исключение из
	проверки backend легло бы 500 на весь сайт, а не только на вход.
	"""
	try:
		return argon2.has_backend()
	except Exception:
		frappe.log_error(title="Хеш пароля: проверка argon2 не прошла, работает pbkdf2_sha256")
		return False
