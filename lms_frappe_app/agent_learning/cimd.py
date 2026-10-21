# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Клиенты OAuth по адресу документа о себе — Client ID Metadata Document.

Агент называет себя адресом: `client_id` — https-ссылка, по которой лежит JSON
с именем агента и адресами возврата. Регистрироваться заранее не нужно. Так
подключается Claude, но только если метаданные сервера авторизации обещают
`client_id_metadata_document_supported`; иначе он регистрирует нового клиента
на каждое подключение (learning-services#661). Спецификация — MCP Authorization,
раздел «Client ID Metadata Documents».

Клиент по адресу хранится обычной записью OAuth Client с именем, равным
адресу. `Why:` на имени записи держатся коды, токены, отзыв и отчёты, и для
отдельного вида клиентов пришлось бы дублировать каждый из этих путей. Запись
обновляется из документа, когда истекает его кэш.

Документ сервер загружает по ссылке, которую назвал клиент, — это запрос
наружу по чужому адресу. Поэтому: только https на порт 443, адрес хоста —
только публичный, соединение идёт на проверенный IP (повторное разрешение имени
подменило бы его на внутренний), без переадресаций, с пределом размера и
времени.
"""

import ipaddress
import json
import socket
from urllib.parse import urlsplit

import certifi
import frappe
import urllib3

#: Имя записи Frappe — не длиннее 140 знаков; поля `Data` — тоже.
ДЛИНА_ПОЛЯ = 140
ПРЕДЕЛ_ДОКУМЕНТА = 64 * 1024
ТАЙМАУТ = urllib3.Timeout(total=5, connect=3)
#: Кэш документа — по его `Cache-Control`, но не чаще раза в минуту и не реже
#: раза в сутки: без нижней границы `no-store` превратил бы каждое подключение
#: в запрос наружу.
КЭШ_НЕ_МЕНЬШЕ = 60
КЭШ_НЕ_БОЛЬШЕ = 24 * 3600
КЭШ_ПО_УМОЛЧАНИЮ = 600
КЛЮЧ_КЭША = "lms_cimd_client:"


class ДокументНеПринят(Exception):
	"""Документ клиента нельзя принять. Текст — для журнала ошибок."""


def это_адрес_клиента(client_id) -> bool:
	"""`client_id` — адрес документа: https, путь, без логина, запроса и якоря."""
	if not isinstance(client_id, str) or not client_id or len(client_id) > ДЛИНА_ПОЛЯ:
		return False
	if not client_id.isascii() or any(з.isspace() or з in "<>" for з in client_id):
		return False
	try:
		части = urlsplit(client_id)
		порт = части.port
	except ValueError:
		return False
	return (
		части.scheme == "https"
		and bool(части.hostname)
		and порт in (None, 443)
		and "@" not in части.netloc
		and части.path not in ("", "/")
		and not части.query
		and not части.fragment
	)


def адрес_петли(хост: str | None) -> bool:
	"""Хост — этот же компьютер: `localhost` или loopback-адрес."""
	if not хост:
		return False
	if хост.lower() == "localhost":
		return True
	try:
		return ipaddress.ip_address(хост.strip("[]")).is_loopback
	except ValueError:
		return False


def _годный_возврат(адрес) -> bool:
	"""Адрес возврата: https или http на этот компьютер, без якоря и логина."""
	if not isinstance(адрес, str) or not адрес.isascii() or any(з.isspace() or з in "<>" for з in адрес):
		return False
	try:
		части = urlsplit(адрес)
		части.port  # негодный порт urlsplit замечает только здесь
	except ValueError:
		return False
	if части.fragment or "@" in части.netloc or not части.hostname:
		return False
	return части.scheme == "https" or (части.scheme == "http" and адрес_петли(части.hostname))


def _ссылка_или_пусто(значение) -> str | None:
	"""Необязательная ссылка о клиенте: https и влезает в поле — иначе не берётся."""
	if (
		isinstance(значение, str)
		and len(значение) <= ДЛИНА_ПОЛЯ
		and значение.startswith("https://")
		and _годный_возврат(значение)
	):
		return значение
	return None


def поля_клиента(адрес: str, документ) -> dict:
	"""Поля записи OAuth Client из документа клиента — или `ДокументНеПринят`.

	Проверки — из спецификации: `client_id` документа совпадает с его адресом
	строка в строку, есть имя и адреса возврата, клиент публичный — секрета
	у клиента по адресу быть не может, сервер его не выдавал.
	"""
	if not isinstance(документ, dict):
		raise ДокументНеПринят("документ — не JSON-объект")
	if документ.get("client_id") != адрес:
		raise ДокументНеПринят("client_id документа не совпадает с его адресом")
	имя = документ.get("client_name")
	if not isinstance(имя, str) or not имя.strip():
		raise ДокументНеПринят("нет client_name")
	адреса = документ.get("redirect_uris")
	if not isinstance(адреса, list) or not адреса or not all(_годный_возврат(а) for а in адреса):
		raise ДокументНеПринят("redirect_uris пуст или в нём негодный адрес")
	# Первый адрес — адрес возврата по умолчанию, у него поле `Data`.
	if len(адреса[0]) > ДЛИНА_ПОЛЯ:
		raise ДокументНеПринят("первый адрес возврата длиннее 140 знаков")
	if документ.get("token_endpoint_auth_method", "none") != "none":
		raise ДокументНеПринят(
			"клиент по адресу принимается только публичным (token_endpoint_auth_method: none)"
		)
	if "client_secret" in документ or "client_secret_expires_at" in документ:
		raise ДокументНеПринят("в документе клиента секрет")
	if "authorization_code" not in (документ.get("grant_types") or ["authorization_code"]):
		raise ДокументНеПринят("grant_types без authorization_code")
	if "code" not in (документ.get("response_types") or ["code"]):
		raise ДокументНеПринят("response_types без code")
	return {
		"app_name": имя.strip()[:ДЛИНА_ПОЛЯ],
		"redirect_uris": " ".join(адреса),
		"default_redirect_uri": адреса[0],
		"client_uri": _ссылка_или_пусто(документ.get("client_uri")),
		"logo_uri": _ссылка_или_пусто(документ.get("logo_uri")),
		"token_endpoint_auth_method": "None",
		"grant_type": "Authorization Code",
		"response_type": "Code",
		"scopes": "all",
		"skip_authorization": 0,
	}


def срок_кэша(cache_control: str | None) -> int:
	"""Сколько секунд держать документ: `max-age` в пределах границ кэша."""
	директивы = {}
	for часть in (cache_control or "").split(","):
		ключ, _, значение = часть.strip().partition("=")
		директивы[ключ.strip().lower()] = значение.strip().strip('"')
	if "no-store" in директивы or "no-cache" in директивы:
		return КЭШ_НЕ_МЕНЬШЕ
	try:
		срок = int(директивы["max-age"])
	except (KeyError, ValueError):
		срок = КЭШ_ПО_УМОЛЧАНИЮ
	return max(КЭШ_НЕ_МЕНЬШЕ, min(срок, КЭШ_НЕ_БОЛЬШЕ))


def публичный_ip(хост: str) -> str:
	"""IP хоста для соединения — если все его адреса публичные.

	`Why:` хотя бы один внутренний адрес — и запрос по чужой ссылке ушёл бы в
	сеть стенда: к базе, redis, панели Coolify. IPv4 — первым: в контейнерах
	стенда IPv6 наружу может не быть.
	"""
	try:
		записи = socket.getaddrinfo(хост, 443, type=socket.SOCK_STREAM)
	except OSError as ошибка:
		raise ДокументНеПринят(f"хост не разрешается: {ошибка}") from ошибка
	адреса = {ipaddress.ip_address(запись[4][0].split("%")[0]) for запись in записи}
	if not адреса:
		raise ДокументНеПринят("у хоста нет адресов")
	if not all(а.is_global for а in адреса):
		raise ДокументНеПринят("хост ведёт во внутреннюю сеть")
	return str(min(адреса, key=lambda а: (а.version, int(а))))


def загрузить(адрес: str) -> tuple[object, int]:
	"""Документ клиента по адресу и срок его кэша — или `ДокументНеПринят`."""
	части = urlsplit(адрес)
	хост = части.hostname
	пул = urllib3.HTTPSConnectionPool(
		публичный_ip(хост),
		443,
		server_hostname=хост,
		assert_hostname=хост,
		cert_reqs="CERT_REQUIRED",
		ca_certs=certifi.where(),
		timeout=ТАЙМАУТ,
		retries=False,
		maxsize=1,
	)
	try:
		ответ = пул.request(
			"GET",
			части.path,
			headers={"Host": хост, "Accept": "application/json", "User-Agent": "Cenda OAuth"},
			redirect=False,
			preload_content=False,
		)
		try:
			if ответ.status != 200:
				raise ДокументНеПринят(f"ответ {ответ.status}")
			тело = ответ.read(ПРЕДЕЛ_ДОКУМЕНТА + 1)
		finally:
			ответ.release_conn()
	except urllib3.exceptions.HTTPError as ошибка:
		raise ДокументНеПринят(f"документ не загрузился: {ошибка}") from ошибка
	finally:
		пул.close()
	if len(тело) > ПРЕДЕЛ_ДОКУМЕНТА:
		raise ДокументНеПринят("документ больше 64 КБ")
	try:
		документ = json.loads(тело)
	except ValueError as ошибка:
		raise ДокументНеПринят("документ — не JSON") from ошибка
	return документ, срок_кэша(ответ.headers.get("Cache-Control"))


def обеспечить_клиента(адрес: str) -> bool:
	"""Запись клиента по адресу свежа: из кэша или из только что загруженного документа.

	`False` — документ не принят, и авторизация клиенту отказана. Прежняя
	запись при этом не спасает: документ, который его владелец сломал или
	убрал, значит, что клиенту больше не верят.
	"""
	if frappe.cache.get_value(КЛЮЧ_КЭША + адрес) and frappe.db.exists("OAuth Client", адрес):
		return True
	try:
		документ, срок = загрузить(адрес)
		поля = поля_клиента(адрес, документ)
	except ДокументНеПринят as ошибка:
		frappe.log_error(title="Документ клиента OAuth не принят", message=f"{адрес}: {ошибка}")
		return False
	записать(адрес, поля)
	frappe.cache.set_value(КЛЮЧ_КЭША + адрес, 1, expires_in_sec=срок)
	return True


def записать(адрес: str, поля: dict) -> None:
	"""Создать или обновить запись клиента с именем-адресом."""
	if frappe.db.exists("OAuth Client", адрес):
		клиент = frappe.get_doc("OAuth Client", адрес)
		# Безусловное сохранение писало бы версию на каждое истечение кэша.
		if all(клиент.get(поле) == значение for поле, значение in поля.items()):
			return
		клиент.update(поля)
		клиент.save(ignore_permissions=True)
	else:
		try:
			frappe.get_doc({"doctype": "OAuth Client", **поля}).insert(
				ignore_permissions=True, set_name=адрес
			)
		except frappe.DuplicateEntryError:
			# Два подключения одновременно: запись создал соседний запрос.
			return
	# Авторизация обычно приходит GET'ом, а GET Frappe не коммитит: без этого
	# запись исчезла бы до кнопки «Разрешить».
	frappe.db.commit()  # nosemgrep
