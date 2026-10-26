# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Пределы записи ученика (learning-services#684): частота вызовов методов
записи и квота места под его файлы. Значения — в `Agent Learning Settings`,
раздел «Пределы записи ученика».

Частота считается на пользователя и метод, а не на адрес. `Why:` вызовы
агентов всех учеников приходят во Frappe с одного адреса — MCP-сервиса, а
`frappe.rate_limiter.rate_limit` считает по адресу или по полю запроса:
предел по адресу был бы общим на всех. Окно фиксированное — счётчик в redis
живёт окно от первого вызова. Сверх предела — `RateLimitExceededError`, то есть
HTTP 429 с заголовком `Retry-After` (секунды до конца окна), и вызов не
выполняется; MCP-сервис передаёт агенту, через сколько повторить.

Флаг System Settings `only_allow_system_managers_to_upload_public_files` не
включается. `Why:` фото профиля и картинки курса Learning загружает
публичными файлами (`Uploader.vue`, `private: false`), и с флагом их загрузка
у всех, кроме System Manager, ломается. Загрузки мимо методов платформы
ограничивает квота (`проверить_файл`).
"""

from __future__ import annotations

import functools
from collections.abc import Callable

import frappe
from frappe.utils import add_days, cint, now_datetime

from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	настройка,
)
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.permissions import АДМИНИСТРАТИВНЫЕ_РОЛИ

#: Группа предела частоты: поле настроек, запасное значение, окно в секундах.
#: `Why:` у групп разный ритм. Заметке и отметке нужен частый ритм занятия.
#: Состояние разговора веб-чат сохраняет несколько раз за каждый ход
#: (learning-services#684), поэтому у него свой, широкий предел. Сохранение
#: домашки несёт файлы до предела сохранения, и его предел — за час.
ГРУППЫ = {
	"запись": ("student_writes_per_minute", 60, 60),
	"состояние": ("state_saves_per_minute", 600, 60),
	"домашка": ("homework_saves_per_hour", 30, 60 * 60),
}

КВОТА_ИСЧЕРПАНА = "storage_quota_exceeded"
#: Запасное значение квоты, МБ за `ОКНО_КВОТЫ`. `Why:` десяток домашек по
#: десятку файлов до 10 МБ с переделками — около сотни мегабайт; вдвое — с
#: запасом на фото профиля.
КВОТА_МБ = 200
#: Квота считается за скользящее окно, а не за всю жизнь аккаунта. `Why:` файлы
#: сдачи ученик не удаляет, и квота навсегда однажды закрыла бы ему сдачу.
ОКНО_КВОТЫ_ДНЕЙ = 30
МБ = 1024 * 1024
#: Флаг записи `File`, квоту которой уже проверил вызывающий (`homework.сохранить`):
#: хук квоты её не пересчитывает.
КВОТА_ПРОВЕРЕНА = "quota_checked"


def частота(группа: str) -> Callable:
	"""Предел частоты метода записи ученика; группа — ключ `ГРУППЫ`.

	Считаются только вызовы внутри HTTP-запроса (`frappe.request`), как у
	`frappe.rate_limiter.rate_limit`: задачи планировщика и тесты зовут функции
	из кода, вне запроса.
	"""
	поле, запасное, окно = ГРУППЫ[группа]

	def декоратор(метод: Callable) -> Callable:
		имя = f"{метод.__module__}.{метод.__qualname__}"

		@functools.wraps(метод)
		def обёртка(*args, **kwargs):
			if frappe.request:
				_учесть(имя, cint(настройка(поле, запасное)) or запасное, окно)
			return метод(*args, **kwargs)

		обёртка.предел_частоты = группа
		return обёртка

	return декоратор


def _учесть(имя: str, предел: int, окно: int) -> None:
	"""Счётчик окна: создание со сроком и прибавка — одной транзакцией redis.

	`Why:` ключ, созданный без срока (сбой между созданием и сроком), закрыл
	бы метод ученику навсегда.
	"""
	ключ = frappe.cache.make_key(f"lms-write-rate:{имя}:{frappe.session.user}:{окно}")
	конвейер = frappe.cache.pipeline(transaction=True)
	конвейер.set(ключ, 0, ex=окно, nx=True)
	конвейер.incr(ключ)
	конвейер.ttl(ключ)
	_, вызовов, осталось = конвейер.execute()
	if вызовов > предел:
		frappe.local.response_headers.set("Retry-After", str(max(cint(осталось), 1)))
		frappe.throw(
			frappe._("Слишком много вызовов подряд — повторите через {0} с").format(max(cint(осталось), 1)),
			frappe.RateLimitExceededError,
		)


class КвотаИсчерпана(frappe.ValidationError):
	"""Файл не помещается в квоту пользователя."""


def квота_байт() -> int:
	return (cint(настройка("student_files_quota_mb", КВОТА_МБ)) or КВОТА_МБ) * МБ


def под_квотой(пользователь: str) -> bool:
	"""Квота — у учеников. Методисты, модераторы, администраторы и сервисная
	роль загружают материалы курсов, и квота ученика им не по размеру.
	Руководитель команды (`Organization Manager`, роли членства `Manager` и
	`Org Admin`) — под квотой: файлы он загружает как ученик."""
	if not пользователь or пользователь in ("Guest", "Administrator"):
		return False
	return not set(frappe.get_roles(пользователь)) & АДМИНИСТРАТИВНЫЕ_РОЛИ


def занято_байт(пользователь: str) -> int:
	"""Сколько места заняли файлы пользователя за `ОКНО_КВОТЫ_ДНЕЙ` — его записи `File`.

	Убранные из ответа файлы домашки тоже: они не удаляются (`CONTRACT.md`,
	`submit_homework`). Одинаковое содержимое Frappe хранит одним файлом, но
	считается каждая запись: иначе квота зависела бы от чужих файлов. Запрос
	идёт по индексу `(owner, creation)` (`install.обеспечить_индекс_файлов`).
	"""
	[(байт,)] = frappe.db.sql(
		"select coalesce(sum(file_size), 0) from `tabFile`"
		" where owner = %s and creation >= %s and is_folder = 0",
		(пользователь, add_days(now_datetime(), -ОКНО_КВОТЫ_ДНЕЙ)),
	)
	return cint(байт)


def проверить_квоту(пользователь: str, новых_байт: int) -> None:
	"""Отказ `storage_quota_exceeded`, если новые файлы не помещаются в квоту."""
	if not новых_байт or not под_квотой(пользователь):
		return
	квота = квота_байт()
	занято = занято_байт(пользователь)
	if занято + новых_байт > квота:
		raise Отказ(
			КВОТА_ИСЧЕРПАНА,
			"Место под файлы на этот месяц закончилось — напиши в поддержку",
			limit_mb=квота // МБ,
			used_mb=round(занято / МБ, 1),
			days=ОКНО_КВОТЫ_ДНЕЙ,
		)


def проверить_файл(файл, method=None) -> None:
	"""`doc_events` `File.before_insert`: квота на любую загрузку пользователя —
	и через `upload_file` Frappe, мимо методов платформы.

	Хук идёт после `File.before_insert`, который уже записал байты на диск:
	отказ откатывает запрос, и Frappe убирает их сам (`File.on_rollback`).
	"""
	if файл.is_folder or файл.flags.get(КВОТА_ПРОВЕРЕНА) or not под_квотой(файл.owner):
		return
	try:
		проверить_квоту(файл.owner, cint(файл.file_size))
	except Отказ as отказ:
		frappe.throw(отказ.сообщение, КвотаИсчерпана)
