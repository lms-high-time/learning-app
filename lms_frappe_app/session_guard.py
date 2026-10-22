# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Сессия браузера: только из cookie и не дольше срока (learning-services#666).

Самооценка ASVS, learning-services#664: 7.2.4 — Frappe берёт `sid` и из
параметров запроса, 7.3.2 — у сессии нет абсолютного срока, только простой.

Тело запроса (в нём бывает пароль входа) и данные сессии (токен CSRF) лежат в
переменных с латинским `secrets` вопреки стилю. `Why:` трассировку с
переменными (`frappe.get_traceback(with_context=True)` — Error Log и Sentry)
Frappe чистит по латинским подстрокам имён: `password`, `secret`, `token`,
`key` (learning-services#664).
"""

from datetime import UTC, datetime, timedelta

import frappe
import orjson
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Response

from lms_frappe_app import security_log

#: Абсолютный срок сессии от входа, сколько бы человек ни был активен.
#: `Why:` ASVS 7.3.2 требует срок, после которого вход повторяется;
#: NIST SP 800-63B-4 для уровня AAL1 (пароль или внешний вход, без второго
#: фактора — уровень учеников) называет 30 дней. Простой ограничивает
#: `session_expiry` в System Settings, этот срок его не заменяет.
СРОК_СЕССИИ = timedelta(days=30)

ОТКАЗ_SID = "Сессия в адресе запроса не принимается: войдите через страницу входа."


def sid_в_параметрах(запрос) -> bool:
	"""Пришёл ли `sid` параметром: в адресе, в форме или в JSON-теле.

	Те же три источника, из которых Frappe собирает `form_dict`
	(`frappe.app.make_form_dict`), и тело разбирается так же — `orjson` по
	тексту тела, иначе `sid`, который увидит Frappe, мы бы не увидели. Тело,
	которое не разобрать, Frappe отклоняет сам; сбой разбора здесь — «нет».
	Сам `form_dict` уже не годится: `sid` из него Frappe вынимает, поднимая
	сессию. Адрес и форма смотрятся и у JSON-запроса: Frappe их тогда не
	читает, но токен в адресе всё равно оседает в журналах.
	"""
	if "sid" in запрос.args or "sid" in запрос.form:
		return True
	body_secrets = запрос.get_data(as_text=True)
	if not (body_secrets and запрос.is_json):
		return False
	try:
		parsed_body_secrets = orjson.loads(body_secrets)
	except Exception:  # в том числе RecursionError глубокой вложенности
		return False
	return isinstance(parsed_body_secrets, dict) and "sid" in parsed_body_secrets


def отклонить_sid_из_запроса() -> None:
	"""`before_request`: запрос с `sid` в параметрах отклоняется ответом 400.

	`Why:` Frappe поднимает сессию из параметра `sid` раньше cookie
	(`frappe.sessions.Session.__init__`) и в ответе ставит её cookie. Ссылка
	`…?sid=<чужая сессия>` входит человеку в чужой аккаунт (фиксация сессии), а
	токен в адресе оседает в журналах и заголовке Referer. Хук — первым в
	`before_request`: следующие могли бы ответить переадресацией и с ней
	поставить cookie.

	Законных путей у параметра нет. `login_via_token` Frappe кладёт `sid` в
	`form_dict` сам, внутри обработчика, после этого хука; realtime, печать в
	PDF, веб-чат и MCP-сервис передают сессию cookie. Метод с аргументом `sid`
	его и так не получил бы: Frappe вынимает параметр из `form_dict`. Ломается
	только `bench browse --user`: он открывает браузер по `/app?sid=…`.
	"""
	if not frappe.request or not sid_в_параметрах(frappe.request):
		return
	security_log.записать("sid_rejected")
	не_ставить_cookie()
	raise HTTPException(response=Response(ОТКАЗ_SID, status=400, mimetype="text/plain"))


def не_ставить_cookie(response=None, request=None) -> None:
	"""`after_request`: на запрос с `sid` в параметрах — ни одной cookie.

	`Why:` `before_request` не видит запросов, которые Frappe отклонил
	раньше, — например, POST без CSRF-токена, — а cookie сессии из параметра
	к тому моменту уже в очереди, и ответ об ошибке её поставит. Хуки
	`after_request` Frappe зовёт на любом ответе, до записи cookie в ответ
	(`frappe.app.application`). Удаление cookie тоже снимается: несуществующий
	`sid` в ссылке иначе выводил бы человека из его собственной сессии.
	"""
	запрос = request or frappe.request
	if not запрос or not sid_в_параметрах(запрос):
		return
	if менеджер := getattr(frappe.local, "cookie_manager", None):
		менеджер.cookies.clear()
		менеджер.to_delete.clear()


def конец_сессии(прежний: str | None, сейчас: datetime) -> str:
	"""Конец сессии: вход + `СРОК_СЕССИИ`, или прежний, если он раньше.

	Прежний конец бывает у «Войти как» (`LoginManager.impersonate` переносит
	конец исходной сессии) и у `bench browse --session-end`. Формат — ISO с
	часовым поясом: его сравнивает с `datetime.now(tz=UTC)` сам Frappe.
	"""
	наш = сейчас + СРОК_СЕССИИ
	if прежний:
		был = datetime.fromisoformat(прежний)
		был = был if был.tzinfo else был.replace(tzinfo=UTC)
		наш = min(наш, был)
	return наш.isoformat()


def истекла(конец: str | None, сейчас: datetime) -> bool:
	"""Прошёл ли абсолютный срок `session_end`; без пояса — UTC, как в `конец_сессии`."""
	if not конец:
		return False
	срок = datetime.fromisoformat(конец)
	return сейчас > (срок if срок.tzinfo else срок.replace(tzinfo=UTC))


def завершить_истёкшую() -> None:
	"""`before_request`: сессия с прошедшим `session_end` завершается, и запрос
	идёт гостем.

	`Why:` Frappe сверяет `session_end` только при чтении сессии из кэша
	(`Session.get_session_data_from_cache`). Когда кэша нет — redis
	перезапущен или очищен, — сессия читается из базы без этой проверки и
	возвращается в кэш, то есть живёт после срока (learning-services#691).
	Сессию к хукам Frappe уже поднял (`init_request` → `HTTPRequest`), поэтому
	здесь она удаляется, а вход поднимается заново — так же, как Frappe
	поступает с истёкшей сессией из кэша: гость, `session_expired` в ответе,
	cookie сняты. Хук — сразу после отказа `sid` из параметров, раньше
	хуков, которые читают пользователя.
	"""
	from frappe.auth import LoginManager
	from frappe.sessions import delete_session

	сессия = getattr(frappe.local, "session_obj", None)
	if not сессия or сессия.user == "Guest":
		return
	if not истекла(сессия.data.data.get("session_end"), datetime.now(tz=UTC)):
		return
	delete_session(сессия.sid, user=сессия.user, reason="Session Expired")
	frappe.local.login_manager = LoginManager()


def ограничить_срок_сессии(login_manager=None) -> None:
	"""`on_session_creation`: у новой сессии — абсолютный срок `session_end`.

	Frappe проверяет `session_end` при чтении сессии из кэша и удаляет
	истёкшую (`Session.get_session_data_from_cache`); при чтении из базы
	срок сверяет `завершить_истёкшую`.

	Сессия к этому хуку уже записана (`Session.start`), поэтому срок
	дописывается в запись в базе и в кэше. Коммит — запроса входа: POST
	коммитит Frappe, вход через Google — `login_oauth_user` сам.
	"""
	сессия = getattr(frappe.local, "session_obj", None)
	if not сессия or сессия.user == "Guest":
		return
	session_secrets = сессия.data.data
	конец = конец_сессии(session_secrets.get("session_end"), datetime.now(tz=UTC))
	if session_secrets.get("session_end") == конец:
		return
	session_secrets.session_end = конец
	сессии = frappe.qb.DocType("Sessions")
	(
		frappe.qb.update(сессии)
		.set(сессии.sessiondata, frappe.as_json(session_secrets, indent=None, separators=(",", ":")))
		.where(сессии.sid == сессия.sid)
	).run()
	frappe.cache.hset("session", сессия.sid, сессия.data)
