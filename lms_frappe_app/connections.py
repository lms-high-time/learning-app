# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Агенты и сессии пользователя: что подключено к аккаунту и где он вошёл
(learning-services#669).

Самооценка ASVS, learning-services#664: 10.4.9 и 10.7.3 — пользователь видит
агентов, которым дал доступ, и отзывает его; 7.5.2 — видит свои сессии
браузера и завершает их, подтвердив вход паролем.

Чужое здесь не видно и не отзывается: каждая выборка — по пользователю, которого
передаёт вызывающий (страница и методы берут его из сессии, а не из
параметра), и действие ищет запись по отпечатку только среди его записей. Ни
токенов, ни `sid` наружу не уходит: строка названа отпечатком — началом
SHA-256, — и сервер сверяет его с отпечатками своих записей.

Переменные с паролем, токенами и `sid` названы латиницей (`password`,
`*_token`) вопреки стилю. `Why:` трассировку с переменными
(`frappe.get_traceback(with_context=True)` — Error Log и Sentry) Frappe
чистит по латинским подстрокам имён: `password`, `secret`, `token`, `key`.
Кириллическое `пароль` ушло бы в журнал как есть (learning-services#664).
"""

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.auth import get_login_attempt_tracker
from frappe.query_builder import Order
from frappe.utils import get_datetime, now_datetime
from frappe.utils.password import check_password

from lms_frappe_app.agent_learning import cimd
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.oauth_client import ВЕБ_ЧАТ, агент_клиента, отозвать_подключение
from lms_frappe_app.agent_learning.oauth_server import ПОЛЕ_НАЧАЛА, ПОЛЕ_СЕМЬИ, СРОК_СЕМЬИ
from lms_frappe_app.install import КЛЮЧ_ЧАТА_ID

#: Знаков отпечатка: 64 бита. Сверяется только со своими записями, поэтому
#: подбирать его бессмысленно — длина нужна лишь против случайных совпадений.
ДЛИНА_ОТПЕЧАТКА = 16

ПОДКЛЮЧЕНИЕ_НЕ_НАЙДЕНО = "connection_not_found"
СЕССИЯ_НЕ_НАЙДЕНА = "session_not_found"
ТЕКУЩАЯ_СЕССИЯ = "session_is_current"
НУЖЕН_ПАРОЛЬ = "password_required"
#: У аккаунта нет пароля вовсе: подтверждать нечем, задаётся по ссылке из письма.
ПАРОЛЯ_НЕТ = "password_not_set"
НЕВЕРНЫЙ_ПАРОЛЬ = "password_incorrect"
ПОПЫТКИ_КОНЧИЛИСЬ = "password_attempts_exceeded"

#: Причина в журнале активности — та же, что у завершения сессии в desk Frappe.
ПРИЧИНА_ЗАВЕРШЕНИЯ = "Force Logged out by the user"

#: Предел длины пароля Frappe (`frappe.auth.MAX_PASSWORD_SIZE`): длиннее хешу
#: не отдаём.
ПРЕДЕЛ_ПАРОЛЯ = 512


def отпечаток(*части: str) -> str:
	return hashlib.sha256("\n".join(части).encode()).hexdigest()[:ДЛИНА_ОТПЕЧАТКА]


def _тот_же(а: str, б: str) -> bool:
	return hmac.compare_digest(а.encode(), б.encode())


# --- агенты ---


def свести_подключения(token_rows: list, сейчас: datetime) -> dict[str, dict]:
	"""Действующие токены пользователя → подключения по отпечатку.

	Подключение — клиент и семья токенов (`oauth_server`): один вход агента и
	все обновления после него. Семья, которой вышел `СРОК_СЕМЬИ`, уже не
	обновится, и её нет, когда истёк и последний токен доступа: пока он жив,
	его ещё есть что отзывать. «Подключён» — начало семьи, «последняя активность» —
	выдача её последнего токена: агент получает новый, пока пользуется доступом.
	Токен до learning-services#665 без семьи — сам её корень, начало — его выдача.
	Семья такого корня — имя записи, то есть сам токен доступа: отсюда `family_token`.
	"""
	connection_tokens: dict[str, dict] = {}
	for token_row in token_rows:
		family_token = token_row.get(ПОЛЕ_СЕМЬИ) or token_row.name
		начало = get_datetime(token_row.get(ПОЛЕ_НАЧАЛА) or token_row.creation)
		срок_доступа = token_row.get("expiration_time")
		if сейчас > начало + СРОК_СЕМЬИ and not (срок_доступа and сейчас < срок_доступа):
			continue
		ключ = отпечаток(token_row.client, family_token)
		connection_token = connection_tokens.setdefault(
			ключ,
			{
				"client": token_row.client,
				"family_token": family_token,
				"connected": начало,
				"last_active": token_row.creation,
			},
		)
		connection_token["connected"] = min(connection_token["connected"], начало)
		connection_token["last_active"] = max(connection_token["last_active"], token_row.creation)
	return connection_tokens


def _подключения(пользователь: str) -> dict[str, dict]:
	token_rows = frappe.get_all(
		"OAuth Bearer Token",
		filters={"user": пользователь, "status": "Active"},
		fields=["name", "client", "creation", "expiration_time", ПОЛЕ_СЕМЬИ, ПОЛЕ_НАЧАЛА],
	)
	return свести_подключения(token_rows, now_datetime())


def сайт_клиента(клиент: str) -> str | None:
	"""Сайт, который публикует документ клиента по адресу (`cimd.py`).

	`Why:` имя агента клиент пишет о себе сам, а документ лежит на его сайте —
	это и показывается человеку, как на экране подтверждения.
	"""
	if not cimd.это_адрес_клиента(клиент):
		return None
	return urlsplit(клиент).hostname


def подключения(пользователь: str) -> list[dict]:
	"""Подключённые агенты пользователя, свежие вперёд — для страницы.

	`app_name` — имя, которым клиент назвал себя сам: шаблон его экранирует.
	"""
	connection_tokens = _подключения(пользователь)
	имена_клиентов = {connection_token["client"] for connection_token in connection_tokens.values()}
	клиенты = {
		к.name: к
		for к in frappe.get_all(
			"OAuth Client",
			filters={"name": ("in", list(имена_клиентов) or [""])},
			fields=["name", "app_name", "redirect_uris"],
		)
	}
	клиент_чата = frappe.conf.get(КЛЮЧ_ЧАТА_ID)
	строки = []
	for ключ, connection_token in connection_tokens.items():
		имя_клиента = connection_token["client"]
		клиент = клиенты.get(имя_клиента) or frappe._dict()
		агент = агент_клиента(имя_клиента, клиент.redirect_uris, клиент_чата)
		строки.append(
			{
				"id": ключ,
				"agent": агент,
				"app_name": клиент.app_name or "",
				"site": сайт_клиента(имя_клиента),
				"web_chat": агент == ВЕБ_ЧАТ,
				"connected": connection_token["connected"],
				"last_active": connection_token["last_active"],
			}
		)
	return sorted(строки, key=lambda с: с["last_active"], reverse=True)


def отключить(пользователь: str, подключение: str) -> None:
	"""Отозвать подключение пользователя по его отпечатку.

	Отпечаток ищется только среди своих подключений: чужое не находится и не
	отзывается — тот же отказ, что у уже отключённого.
	"""
	for ключ, connection_token in _подключения(пользователь).items():
		if _тот_же(ключ, подключение or ""):
			отозвать_подключение(пользователь, connection_token["client"], connection_token["family_token"])
			return
	raise Отказ(ПОДКЛЮЧЕНИЕ_НЕ_НАЙДЕНО, _("This connection has already been disconnected"))


# --- сессии браузера ---

#: Название Яндекс Браузера — по языку страницы (`устройство`), прочие —
#: латиницей на любом языке.
ЯНДЕКС = "Yandex Browser"
#: Браузер и система по строке User-Agent: первое совпадение в порядке списка.
#: Edge, Opera и Яндекс Браузер называют себя и Chrome — поэтому они раньше.
БРАУЗЕРЫ = (
	("Edg/", "Edge"),
	("EdgiOS", "Edge"),
	("OPR/", "Opera"),
	("YaBrowser", ЯНДЕКС),
	("SamsungBrowser", "Samsung Internet"),
	("Firefox/", "Firefox"),
	("FxiOS", "Firefox"),
	("CriOS", "Chrome"),
	("Chrome/", "Chrome"),
	("Safari/", "Safari"),
)
#: Android и iOS — раньше Linux и macOS: их строки содержат и те.
СИСТЕМЫ = (
	("Windows", "Windows"),
	("Android", "Android"),
	("iPhone", "iOS"),
	("iPad", "iPadOS"),
	("CrOS", "ChromeOS"),
	("Mac OS X", "macOS"),
	("Macintosh", "macOS"),
	("Linux", "Linux"),
)


def устройство(user_agent: str | None) -> str:
	"""Коротко, откуда сессия: «Chrome, Windows». Незнакомое — как есть не
	показывается: строку User-Agent задаёт клиент, она длинная и может быть
	любой."""
	ua = user_agent or ""
	браузер = next((имя for признак, имя in БРАУЗЕРЫ if признак in ua), None)
	система = next((имя for признак, имя in СИСТЕМЫ if признак in ua), None)
	if браузер == ЯНДЕКС:
		браузер = _("Yandex Browser")
	return ", ".join(ч for ч in (браузер, система) if ч) or _("Unknown device")


def _действует(session_token_row, порог, сейчас: datetime) -> bool:
	"""Можно ли ещё войти по сессии: не вышел простой (`session_expiry`) и
	абсолютный срок от входа (`session_guard`). Иначе Frappe удалит её, когда
	её предъявят, а запись уберёт планировщиком."""
	if get_datetime(session_token_row.lastupdate) <= get_datetime(порог):
		return False
	конец = _данные(session_token_row).get("session_end")
	if not конец:
		return True
	try:
		конец = datetime.fromisoformat(конец)
	except (TypeError, ValueError):
		return True
	return (конец if конец.tzinfo else конец.replace(tzinfo=UTC)) > сейчас


def _данные(session_token_row) -> dict:
	# Данные сессии несут токен CSRF: у переменных с ними в имени `secret`.
	return frappe.parse_json(session_token_row.sessiondata or "{}") or {}


def _сессии_пользователя(пользователь: str) -> list:
	таблица = frappe.qb.DocType("Sessions")
	return (
		frappe.qb.from_(таблица)
		.select(таблица.sid, таблица.sessiondata, таблица.lastupdate)
		.where(таблица.user == пользователь)
		.orderby(таблица.lastupdate, order=Order.desc)
	).run(as_dict=True)


def сессии(пользователь: str, current_session_token: str | None) -> list[dict]:
	"""Действующие сессии браузера пользователя: текущая первой, дальше — по
	последней активности.

	Вышедшие по простою или по абсолютному сроку не показываются: войти по
	ним уже нельзя. «Последняя активность» — из записи в базе: Frappe обновляет
	её не чаще раза в 10 минут.
	"""
	from frappe.sessions import get_expired_threshold

	порог, сейчас = get_expired_threshold(), datetime.now(tz=UTC)
	строки = []
	for session_token_row in _сессии_пользователя(пользователь):
		if not _действует(session_token_row, порог, сейчас):
			continue
		session_secrets = _данные(session_token_row)
		вход = session_secrets.get("creation")
		строки.append(
			{
				"id": отпечаток(session_token_row.sid),
				"current": bool(current_session_token) and session_token_row.sid == current_session_token,
				"device": устройство(session_secrets.get("user_agent")),
				"ip": session_secrets.get("session_ip") or "",
				"started": get_datetime(вход) if вход else None,
				"last_active": session_token_row.lastupdate,
				"impersonated": bool(session_secrets.get("impersonated_by")),
			}
		)
	return sorted(строки, key=lambda с: not с["current"])


def есть_пароль(пользователь: str) -> bool:
	"""Задан ли у пользователя пароль: запись в `__Auth`, как её ищет
	`check_password` Frappe."""
	auth = frappe.qb.Table("__Auth")
	return bool(
		frappe.qb.from_(auth)
		.select(auth.name)
		.where(
			(auth.doctype == "User")
			& (auth.name == пользователь)
			& (auth.fieldname == "password")
			& (auth.encrypted == 0)
		)
		.limit(1)
		.run()
	)


def подтвердить_паролем(пользователь: str, password: str | None) -> None:
	"""Повторный вход паролем перед завершением сессий (ASVS 7.5.2).

	Неверные попытки идут в тот же счётчик, что вход (`LoginAttemptTracker`
	Frappe): пароль один, и подбирать его здесь не легче, чем на странице
	входа. Пока счётчик закрыт — отказ без проверки пароля. У аккаунта без
	пароля — свой отказ и не в счётчик: ошибки ввода тут нет.
	"""
	if not есть_пароль(пользователь):
		raise Отказ(ПАРОЛЯ_НЕТ, _("Your account has no password. Set one using the link in an email"))
	if not password:
		raise Отказ(НУЖЕН_ПАРОЛЬ, _("Enter your password to confirm"))
	счётчик = get_login_attempt_tracker(пользователь, raise_locked_exception=False)
	if not счётчик.is_user_allowed():
		raise Отказ(ПОПЫТКИ_КОНЧИЛИСЬ, _("Too many incorrect attempts. Try again in a few minutes"))
	try:
		if len(password) > ПРЕДЕЛ_ПАРОЛЯ:
			raise frappe.AuthenticationError
		check_password(пользователь, password)
	except frappe.AuthenticationError:
		счётчик.add_failure_attempt()
		raise Отказ(НЕВЕРНЫЙ_ПАРОЛЬ, _("Incorrect password")) from None
	счётчик.add_success_attempt()


def завершить_сессию(пользователь: str, сессия: str, current_session_token: str | None) -> None:
	"""Завершить одну свою сессию по отпечатку; текущую — нельзя, её завершает
	«Выйти»: удалённую из-под запроса Frappe записал бы обратно в конце него."""
	from frappe.sessions import delete_session

	for session_token_row in _сессии_пользователя(пользователь):
		if not _тот_же(отпечаток(session_token_row.sid), сессия or ""):
			continue
		if session_token_row.sid == current_session_token:
			raise Отказ(ТЕКУЩАЯ_СЕССИЯ, _("To end this session, use “Log out”"))
		delete_session(session_token_row.sid, user=пользователь, reason=ПРИЧИНА_ЗАВЕРШЕНИЯ)
		return
	raise Отказ(СЕССИЯ_НЕ_НАЙДЕНА, _("This session has already been ended"))


def завершить_другие(пользователь: str, current_session_token: str | None) -> int:
	"""Завершить все свои сессии, кроме текущей; сколько из них было на
	странице.

	Удаляются и вышедшие по сроку — войти по ним нельзя, но и держать их
	незачем, — а в число не входят: человек их не видел. Свой обход, а не
	`clear_sessions` Frappe: тот берёт не больше 100 сессий и текущую — из
	`frappe.session`, а не от вызывающего.
	"""
	from frappe.sessions import delete_session, get_expired_threshold

	порог, сейчас = get_expired_threshold(), datetime.now(tz=UTC)
	завершено = 0
	for session_token_row in _сессии_пользователя(пользователь):
		if session_token_row.sid == current_session_token:
			continue
		завершено += _действует(session_token_row, порог, сейчас)
		delete_session(session_token_row.sid, user=пользователь, reason=ПРИЧИНА_ЗАВЕРШЕНИЯ)
	return завершено
