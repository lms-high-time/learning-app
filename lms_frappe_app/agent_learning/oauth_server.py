# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Сервер OAuth Frappe с правилами платформы (learning-services#661).

Эндпоинты OAuth — функции Frappe; они берут сервер oauthlib из
`frappe.local.oauth_server` и создают его, только если его там нет. Подмены
из `override_whitelisted_methods` кладут туда сервер со своим валидатором —
наследником валидатора Frappe — и зовут исходную функцию. Код Frappe не
меняется, а правки валидатора видны в одном классе.

Что валидатор меняет:

- клиент с `client_id`-адресом — клиент по документу о себе (`cimd.py`);
- адрес возврата на этот же компьютер принимается с любым портом, как требует
  RFC 8252 §7.3: Claude Code слушает случайный порт, а в документе адрес без
  него. Адреса клиента делятся любым пробелом — динамическая регистрация Frappe
  пишет их через перевод строки, а проверка Frappe делила только пробелом;
- обмен кода сверяет адрес возврата с тем, к которому привязан код (RFC 6749
  §4.1.3), а не со списком клиента: иначе локальный адрес с портом здесь бы и
  не прошёл;
- отозванный или неизвестный токен обновления — `invalid_grant`, как требует
  RFC 6749 §5.2. Frappe отвечал на него ошибкой «запись не найдена» мимо
  OAuth, и агент не понимал, что пора войти заново.
"""

from urllib.parse import urlsplit

import frappe
from frappe.integrations import oauth2
from frappe.oauth import OAuthWebRequestValidator
from frappe.utils import cstr
from oauthlib.openid.connect.core.endpoints.pre_configured import Server
from werkzeug import Response
from werkzeug.exceptions import NotFound

from lms_frappe_app.agent_learning import cimd


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


class Валидатор(OAuthWebRequestValidator):
	def validate_client_id(self, client_id, request, *args, **kwargs):
		if cimd.это_адрес_клиента(client_id) and not cimd.обеспечить_клиента(client_id):
			return False
		return super().validate_client_id(client_id, request, *args, **kwargs)

	def validate_redirect_uri(self, client_id, redirect_uri, request, *args, **kwargs):
		return адрес_возврата_разрешён(redirect_uri, _адреса_клиента(client_id))

	def confirm_redirect_uri(self, client_id, code, redirect_uri, client, *args, **kwargs):
		привязан = frappe.db.get_value(
			"OAuth Authorization Code",
			{"name": code, "client": client_id},
			"redirect_uri_bound_to_authorization_code",
		)
		return bool(привязан) and привязан == redirect_uri

	def authenticate_client(self, request, *args, **kwargs):
		# Frappe ищет клиента через `get_doc`: токен обновления, которого нет,
		# давал «запись не найдена» вместо ответа OAuth.
		try:
			return super().authenticate_client(request, *args, **kwargs)
		except frappe.DoesNotExistError:
			return False

	def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
		пользователь = frappe.db.get_value(
			"OAuth Bearer Token",
			{"refresh_token": refresh_token, "status": "Active", "client": client.get("name")},
			"user",
		)
		if not пользователь:
			return False
		request.user = пользователь
		return True


def _свой_сервер() -> None:
	frappe.local.oauth_server = Server(Валидатор())


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
	ответ = Response(frappe.as_json(метаданные), mimetype="application/json")
	frappe.local.allow_cors = "*"
	return ответ


def возврат_подключения() -> dict:
	"""Куда агент получит доступ — для экрана подтверждения.

	`Why:` имя агента клиент пишет о себе сам, а адрес возврата — то, куда
	Frappe отдаст код. Спецификация клиентов по адресу требует показать его хост
	человеку, а локальный адрес — предупредить: так подключается программа на
	этом компьютере, и чужая ссылка на подключение выглядит так же.
	"""
	данные = frappe.flags.oauth_credentials or {}
	адрес = данные.get("redirect_uri") if isinstance(данные, dict) else None
	try:
		хост = urlsplit(адрес).hostname if адрес else None
	except ValueError:
		хост = None
	return {"хост": хост, "локальный": cimd.адрес_петли(хост)}
