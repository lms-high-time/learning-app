# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Сервер OAuth Frappe с правилами платформы (learning-services#661, #665).

Эндпоинты OAuth — функции Frappe; они берут сервер oauthlib из
`frappe.local.oauth_server` и создают его, только если его там нет. Подмены
из `override_whitelisted_methods` кладут туда сервер со своим валидатором —
наследником валидатора Frappe — и зовут исходную функцию. Код Frappe не
меняется, а правки валидатора видны в одном классе. Интроспекция и
динамическая регистрация подменены целиком: у них нет валидатора, через
который их можно поправить.

Что валидатор меняет:

- клиент с `client_id`-адресом — клиент по документу о себе (`cimd.py`);
- адрес возврата на этот же компьютер принимается с любым портом, как требует
  RFC 8252 §7.3: Claude Code слушает случайный порт, а в документе адрес без
  него. Адреса клиента делятся любым пробелом — динамическая регистрация Frappe
  пишет их через перевод строки, а проверка Frappe делила только пробелом;
- обмен кода сверяет адрес возврата с тем, к которому привязан код (RFC 6749
  §4.1.3), а не со списком клиента: иначе локальный адрес с портом здесь бы и
  не прошёл;
- только код авторизации и токен обновления: Frappe разрешал всем клиентам
  вход по паролю (ROPC — мимо экрана согласия) и ответы implicit и hybrid
  (`token`, `id_token token`, `code id_token`), а наши агенты и веб-чат
  просят только `code` (learning-services#665);
- клиент аутентифицируется по способу из своей записи. Конфиденциальный
  (`Client Secret Basic` и `Client Secret Post`) — секретом в теле: Frappe
  секрет не сверял вовсе. Сервис MCP заведён как `client_secret_basic`, а
  секрет шлёт в теле. Заголовок Basic валидатор тоже понимает, но по HTTP до
  него не доходит: Frappe принимает Basic за вход по API-ключу и отвечает 401
  раньше OAuth, поэтому метаданные обещают только `client_secret_post`.
  Публичный (`None`) — без секрета, и вход ему держит PKCE;
- PKCE — только S256 и обязателен всем клиентам, конфиденциальным тоже
  (ASVS 10.4.6, RFC 9700 §2.1.1). `plain` не принимается: метаданные
  обещают S256;
- код авторизации живёт `СРОК_КОДА` и обменивается один раз под блокировкой
  своей строки. Повторное предъявление кода отзывает токены, выданные по нему
  (RFC 6749 §4.1.2);
- отозванный, неизвестный или чужой токен обновления — `invalid_grant`, как
  требует RFC 6749 §5.2. Frappe отвечал на него ошибкой «запись не найдена»
  мимо OAuth, и агент не понимал, что пора войти заново;
- токен обновления одноразовый у всех клиентов. Токены одного входа — одна
  семья: повтор уже заменённого токена обновления отзывает всю семью (RFC 9700
  §4.14.2) с записью в журнал, а через `СРОК_СЕМЬИ` от входа семья больше не
  обновляется. Исключение — повтор в `ОКНО_ГОНКИ` после замены: ответ на
  обновление мог потеряться, и клиент получает новую пару той же семьи;
- отзыв токена (RFC 7009) снимает токен только своему клиенту и вместе с его
  семьёй; чужой или неизвестный токен — тот же ответ 200;
- интроспекция — только конфиденциальному клиенту с секретом из списка в
  конфигурации сайта, без списка — никому; динамическая регистрация — с областью `all`,
  конфиденциальным — `client_secret_post` и длинный секрет, с пределом частоты.

Семья хранится в полях токена, а не ссылкой на первый: Frappe каждый час
удаляет отозванные токены (`delete_oauth2_data`), а истёкшие — через 30 дней
(Log Settings). Поэтому начало семьи переписывается в каждый новый токен, а
повтор узнаётся по отпечатку прежнего токена обновления в его преемнике.
Действующий токен семьи жив, пока жива она, и повтор токена, заменённого
последним, узнаётся всегда; более старого — пока Frappe не удалил его
преемника. Повтор кода — так же: семья названа кодом.

Переменные и параметры с секретом клиента, токенами и кодами авторизации
названы латиницей (`client_secret`, `*_token`, `code_token`) вопреки стилю.
`Why:` трассировку с переменными (`frappe.get_traceback(with_context=True)` —
Error Log и Sentry) Frappe чистит по латинским подстрокам имён: `password`,
`secret`, `token`, `key`. Кириллическое `секрет` ушло бы в журнал как есть
(learning-services#664). Имя записи `OAuth Bearer Token` — сам токен
доступа, поэтому и имена токенов — `access_token`. Семья названа кодом или,
у токенов до learning-services#665, токеном — `family_token`.
"""

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta
from urllib.parse import unquote_plus, urlsplit
from zoneinfo import ZoneInfo

import frappe
from frappe.integrations import oauth2
from frappe.oauth import OAuthWebRequestValidator, get_userinfo
from frappe.rate_limiter import rate_limit
from frappe.utils import add_to_date, cstr, get_system_timezone, now_datetime
from oauthlib.common import Request as ЗапросOAuth
from oauthlib.oauth2.rfc6749.errors import UnsupportedCodeChallengeMethodError
from oauthlib.openid.connect.core.endpoints.pre_configured import Server
from werkzeug import Response
from werkzeug.exceptions import NotFound

from lms_frappe_app.agent_learning import cimd

#: Срок кода авторизации, секунд: предел, который рекомендует RFC 6749 §4.1.2.
#: Агент обменивает код сразу после возврата.
СРОК_КОДА = 10 * 60
#: Сколько живёт семья токенов от входа. `Why:` ASVS 7.3.2 требует
#: абсолютного срока: без него утёкший токен обновления, который регулярно
#: обновляют, работает бессрочно. Агент держит подключение фоном, без
#: открытого браузера, и повторный вход раз в 90 дней — небольшая цена, а
#: утёкший токен перестаёт работать сам.
СРОК_СЕМЬИ = timedelta(days=90)
#: Повтор токена обновления в эти секунды после замены продолжает семью: клиент
#: получает новую пару, а преемник гасится. `Why:` ответ на обновление
#: теряется (обрыв связи, таймаут), клиент повторяет запрос прежним токеном, и
#: отказ или отзыв семьи разлогинил бы агента на ровном месте. Так же делают
#: Okta и Auth0. Позже повтор — признак утечки.
ОКНО_ГОНКИ = timedelta(seconds=60)
#: Регистраций клиента за час с одного адреса (реальный адрес клиента nginx
#: сайта передаёт с learning-services#667). `Why:` регистрация открыта
#: гостям, и каждая — запись OAuth Client. Claude, ChatGPT и Claude Code
#: входят адресом документа о себе и не регистрируются, так что регистрация —
#: редкое событие: прочие агенты, раз на подключение. Двадцать в час покрывают
#: их с запасом и не дают заполнить таблицу клиентов: не больше 480 записей в
#: сутки.
РЕГИСТРАЦИЙ_ЗА_ЧАС = 20

ПУБЛИЧНЫЙ = "None"
#: Ключ конфигурации сайта: `client_id`, которым можно спрашивать интроспекцию.
КЛЮЧ_ИНТРОСПЕКЦИИ = "lms_introspection_clients"
КЛЮЧ_ПРЕДУПРЕЖДЕНИЯ = "lms_introspection_unset_warned"
ПОЛЕ_СЕМЬИ = "lms_token_family"
ПОЛЕ_НАЧАЛА = "lms_family_started"
ПОЛЕ_ПРЕЖНЕГО = "lms_previous_refresh"


def адрес_возврата_разрешён(адрес: str | None, зарегистрированные: list[str]) -> bool:
	"""Адрес возврата из запроса — один из адресов клиента.

	Локальный адрес (`http://localhost`, `http://127.0.0.1`) совпадает с
	зарегистрированным с точностью до порта: схема, хост, путь и запрос — те же.
	"""
	if not адрес:
		return False
	if адрес in зарегистрированные:
		return True
	try:
		части = urlsplit(адрес)
		части.port  # негодный порт urlsplit замечает только здесь
	except ValueError:
		return False
	if (
		части.scheme != "http"
		or not cimd.адрес_петли(части.hostname)
		or части.fragment
		or "@" in части.netloc
	):
		return False
	for свой in зарегистрированные:
		try:
			образец = urlsplit(свой)
		except ValueError:
			continue
		if (образец.scheme, образец.hostname, образец.path, образец.query) == (
			части.scheme,
			части.hostname,
			части.path,
			части.query,
		):
			return True
	return False


def _адреса_клиента(client_id) -> list[str]:
	адреса, по_умолчанию = frappe.db.get_value(
		"OAuth Client", client_id, ["redirect_uris", "default_redirect_uri"]
	) or (None, None)
	return cstr(адреса).split() or ([по_умолчанию] if по_умолчанию else [])


def отпечаток(token: str) -> str:
	"""Отпечаток токена обновления: преемник хранит его, а не сам токен."""
	return hashlib.sha256(token.encode()).hexdigest()


def учётные_данные(request) -> tuple[str | None, str | None]:
	"""`client_id` и секрет из заголовка Basic (RFC 6749 §2.3.1) или из тела."""
	схема, _, basic_secret = (request.headers.get("Authorization") or "").partition(" ")
	if схема.lower() != "basic":
		return request.client_id, request.client_secret
	try:
		id_, _, client_secret = base64.b64decode(basic_secret.strip(), validate=True).decode().partition(":")
	except (ValueError, UnicodeDecodeError):
		return None, None
	return unquote_plus(id_), unquote_plus(client_secret)


def _клиент(имя) -> dict | None:
	"""Запись клиента — если имя совпадает с ней строка в строку.

	`Why:` имена в MariaDB сравниваются без регистра, а oauthlib сверяет
	`client_id` запроса с найденным клиентом точно и на расхождение отвечает 500.
	"""
	if not имя:
		return None
	клиент = frappe.db.get_value(
		"OAuth Client", имя, ["name", "client_secret", "token_endpoint_auth_method"], as_dict=True
	)
	return клиент if клиент and клиент.name == имя else None


class Валидатор(OAuthWebRequestValidator):
	def validate_client_id(self, client_id, request, *args, **kwargs):
		if cimd.это_адрес_клиента(client_id) and not cimd.обеспечить_клиента(client_id):
			return False
		return super().validate_client_id(client_id, request, *args, **kwargs)

	def validate_redirect_uri(self, client_id, redirect_uri, request, *args, **kwargs):
		return адрес_возврата_разрешён(redirect_uri, _адреса_клиента(client_id))

	def confirm_redirect_uri(self, client_id, code_token, redirect_uri, client, *args, **kwargs):
		привязан = frappe.db.get_value(
			"OAuth Authorization Code",
			{"name": code_token, "client": client_id},
			"redirect_uri_bound_to_authorization_code",
		)
		return bool(привязан) and привязан == redirect_uri

	def validate_response_type(self, client_id, response_type, client, request, *args, **kwargs):
		return response_type == "code"

	def validate_grant_type(self, client_id, grant_type, client, request, *args, **kwargs):
		return grant_type in ("authorization_code", "refresh_token")

	def validate_user(self, username, password, client, request, *args, **kwargs):
		# oauthlib сверяет пароль раньше, чем спрашивает `validate_grant_type`:
		# без этого отказ по гранту приходил бы после проверки пароля, и
		# токен-эндпоинт отвечал бы, верен ли он.
		return False

	# Аутентификация клиента на токен-эндпоинте и при отзыве. oauthlib сначала
	# спрашивает, нужна ли она, и зовёт `authenticate_client` для
	# конфиденциальных, `authenticate_client_id` — для публичных.

	def client_authentication_required(self, request, *args, **kwargs):
		# Клиента не опознать — аутентификация нужна, и её провал — `invalid_client`.
		id_, _ = учётные_данные(request)
		клиент = _клиент(id_ or _клиент_по_токену(request))
		return not клиент or клиент.token_endpoint_auth_method != ПУБЛИЧНЫЙ

	def authenticate_client(self, request, *args, **kwargs):
		id_, client_secret = учётные_данные(request)
		клиент = _клиент(id_)
		if (
			not клиент
			or not client_secret
			or клиент.token_endpoint_auth_method == ПУБЛИЧНЫЙ
			or not клиент.client_secret
			or (request.client_id and request.client_id != id_)
			or not hmac.compare_digest(client_secret.encode(), клиент.client_secret.encode())
		):
			return False
		request.client = frappe.get_doc("OAuth Client", клиент.name).as_dict()
		return True

	def authenticate_client_id(self, client_id, request, *args, **kwargs):
		# Без `client_id` Frappe относил запрос к клиенту по токену из запроса —
		# публичному клиенту так и остаётся: секрета у него всё равно нет.
		клиент = _клиент(client_id or _клиент_по_токену(request))
		if not клиент or клиент.token_endpoint_auth_method != ПУБЛИЧНЫЙ:
			return False
		request.client = frappe.get_doc("OAuth Client", клиент.name).as_dict()
		return True

	# PKCE. С обязательным PKCE oauthlib сверяет вызов кода сам и спрашивает его
	# здесь; Frappe этих методов не задаёт и сверял в `validate_code`.

	def is_pkce_required(self, client_id, request):
		return True

	def get_code_challenge(self, code_token, request):
		return frappe.db.get_value("OAuth Authorization Code", code_token, "code_challenge") or None

	def get_code_challenge_method(self, code_token, request):
		метод = frappe.db.get_value("OAuth Authorization Code", code_token, "code_challenge_method")
		# Frappe хранит метод в нижнем регистре, oauthlib ждёт его как в RFC 7636.
		return "S256" if метод == "s256" else метод

	def save_authorization_code(self, client_id, code_token, request, *args, **kwargs):
		super().save_authorization_code(client_id, code_token, request, *args, **kwargs)
		frappe.db.set_value(
			"OAuth Authorization Code",
			code_token["code"],
			"expiration_time",
			add_to_date(now_datetime(), seconds=СРОК_КОДА),
			update_modified=False,
		)
		# Авторизация без экрана согласия приходит GET'ом, а GET Frappe не коммитит.
		frappe.db.commit()

	def validate_code(self, client_id, code_token, client, request, *args, **kwargs):
		# Строка кода блокируется до выдачи токена: второй одновременный обмен
		# ждёт и видит код уже использованным. Код отмечается использованным в
		# `save_bearer_token`, в одной транзакции с токеном, — Frappe отмечал его
		# после коммита токена, и между ними код проходил второй раз.
		запись = frappe.db.get_value(
			"OAuth Authorization Code",
			code_token,
			[
				"client",
				"user",
				"scopes",
				"validity",
				"expiration_time",
				"code_challenge",
				"code_challenge_method",
			],
			as_dict=True,
			for_update=True,
		)
		if not запись:
			# Использованные коды Frappe удаляет каждый час, а семья токенов
			# названа кодом: повтор узнаётся и после удаления.
			if отозвать_семью(code_token, client_id):
				_в_журнал_повтор("Повтор кода авторизации — семья отозвана", client_id, None, code_token)
			return False
		if запись.client != client_id:
			return False
		if запись.validity != "Valid":
			if отозвать_семью(code_token, client_id):
				_в_журнал_повтор(
					"Повтор кода авторизации — семья отозвана", client_id, запись.user, code_token
				)
			return False
		if not запись.expiration_time or now_datetime() > запись.expiration_time:
			return False
		# Вызов с проверкой oauthlib сверяет сам; метод — только S256, как
		# обещают метаданные.
		if not запись.code_challenge or запись.code_challenge_method != "s256":
			return False
		# Смена пароля и отключение гасят коды, но отключение мимо `save`
		# (`db.set_value`) хуков не зовёт.
		if not frappe.db.exists("User", {"name": запись.user, "enabled": 1}):
			return False
		request.scopes = cstr(запись.scopes).split()
		request.user = запись.user
		return True

	def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
		клиент = (client or {}).get("name")
		access_token = frappe.db.get_value(
			"OAuth Bearer Token",
			{"refresh_token": refresh_token, "status": "Active", "client": клиент},
			"name",
		)
		if access_token:
			# Токен обновления одноразовый. Запись блокируется по первичному
			# ключу — блокировка по неиндексированному `refresh_token` встала бы
			# на всю таблицу — и перечитывается: второе одновременное обновление
			# тем же токеном увидит её отозванной.
			access_token = frappe.db.get_value(
				"OAuth Bearer Token", {"name": access_token, "status": "Active"}, "name", for_update=True
			)
		if not access_token:
			access_token = _повтор_обновления(refresh_token, клиент, request)
		if not access_token:
			return False
		token_record = frappe.db.get_value(
			"OAuth Bearer Token",
			access_token,
			["user", "scopes", "creation", ПОЛЕ_СЕМЬИ, ПОЛЕ_НАЧАЛА],
			as_dict=True,
		)
		# Токены, выданные до learning-services#665, без семьи: семья — сам
		# токен, начало — его выдача.
		начало = token_record.get(ПОЛЕ_НАЧАЛА) or token_record.creation
		if now_datetime() > начало + СРОК_СЕМЬИ:
			frappe.db.set_value(
				"OAuth Bearer Token", access_token, "status", "Revoked", update_modified=False
			)
			return False
		if not frappe.db.exists("User", {"name": token_record.user, "enabled": 1}):
			return False
		request.user = token_record.user
		request.lms_прежний_токен = access_token
		request.lms_области = token_record.scopes
		request.lms_семья = token_record.get(ПОЛЕ_СЕМЬИ) or access_token
		request.lms_начало_семьи = начало
		return True

	def get_original_scopes(self, refresh_token, request, *args, **kwargs):
		# Области — того токена, который продолжается: при повторе в окне гонки
		# это преемник, а запись предъявленного Frappe мог уже удалить.
		return getattr(request, "lms_области", None) or super().get_original_scopes(
			refresh_token, request, *args, **kwargs
		)

	def save_bearer_token(self, token, request, *args, **kwargs):
		"""Токен — с семьёй; прежний токен и код гасятся в той же транзакции.

		`Why:` Frappe коммитит сразу после записи нового токена, а прежний
		токен и код отмечал отдельно — или не отмечал вовсе. Записывается здесь
		сам, а не через `super()`: поля семьи нужны в той же вставке.
		"""
		previous_token = getattr(request, "lms_прежний_токен", None)
		if previous_token:
			frappe.db.set_value(
				"OAuth Bearer Token", previous_token, "status", "Revoked", update_modified=False
			)
			family_token, начало = request.lms_семья, request.lms_начало_семьи
		else:
			# Обмен кода: family_token названа кодом — по нему повтор кода находит токены.
			family_token, начало = request.code or token["access_token"], now_datetime()
			if request.code:
				frappe.db.set_value(
					"OAuth Authorization Code", request.code, "validity", "Invalid", update_modified=False
				)
		клиент = request.client["name"]
		frappe.get_doc(
			{
				"doctype": "OAuth Bearer Token",
				"client": клиент,
				"user": request.user,
				"scopes": " ".join(request.scopes),
				"access_token": token["access_token"],
				"refresh_token": token.get("refresh_token"),
				"expires_in": token["expires_in"],
				ПОЛЕ_СЕМЬИ: family_token,
				ПОЛЕ_НАЧАЛА: начало,
				ПОЛЕ_ПРЕЖНЕГО: (
					getattr(request, "lms_отпечаток_прежнего", None) or отпечаток(request.refresh_token)
				)
				if previous_token
				else None,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		return frappe.db.get_value("OAuth Client", клиент, "default_redirect_uri")

	def revoke_token(self, token, token_type_hint, request, *args, **kwargs):
		"""Отзыв (RFC 7009): токен своего клиента — вместе с семьёй.

		Чужой и неизвестный токен ничего не меняют, и ответ тот же: по нему
		клиент не узнает, чей это токен и есть ли он. Frappe отзывал любой
		токен, кто бы о нём ни попросил.
		"""
		клиент = (request.client or {}).get("name")
		поля = ("refresh_token", "name") if token_type_hint == "refresh_token" else ("name", "refresh_token")
		for поле in поля:
			token_record = frappe.db.get_value(
				"OAuth Bearer Token", {поле: token, "client": клиент}, ["name", ПОЛЕ_СЕМЬИ], as_dict=True
			)
			if token_record:
				frappe.db.set_value(
					"OAuth Bearer Token", token_record.name, "status", "Revoked", update_modified=False
				)
				# Токен до learning-services#665 без семьи — сам корень семьи преемников.
				отозвать_семью(token_record.get(ПОЛЕ_СЕМЬИ) or token_record.name, клиент)
				return


def _только_s256(request) -> dict:
	"""Проверка запроса авторизации: вызов PKCE — только S256.

	Без метода oauthlib считает его `plain` (RFC 7636 §4.3).
	"""
	if request.code_challenge is not None and request.code_challenge_method != "S256":
		raise UnsupportedCodeChallengeMethodError(request=request)
	return {}


class Сервер(Server):
	"""Сервер oauthlib с валидатором платформы и проверкой метода PKCE.

	Методы PKCE oauthlib перечисляет в самом гранте, а не в валидаторе,
	поэтому `plain` отсекается проверкой запроса авторизации — её грант
	принимает штатно.
	"""

	def __init__(self):
		super().__init__(Валидатор())
		for грант in (self.auth_grant, self.openid_connect_auth):
			грант.custom_validators.pre_auth.append(_только_s256)


def отозвать_семью(family_token: str | None, клиент: str | None) -> int:
	"""Отозвать действующие токены семьи у её клиента; сколько отозвано."""
	if not family_token or not клиент:
		return 0
	access_tokens = frappe.get_all(
		"OAuth Bearer Token",
		filters={ПОЛЕ_СЕМЬИ: family_token, "client": клиент, "status": "Active"},
		pluck="name",
	)
	if access_tokens:
		frappe.db.set_value(
			"OAuth Bearer Token",
			{"name": ("in", access_tokens)},
			"status",
			"Revoked",
			update_modified=False,
		)
	return len(access_tokens)


def _повтор_обновления(refresh_token: str, клиент: str | None, request) -> str | None:
	"""Предъявлен уже заменённый токен обновления: имя токена, который продолжит
	семью, или `None` — отказ.

	Заменённый токен узнаётся по преемнику: в нём отпечаток прежнего токена
	обновления. Сам заменённый токен Frappe удаляет в течение часа, а
	действующий преемник живёт, пока жива семья. Токен, отозванный не заменой
	(выход, отзыв, смена пароля), — просто отказ: его семья уже отозвана тем же
	действием.

	В `ОКНО_ГОНКИ` после замены повтор продолжает семью: действующий преемник
	гасится, как гасился бы сам предъявленный токен, и клиент получает новую
	пару. Позже — семья отзывается, и это пишется в журнал (ASVS 7.2.4).

	Новый токен помнит отпечаток погашенного преемника, а не предъявленного
	токена. `Why:` иначе украденный прежний токен, предъявленный в окне, гасил
	бы преемника законного клиента, и его токен обновления не находил бы ни
	записи, ни преемника — отказ без отзыва семьи и без журнала, а вор
	продлевал бы семью повторами того же токена. Так прежний токен продолжает
	семью не больше одного раза, а токен погашенного преемника находит нового.
	"""
	if not клиент:
		return None
	successor_token = frappe.db.get_value(
		"OAuth Bearer Token",
		{ПОЛЕ_ПРЕЖНЕГО: отпечаток(refresh_token), "client": клиент},
		["name", "status", "user", "creation", ПОЛЕ_СЕМЬИ],
		as_dict=True,
		order_by="creation desc",
	)
	if not successor_token:
		return None
	прошло = now_datetime() - successor_token.creation
	if прошло < ОКНО_ГОНКИ:
		if successor_token.status != "Active":
			return None
		access_token = frappe.db.get_value(
			"OAuth Bearer Token", {"name": successor_token.name, "status": "Active"}, "name", for_update=True
		)
		if access_token:
			request.lms_отпечаток_прежнего = отпечаток(
				frappe.db.get_value("OAuth Bearer Token", access_token, "refresh_token")
			)
		return access_token
	if отозвать_семью(successor_token.get(ПОЛЕ_СЕМЬИ), клиент):
		_в_журнал_повтор(
			"Повтор токена обновления — семья отозвана",
			клиент,
			successor_token.user,
			successor_token.get(ПОЛЕ_СЕМЬИ),
			f"через {int(прошло.total_seconds())} с после замены",
		)
	return None


def _в_журнал_повтор(заголовок: str, клиент, пользователь, family_token, подробность: str = "") -> None:
	"""Запись о повторе в журнал ошибок — без значений токенов и кодов.

	Семья названа кодом, а у токенов до learning-services#665 — самим токеном,
	поэтому в журнал идёт её отпечаток: по нему запись находится в базе, а
	учётных данных в журнале нет.
	"""
	frappe.log_error(
		title=заголовок,
		message=(
			f"клиент {клиент}, пользователь {пользователь}, "
			f"семья (отпечаток) {отпечаток(family_token or '')[:16]}"
			+ (f", {подробность}" if подробность else "")
		),
	)


def _клиент_по_токену(request) -> str | None:
	"""Клиент токена из запроса — там же, где его искал `authenticate_client` Frappe.

	`Why:` без `client_id` Frappe грузил клиента по токену `get_doc` без
	проверки: неизвестный токен давал 403 «нет доступа к OAuth-клиенту», запрос
	без токена и заголовка (клиент с `client_assertion`) — 500
	(learning-services#663).
	"""
	if request.refresh_token:
		token_filters = [{"refresh_token": request.refresh_token}]
	elif request.token:
		token_filters = [{"name": request.token}, {"refresh_token": request.token}]
	else:
		схема, _, access_token = (request.headers.get("Authorization") or "").partition(" ")
		if схема.lower() != "bearer" or not access_token.strip():
			return None
		token_filters = [{"name": access_token.strip()}]
	for token_filter in token_filters:
		if клиент := frappe.db.get_value("OAuth Bearer Token", token_filter, "client"):
			return клиент
	return None


def _свой_сервер() -> None:
	frappe.local.oauth_server = Сервер()


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def authorize(**kwargs):
	_свой_сервер()
	return oauth2.authorize(**kwargs)


@frappe.whitelist(methods=["POST"])
def approve(*args, **kwargs):
	_свой_сервер()
	return oauth2.approve(*args, **kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def get_token(*args, **kwargs):
	_свой_сервер()
	return oauth2.get_token(*args, **kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def revoke_token(*args, **kwargs):
	_свой_сервер()
	return oauth2.revoke_token(*args, **kwargs)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def introspect_token(token: str | None = None, token_type_hint: str | None = None):
	"""Интроспекция (RFC 7662) — только конфиденциальному клиенту с секретом.

	`Why:` Frappe отвечал любому гостю. Ответ — в формате Frappe; `exp` — в
	UTC, а не наивным временем сайта (сайт в Asia/Samarkand, контейнер в UTC,
	`exp` уходил на 5 часов вперёд), и истёкший токен не активен.

	Каким клиентам можно спрашивать — список `client_id` в `КЛЮЧ_ИНТРОСПЕКЦИИ`
	конфигурации сайта (на стенде — клиент сервиса MCP; ключ пишет
	`create-site` из `MCP_OAUTH_CLIENT_ID`). Без ключа — никому, с записью в
	журнал (learning-services#691): допуск к интроспекции даёт только список.
	"""
	r = frappe.request
	статус, тело = ответ_интроспекции(ЗапросOAuth(r.url, r.method, r.form, r.headers), token, token_type_hint)
	frappe.local.response = frappe._dict(тело)
	if статус != 200:
		frappe.local.response["http_status_code"] = статус


def ответ_интроспекции(запрос, token: str | None, token_type_hint: str | None) -> tuple[int, dict]:
	# Параметры в строке запроса — секрет и токен в журналах прокси и сервера.
	if запрос.uri_query:
		return 400, {"error": "invalid_request", "error_description": "query parameters are not allowed"}
	if not Валидатор().authenticate_client(запрос) or not _можно_спрашивать(запрос.client["name"]):
		return 401, {"error": "invalid_client"}
	return 200, сведения_о_токене(token, token_type_hint)


def _можно_спрашивать(клиент: str) -> bool:
	разрешённые = frappe.conf.get(КЛЮЧ_ИНТРОСПЕКЦИИ)
	if isinstance(разрешённые, list):
		return клиент in разрешённые
	if разрешённые is not None:
		# Строка сравнивалась бы подстроками: `"abc"` пускал бы клиента `b`.
		frappe.log_error(
			title="Интроспекция закрыта: список клиентов задан не списком",
			message=f"{КЛЮЧ_ИНТРОСПЕКЦИИ} в конфигурации сайта — {type(разрешённые).__name__}, нужен список client_id.",
		)
		return False
	# Запись — раз, а не на каждый вызов инструмента агента.
	if not frappe.cache.get_value(КЛЮЧ_ПРЕДУПРЕЖДЕНИЯ):
		frappe.cache.set_value(КЛЮЧ_ПРЕДУПРЕЖДЕНИЯ, 1)
		frappe.log_error(
			title="Интроспекция закрыта: список клиентов не задан",
			message=f"В конфигурации сайта нет {КЛЮЧ_ИНТРОСПЕКЦИИ}: список client_id, которым можно спрашивать.",
		)
	return False


def сведения_о_токене(token: str | None, token_type_hint: str | None) -> dict:
	"""Ответ интроспекции о токене. Неактивному — только `active: false` (RFC 7662 §2.2).

	Токен ищется по виду из подсказки, как у Frappe, и только по нему: токен
	обновления, предъявленный как токен доступа, — не действующий токен доступа.
	"""
	if not token:
		return {"active": False}
	вид = "refresh_token" if token_type_hint == "refresh_token" else "access_token"
	token_record = frappe.db.get_value(
		"OAuth Bearer Token",
		{вид: token},
		["client", "user", "status", "scopes", "expiration_time", "creation", ПОЛЕ_НАЧАЛА],
		as_dict=True,
	)
	if not token_record:
		return {"active": False}

	сейчас = now_datetime()
	if вид == "access_token":
		срок = token_record.expiration_time
	else:
		срок = (token_record.get(ПОЛЕ_НАЧАЛА) or token_record.creation) + СРОК_СЕМЬИ
	if (
		token_record.status != "Active"
		or not срок
		or сейчас >= срок
		or not frappe.db.exists("User", {"name": token_record.user, "enabled": 1})
	):
		return {"active": False}

	клиент = frappe.db.get_value(
		"OAuth Client", token_record.client, ["client_id", "skip_authorization"], as_dict=True
	)
	ответ = {
		"client_id": клиент.client_id,
		"trusted_client": клиент.skip_authorization,
		"active": True,
		"exp": round(срок.replace(tzinfo=ZoneInfo(get_system_timezone())).timestamp()),
		"scope": token_record.scopes,
	}
	if "openid" in cstr(token_record.scopes).split():
		sub = frappe.db.get_value(
			"User Social Login", {"provider": "frappe", "parent": token_record.user}, "userid"
		)
		if sub:
			ответ["sub"] = sub
			ответ.update(get_userinfo(frappe.get_doc("User", token_record.user)))
	return ответ


def _предел_регистраций() -> int:
	# Функцией: предел читается на каждом вызове, а не при импорте модуля.
	return РЕГИСТРАЦИЙ_ЗА_ЧАС


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=_предел_регистраций, seconds=60 * 60)
def register_client():
	"""Динамическая регистрация Frappe (RFC 7591) с пределом частоты и областью `all`.

	`Why:` клиент выбирал область сам, вплоть до `openid` — его данные
	пользователя в ответе интроспекции и ID Token. Область — та же, что у
	остальных клиентов; RFC 7591 §3.2.1 позволяет серверу заменить
	запрошенное и требует вернуть то, что записано.

	Конфиденциальный клиент записывается с `client_secret_post` — и когда
	просил `client_secret_basic`, и когда метода не назвал (умолчание RFC 7591 —
	basic): заголовок Basic Frappe принимает за вход по API-ключу и отвечает 401
	раньше OAuth, и такой клиент не вошёл бы. Секрет — 32 случайных байта:
	Frappe выдаёт 10 шестнадцатеричных знаков, около 40 бит. Публичный клиент
	(`none`) записывается как есть.

	Предел — по IP запроса: реальный адрес клиента nginx публичного сайта
	передаёт с learning-services#667, до него это предел на весь сайт.
	"""
	ответ = oauth2.register_client()
	if ответ.status_code != 201:
		return ответ
	данные = frappe.parse_json(ответ.get_data(as_text=True))
	поля = {"scopes": "all"}
	if frappe.db.get_value("OAuth Client", данные["client_id"], "token_endpoint_auth_method") == ПУБЛИЧНЫЙ:
		данные["token_endpoint_auth_method"] = "none"
	else:
		поля.update(token_endpoint_auth_method="Client Secret Post", client_secret=secrets.token_urlsafe(32))
		данные.update(token_endpoint_auth_method="client_secret_post", client_secret=поля["client_secret"])
	frappe.db.set_value("OAuth Client", данные["client_id"], поля, update_modified=False)
	данные["scope"] = "all"
	# Frappe отвечает `grant_types: ["authorization_code"]`, а токен обновления
	# выдаёт всем: ответ называет то, что клиенту действительно доступно.
	данные.update(grant_types=["authorization_code", "refresh_token"], response_types=["code"])
	ответ.set_data(frappe.as_json(данные))
	return ответ


@frappe.whitelist(allow_guest=True, methods=["GET"])
def authorization_server_metadata():
	"""Метаданные сервера авторизации (RFC 8414) с поддержкой клиентов по адресу.

	`/.well-known/` Frappe обслуживает до маршрутов и хуков, поэтому
	`/.well-known/oauth-authorization-server` сюда переадресует nginx сайта
	(`site/nginx/default.conf` в learning-services).
	"""
	if not oauth2.is_oauth_metadata_enabled("auth_server"):
		raise NotFound
	метаданные = oauth2._get_authorization_server_metadata()
	метаданные["client_id_metadata_document_supported"] = True
	# Frappe обещает `client_secret_basic`, но заголовок Basic сам же принимает
	# за вход по API-ключу и отвечает 401 раньше OAuth (learning-services#665).
	метаданные["token_endpoint_auth_methods_supported"] = ["none", "client_secret_post"]
	# Публичные клиенты (по адресу документа) отзывают свои токены без секрета.
	метаданные["revocation_endpoint_auth_methods_supported"] = ["none", "client_secret_post"]
	метаданные["introspection_endpoint_auth_methods_supported"] = ["client_secret_post"]
	ответ = Response(frappe.as_json(метаданные), mimetype="application/json")
	frappe.local.allow_cors = "*"
	return ответ


@frappe.whitelist(allow_guest=True, methods=["GET"])
def openid_configuration():
	"""Конфигурация OpenID Connect Frappe с тем, что сервер действительно делает.

	`Why:` Frappe обещает в ней ответы implicit и hybrid (`token`,
	`id_token token`, …), которые валидатор отклоняет, и не называет PKCE
	(learning-services#691). Клиент, который читает её, а не метаданные RFC
	8414, выбрал бы закрытый путь. Адрес сюда переадресует nginx сайта, как и
	метаданные сервера авторизации.
	"""
	конфигурация = frappe.parse_json(oauth2.get_openid_configuration().get_data(as_text=True))
	конфигурация.update(
		response_types_supported=["code"],
		response_modes_supported=["query"],
		grant_types_supported=["authorization_code", "refresh_token"],
		code_challenge_methods_supported=["S256"],
		token_endpoint_auth_methods_supported=["none", "client_secret_post"],
	)
	frappe.local.allow_cors = "*"
	return Response(frappe.as_json(конфигурация), mimetype="application/json")


def возврат_подключения() -> dict:
	"""Куда агент получит доступ — для экрана подтверждения.

	`Why:` имя агента клиент пишет о себе сам, а адрес возврата — то, куда
	Frappe отдаст код. Спецификация клиентов по адресу требует показать его хост
	человеку, а локальный адрес — предупредить: так подключается программа на
	этом компьютере, и чужая ссылка на подключение выглядит так же.
	"""
	данные = frappe.flags.oauth_credentials or {}
	if not isinstance(данные, dict):
		данные = {}
	хост = _хост(данные.get("redirect_uri"))
	# Клиенту по адресу документ о себе публикует его сайт, и имя агента без
	# этого сайта ничего не доказывает: «Claude Code» назовётся кто угодно.
	client_id = данные.get("client_id")
	сайт = _хост(client_id) if cimd.это_адрес_клиента(client_id) else None
	return {"хост": хост, "локальный": cimd.адрес_петли(хост), "сайт_клиента": сайт}


def _хост(адрес) -> str | None:
	if not isinstance(адрес, str) or not адрес:
		return None
	try:
		return urlsplit(адрес).hostname
	except ValueError:
		return None
