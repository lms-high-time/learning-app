# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Журнал событий безопасности (learning-services#680).

Одна точка записи: каждое событие — одна JSON-строка в stderr процесса, то есть
в журнал контейнера, откуда журналы стенда уезжают с хоста
(`deploy/README.md` learning-services, «Журналы»). В строке — время в UTC с
зоной, событие, пользователь, IP клиента, метод и путь запроса без параметров,
схема заголовка `Authorization` без значения и поля события.

Кто пишет:

- обёртка `контракт` — отказы контракта о доступе (`ОТКАЗЫ_ДОСТУПА`);
- `after_request` — ответы 401, 403 и 429, неудачный вход, блокировка входа
  и сбой входа;
- `on_login` — вход в сессию: паролем, через Google, «Войти как»;
- `auth_hooks` — запрос с ключом API в заголовке;
- сервер OAuth — выдача и обновление токенов, отказ в них, повтор кода или
  токена обновления;
- отклонение `sid` в параметрах запроса.

`Why:` ошибки прав Frappe пишет в Error Log только при ответе 500 и выше, а
отказы контракта — успешный HTTP-ответ, и не пишутся нигде. Без журнала
неудачные попытки доступа к чужим данным и к методам чужой роли не видны
ни при разборе, ни для оповещения (ASVS 16.2.1, 16.3.1–16.3.4).

Секретов в записи нет: параметры и тело запроса не пишутся вовсе, у
`Authorization` — только схема, а значение поля, чьё имя похоже на секрет,
заменяется на `***` (`СЕКРЕТНЫЕ_ИМЕНА`). Журнал не должен сбивать запрос:
сбой записи проглатывается.
"""

import hashlib
import hmac
import json
import logging
import re
import sys
from datetime import UTC, datetime

import frappe

from lms_frappe_app.agent_learning.errors import НЕТ_ПРАВА, ЧУЖОЕ_ЗАНЯТИЕ
from lms_frappe_app.site_secrets import ЖУРНАЛ_ВХОДА

#: Коды отказа контракта, которые означают «не ваше» — их и пишем. Прочие
#: отказы (попытки кончились, урок не найден) — ход занятия, а не доступ.
ОТКАЗЫ_ДОСТУПА = frozenset({НЕТ_ПРАВА, ЧУЖОЕ_ЗАНЯТИЕ})

#: Ответы, которые всегда пишутся из `after_request`.
СТАТУСЫ_ОТКАЗА = frozenset({401, 403, 429})

#: Имя поля, значение которого не пишется. Те же подстроки, по которым Frappe
#: чистит трассировку и `form_dict` (`frappe.utils.logger.sanitized_dict`), и
#: ещё `sid`, `authorization` и `cookie`.
СЕКРЕТНЫЕ_ИМЕНА = re.compile(r"password|passwd|pwd|secret|token|key|sid|authorization|cookie", re.I)

#: Длина строкового значения в записи. `Why:` поле из запроса (имя входа,
#: путь) иначе раздувало бы журнал по воле клиента.
ПРЕДЕЛ_ЗНАЧЕНИЯ = 200

ИМЯ_ЖУРНАЛА = "lms_frappe_app.security"

#: Схемы `Authorization`, которые пишутся по имени. `Why:` в заголовке без
#: схемы первое слово — сам ключ или токен; такой пишется как `other`.
СХЕМЫ = frozenset({"bearer", "token", "basic"})
#: Схемы входа ключом API: такой запрос пишется каждый. Bearer — токены
#: агентов; их выдача и обновление пишет сервер OAuth, а не каждый запрос.
СХЕМЫ_КЛЮЧА = frozenset({"token", "basic"})


class _Stderr(logging.StreamHandler):
	"""stderr на момент записи, а не на момент создания обработчика.

	`Why:` тестовый прогон и gunicorn подменяют `sys.stderr` после импорта
	модуля; обработчик со старым потоком писал бы в закрытый файл.
	"""

	@property
	def stream(self):
		return sys.stderr

	@stream.setter
	def stream(self, _значение):
		pass


def _настроить() -> logging.Logger:
	журнал = logging.getLogger(ИМЯ_ЖУРНАЛА)
	# Повторный импорт модуля (перезагрузка кода) дал бы второй обработчик и
	# каждую строку дважды; класс при этом новый — узнаём по метке.
	if any(getattr(обработчик, "lms_security", False) for обработчик in журнал.handlers):
		return журнал
	обработчик = _Stderr()
	обработчик.lms_security = True
	обработчик.setFormatter(logging.Formatter("%(message)s"))
	журнал.addHandler(обработчик)
	журнал.setLevel(logging.INFO)
	# Корневой журнал процесса настраивает Frappe или gunicorn; строка события
	# не должна уйти туда второй раз в чужом формате.
	журнал.propagate = False
	return журнал


_ЖУРНАЛ = _настроить()


def _значение(имя: str, значение):
	if СЕКРЕТНЫЕ_ИМЕНА.search(имя):
		return "***"
	if isinstance(значение, dict):
		return {str(к): _значение(str(к), з) for к, з in значение.items()}
	if isinstance(значение, list | tuple):
		return [_значение(имя, з) for з in значение]
	if значение is None or isinstance(значение, bool | int | float):
		return значение
	return str(значение)[:ПРЕДЕЛ_ЗНАЧЕНИЯ]


def схема_авторизации() -> str | None:
	"""Схема заголовка `Authorization` из `СХЕМЫ` или `other` — без значения."""
	заголовок = frappe.get_request_header("Authorization") if getattr(frappe.local, "request", None) else None
	заголовок = (заголовок or "").strip()
	if not заголовок:
		return None
	схема = заголовок.split(" ", 1)[0].lower()
	return схема if схема in СХЕМЫ else "other"


def запись(событие: str, **поля) -> dict:
	"""Запись события: контекст запроса и поля, без секретов."""
	запрос = getattr(frappe.local, "request", None)
	сессия = getattr(frappe.local, "session", None)
	основа = {
		"ts": datetime.now(tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
		"event": событие,
		"user": getattr(сессия, "user", None),
		"ip": getattr(frappe.local, "request_ip", None),
		"method": getattr(запрос, "method", None),
		# Путь без параметров: в них бывают одноразовые ключи и коды.
		"path": getattr(запрос, "path", None),
		"auth": схема_авторизации() if запрос else None,
		"site": getattr(frappe.local, "site", None),
	}
	основа.update(поля)
	return {имя: _значение(имя, значение) for имя, значение in основа.items() if значение is not None}


def записать(событие: str, **поля) -> None:
	"""Пишет событие безопасности. Никогда не роняет вызывающего."""
	try:
		_ЖУРНАЛ.info(json.dumps(запись(событие, **поля), ensure_ascii=False, default=str))
	except Exception:
		pass


def отказ_контракта(функция, код: str, подробности: dict) -> None:
	"""Из обёртки `контракт`: отказ о доступе — в журнал, прочие — нет."""
	try:
		if код in ОТКАЗЫ_ДОСТУПА:
			записать(
				"contract_denied",
				reason=код,
				function=f"{функция.__module__}.{функция.__qualname__}",
				details=подробности or None,
			)
	except Exception:
		pass


def _это_вход(request) -> bool:
	"""Запрос входа паролем — те же признаки, что у `LoginManager` Frappe."""
	form_dict = getattr(frappe.local, "form_dict", None) or {}
	return form_dict.get("cmd") == "login" or getattr(request, "path", None) == "/api/method/login"


def _тип_исключения() -> str | None:
	"""Класс исключения, которым Frappe ответил на запрос, — из тела ответа v1 и v2."""
	ответ = getattr(frappe.local, "response", None) or {}
	if тип := ответ.get("exc_type"):
		return тип
	ошибки = ответ.get("errors")
	if isinstance(ошибки, list) and ошибки and isinstance(ошибки[0], dict):
		return ошибки[0].get("type")
	return None


def _заблокирован(имя: str | None) -> bool:
	"""Вход закрыт счётчиком неудач — по имени или по адресу (`frappe.auth`)."""
	from frappe.auth import get_login_attempt_tracker

	ключи = [ключ for ключ in (имя, getattr(frappe.local, "request_ip", None)) if ключ]
	return any(
		not get_login_attempt_tracker(ключ, raise_locked_exception=False).is_user_allowed() for ключ in ключи
	)


def _кто_входил(имя: str | None) -> dict:
	"""Имя входа — как есть, только если такой пользователь есть.

	`Why:` в поле имени по ошибке вводят пароль; несуществующее имя пишется
	префиксом HMAC секретом сайта — повторы видны, а значение по словарю не
	подобрать, не зная секрета. Секрет — свой, не `encryption_key`
	(`site_secrets`, learning-services#690); до первой миграции его нет, и
	отпечатка тогда нет. Имя — только строка: тело запроса JSON может
	прислать что угодно.
	"""
	if not имя or not isinstance(имя, str):
		return {"login_unknown": True} if имя else {}
	if frappe.db.exists("User", имя):
		return {"login": имя}
	secret = frappe.conf.get(ЖУРНАЛ_ВХОДА)
	if not secret:
		return {"login_unknown": True}
	отпечаток = hmac.new(secret.encode(), имя.encode(), hashlib.sha256).hexdigest()[:12]
	return {"login_unknown": True, "login_hmac": отпечаток}


def записать_отказ(response=None, request=None) -> None:
	"""`after_request`: отказы 401, 403, 429, неудачный вход и блокировка входа.

	Блокировку после неудачных входов Frappe отдаёт исключением
	`SecurityException` без своего кода — ответом 500, а у HTML-ответа (форма
	входа на `/`) типа исключения в теле нет. Поэтому на пути входа любой ответ
	500 и выше — `login_locked`, если тип — `SecurityException` или вход закрыт
	счётчиком, иначе `login_error`. Отказ, который уже записал сам обработчик
	(`frappe.flags.lms_security_logged`), второй раз не пишется.
	"""
	try:
		статус = getattr(response, "status_code", None)
		if статус is None or frappe.flags.get("lms_security_logged"):
			return
		тип = _тип_исключения()
		if _это_вход(request or frappe.request) and (статус in СТАТУСЫ_ОТКАЗА or статус >= 500):
			имя = (getattr(frappe.local, "form_dict", None) or {}).get("usr")
			if статус < 500:
				событие = "login_failed"
			elif тип == "SecurityException" or _заблокирован(имя):
				событие = "login_locked"
			else:
				событие = "login_error"
			записать(событие, status=статус, exc=тип, **_кто_входил(имя))
		elif статус in СТАТУСЫ_ОТКАЗА:
			записать(f"http_{статус}", status=статус, exc=тип)
	except Exception:
		pass


def при_входе(login_manager=None) -> None:
	"""`on_login`: вход в сессию. Пользователь — тот, кто входит, а не текущий.

	Выход Frappe проводит как вход гостя — он не пишется. «Войти как» несёт
	`actor` — того, кто входит под чужим именем.
	"""
	try:
		пользователь = getattr(login_manager, "user", None)
		if not пользователь or пользователь == "Guest":
			return
		текущий = getattr(getattr(frappe.local, "session", None), "user", None)
		actor = текущий if текущий and текущий not in ("Guest", пользователь) else None
		записать("login", user=пользователь, actor=actor)
	except Exception:
		pass


def по_заголовку() -> None:
	"""`auth_hooks`: запрос, вошедший ключом API (`СХЕМЫ_КЛЮЧА`).

	Хук идёт после проверок Frappe (`frappe.auth.validate_auth`): пользователь
	уже выбран по заголовку. Не вошедший так запрос Frappe тут же отклоняет
	ответом 401 — его пишет `записать_отказ`.
	"""
	try:
		if схема_авторизации() not in СХЕМЫ_КЛЮЧА:
			return
		if frappe.session.user in (None, "", "Guest"):
			return
		записать("auth")
	except Exception:
		pass
