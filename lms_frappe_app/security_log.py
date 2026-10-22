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
- `after_request` — ответы 401, 403 и 429, неудачный вход и блокировка входа;
- `on_login` — вход в сессию: паролем, через Google, «Войти как»;
- `auth_hooks` — запрос с ключом API или токеном в заголовке;
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

import json
import logging
import re
import sys
from datetime import UTC, datetime

import frappe

from lms_frappe_app.agent_learning.errors import НЕТ_ПРАВА, ЧУЖОЕ_ЗАНЯТИЕ

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
	обработчик = _Stderr()
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
	"""Схема заголовка `Authorization` (`bearer`, `token`, `basic`) — без значения."""
	заголовок = frappe.get_request_header("Authorization") if getattr(frappe.local, "request", None) else None
	схема = (заголовок or "").strip().split(" ", 1)[0].lower()
	return схема[:20] or None


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
	if код in ОТКАЗЫ_ДОСТУПА:
		записать(
			"contract_denied",
			reason=код,
			function=f"{функция.__module__}.{функция.__qualname__}",
			details=подробности or None,
		)


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


def записать_отказ(response=None, request=None) -> None:
	"""`after_request`: отказы 401, 403, 429, неудачный вход и блокировка входа.

	Блокировку после неудачных входов Frappe отдаёт исключением
	`SecurityException` без своего кода — ответом 500, поэтому она узнаётся
	по типу исключения, а не по статусу.
	"""
	try:
		статус = getattr(response, "status_code", None)
		if статус is None:
			return
		тип = _тип_исключения()
		if _это_вход(request or frappe.request) and (статус in СТАТУСЫ_ОТКАЗА or тип == "SecurityException"):
			событие = "login_locked" if тип == "SecurityException" else "login_failed"
			# Имя, которым пытались войти: учётной записи с ним может и не быть.
			имя = (getattr(frappe.local, "form_dict", None) or {}).get("usr")
			записать(событие, status=статус, exc=тип, login=имя)
		elif статус in СТАТУСЫ_ОТКАЗА:
			записать(f"http_{статус}", status=статус, exc=тип)
	except Exception:
		pass


def при_входе(login_manager=None) -> None:
	"""`on_login`: вход в сессию. Пользователь — тот, кто входит, а не текущий."""
	записать("login", user=getattr(login_manager, "user", None))


def по_заголовку() -> None:
	"""`auth_hooks`: запрос, вошедший ключом API или токеном из заголовка.

	Хук идёт после проверок Frappe (`frappe.auth.validate_auth`): пользователь
	уже выбран по заголовку. Не вошедший так запрос Frappe тут же отклоняет
	ответом 401 — его пишет `записать_отказ`.
	"""
	try:
		if not схема_авторизации():
			return
		if frappe.session.user in (None, "", "Guest"):
			return
		записать("auth")
	except Exception:
		pass
