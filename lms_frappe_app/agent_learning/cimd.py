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
подменило бы его на внутренний), без переадресаций, с пределом размера. Загрузка
идёт в отдельном небольшом пуле потоков с общим сроком: медленный DNS или
сервер, отдающий байт в секунду, держит поток пула, а не воркер сайта, а сторож
по сроку обрывает его соединение. Свободного места в пуле нет — загрузка не
ждёт в очереди. Число загрузок от одного пользователя ограничено.

Документ, который не загрузился (сеть, ответ 5xx, пул занят, предел загрузок),
известному клиенту не мешает — он входит по прежней записи. Отказ — только
документу, который загрузился и не годится или которого больше нет (4xx):
иначе медленный чужой сервер, заняв пул, отрезал бы и настоящих агентов.

Чего модуль не делает: токен-эндпоинт документ не перепроверяет — выданные
токены обновления работают, пока их не отзовут, даже если документ убрали.
Отказ приходит на следующей авторизации.
"""

import ipaddress
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as Просрочено
from urllib.parse import unquote, urlsplit

import certifi
import frappe
import urllib3

#: Имя записи Frappe — не длиннее 140 знаков; поля `Data` — тоже.
ДЛИНА_ПОЛЯ = 140
ПРЕДЕЛ_ДОКУМЕНТА = 64 * 1024
ТАЙМАУТ = urllib3.Timeout(connect=3, read=3)
#: Общий срок загрузки вместе с разрешением имени, секунд.
СРОК_ЗАГРУЗКИ = 6
#: Потоки загрузки на процесс. Застрявшие на чужом сервере потоки дорабатывают
#: в пуле, и больше этого числа их не станет.
ПОТОКИ_ЗАГРУЗКИ = 4
#: Загрузок документа от одного пользователя за окно, секунд.
ЗАГРУЗОК_НЕ_БОЛЬШЕ = 20
ОКНО_ЗАГРУЗОК = 600
#: Кэш документа — по его `Cache-Control`, но не чаще раза в минуту и не реже
#: раза в сутки: без нижней границы `no-store` превратил бы каждое подключение
#: в запрос наружу.
КЭШ_НЕ_МЕНЬШЕ = 60
КЭШ_НЕ_БОЛЬШЕ = 24 * 3600
КЭШ_ПО_УМОЛЧАНИЮ = 600
КЛЮЧ_КЭША = "lms_cimd_client:"
КЛЮЧ_СЧЁТЧИКА = "lms_cimd_fetches:"
#: Адреса, которые `is_global` считает публичными, хотя это служебные сети
#: (RFC 6890): IPv4 — служебные и шлюзы 6to4; IPv6 — только глобальный
#: юникаст 2000::/3 без 6to4 и Teredo.
СЛУЖЕБНЫЕ_IPV4 = (ipaddress.ip_network("192.0.0.0/24"), ipaddress.ip_network("192.88.99.0/24"))
ГЛОБАЛЬНЫЕ_IPV6 = ipaddress.ip_network("2000::/3")
СЛУЖЕБНЫЕ_IPV6 = (ipaddress.ip_network("2002::/16"), ipaddress.ip_network("2001::/32"))

_пул_загрузки = ThreadPoolExecutor(max_workers=ПОТОКИ_ЗАГРУЗКИ, thread_name_prefix="cimd")
_места_загрузки = threading.BoundedSemaphore(ПОТОКИ_ЗАГРУЗКИ)


class ДокументНеПринят(Exception):
	"""Документ клиента нельзя принять. Текст — для журнала ошибок."""


class ДокументНеДоступен(ДокументНеПринят):
	"""Документ не загрузился по причине, которая пройдёт: сеть, 5xx, срок."""


class _Пул(urllib3.HTTPSConnectionPool):
	"""Пул, который помнит свои соединения, — их обрывает сторож по сроку."""

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.соединения = []

	def _new_conn(self):
		соединение = super()._new_conn()
		self.соединения.append(соединение)
		return соединение


def оборвать(пул: _Пул) -> None:
	"""Закрыть сокеты пула из другого потока: зависшее чтение в нём падает.

	`Why:` таймаут чтения действует на каждое чтение, а не на загрузку целиком:
	сервер, отдающий по байту чуть реже таймаута, держал бы поток сутками.
	"""
	for соединение in пул.соединения:
		сокет = getattr(соединение, "sock", None)
		if сокет is not None:
			try:
				сокет.shutdown(socket.SHUT_RDWR)
			except OSError:
				pass


def это_адрес_клиента(client_id) -> bool:
	"""`client_id` — адрес документа: https, путь без `.` и `..`, без логина, запроса и якоря."""
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
		and not any(сегмент in (".", "..") for сегмент in unquote(части.path).split("/"))
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
	for поле, нужно in (("grant_types", "authorization_code"), ("response_types", "code")):
		значение = документ.get(поле, [нужно])
		if not isinstance(значение, list) or нужно not in значение:
			raise ДокументНеПринят(f"{поле} без {нужно}")
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
	except UnicodeError as ошибка:
		# Метка длиннее 63 знаков или пустая: имя не кодируется.
		raise ДокументНеПринят(f"негодное имя хоста: {ошибка}") from ошибка
	except OSError as ошибка:
		raise ДокументНеДоступен(f"хост не разрешается: {ошибка}") from ошибка
	адреса = {ipaddress.ip_address(запись[4][0].split("%")[0]) for запись in записи}
	if not адреса:
		raise ДокументНеПринят("у хоста нет адресов")
	if not all(_публичный(а) for а in адреса):
		raise ДокументНеПринят("хост ведёт во внутреннюю сеть")
	return str(min(адреса, key=lambda а: (а.version, int(а))))


def _публичный(адрес: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
	if not адрес.is_global:
		return False
	if адрес.version == 4:
		return not any(адрес in сеть for сеть in СЛУЖЕБНЫЕ_IPV4)
	return адрес in ГЛОБАЛЬНЫЕ_IPV6 and not any(адрес in сеть for сеть in СЛУЖЕБНЫЕ_IPV6)


def загрузить(адрес: str) -> tuple[object, int]:
	"""Документ клиента по адресу и срок его кэша — или `ДокументНеПринят`."""
	части = urlsplit(адрес)
	хост = части.hostname
	пул = _Пул(
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
	сторож = threading.Timer(СРОК_ЗАГРУЗКИ, оборвать, (пул,))
	сторож.daemon = True
	сторож.start()
	try:
		ответ = пул.request(
			"GET",
			части.path,
			headers={"Host": хост, "Accept": "application/json", "User-Agent": "Cenda OAuth"},
			redirect=False,
			preload_content=False,
		)
		try:
			if ответ.status >= 500:
				raise ДокументНеДоступен(f"ответ {ответ.status}")
			if ответ.status != 200:
				raise ДокументНеПринят(f"ответ {ответ.status}")
			тело = ответ.read(ПРЕДЕЛ_ДОКУМЕНТА + 1)
		finally:
			ответ.release_conn()
	except (urllib3.exceptions.HTTPError, OSError) as ошибка:
		raise ДокументНеДоступен(f"документ не загрузился: {ошибка}") from ошибка
	finally:
		сторож.cancel()
		пул.close()
	if len(тело) > ПРЕДЕЛ_ДОКУМЕНТА:
		raise ДокументНеПринят("документ больше 64 КБ")
	try:
		документ = json.loads(тело)
	except (ValueError, RecursionError) as ошибка:
		# RecursionError — вложенность в тысячи уровней влезает и в 64 КБ.
		raise ДокументНеПринят("документ — не JSON") from ошибка
	return документ, срок_кэша(ответ.headers.get("Cache-Control"))


def обеспечить_клиента(адрес: str) -> bool:
	"""Запись клиента по адресу свежа: из кэша или из только что загруженного документа.

	`False` — документ не принят, и авторизация клиенту отказана. Документ,
	который его владелец сломал или убрал, значит, что клиенту больше не верят,
	и прежняя запись не спасает. Документ, который просто не загрузился,
	известного клиента не останавливает — см. docstring модуля.
	"""
	имя = frappe.db.exists("OAuth Client", адрес)
	if имя and имя != адрес:
		# Имена в MariaDB сравниваются без регистра: `/raw/AbCd` нашёл бы чужую
		# запись `/raw/abcd` и переписал её адреса возврата своими.
		_в_журнал(адрес, f"занят адресом {имя}")
		return False
	if имя and frappe.cache.get_value(КЛЮЧ_КЭША + адрес):
		return True
	try:
		документ, срок = _загрузить_в_пуле(адрес)
		поля = поля_клиента(адрес, документ)
	except _ПределЗагрузок:
		return bool(имя)
	except ДокументНеДоступен as ошибка:
		_в_журнал(адрес, f"{ошибка}" + ("; вход по прежней записи" if имя else ""))
		return bool(имя)
	except ДокументНеПринят as ошибка:
		_в_журнал(адрес, str(ошибка))
		return False
	записать(адрес, поля)
	frappe.cache.set_value(КЛЮЧ_КЭША + адрес, 1, expires_in_sec=срок)
	return True


def _загрузить_в_пуле(адрес: str) -> tuple[object, int]:
	посчитать_загрузку()
	if not _места_загрузки.acquire(blocking=False):
		raise ДокументНеДоступен("все потоки загрузки заняты")
	try:
		задача = _пул_загрузки.submit(загрузить, адрес)
	except BaseException:
		_места_загрузки.release()
		raise
	задача.add_done_callback(lambda _: _места_загрузки.release())
	try:
		return задача.result(timeout=СРОК_ЗАГРУЗКИ)
	except Просрочено as ошибка:
		raise ДокументНеДоступен("загрузка дольше срока") from ошибка


def _в_журнал(адрес: str, причина: str) -> None:
	frappe.log_error(title="Документ клиента OAuth не принят", message=f"{адрес}: {причина}")


def посчитать_загрузку() -> None:
	"""Ещё одна загрузка от пользователя — или `ДокументНеДоступен`, если их много.

	`Why:` адрес документа выбирает вошедший пользователь, а регистрация на
	платформе открыта: без предела любой ученик гонял бы сервер по своим ссылкам
	и копил записи клиентов и ошибок. Отказ по пределу в журнал пишется один
	раз за окно — первый.
	"""
	ключ = frappe.cache.make_key(КЛЮЧ_СЧЁТЧИКА + frappe.session.user)
	# Ключ со сроком создаётся одной командой, а `incr` атомарен: обрыв между
	# ними не оставит ключ без срока, а одновременные подключения не проскочат
	# предел вдвоём. Окно — от первой загрузки.
	frappe.cache.set(ключ, 0, ex=ОКНО_ЗАГРУЗОК, nx=True)
	загрузок = frappe.cache.incr(ключ)
	if загрузок == ЗАГРУЗОК_НЕ_БОЛЬШЕ + 1:
		raise ДокументНеДоступен(
			f"больше {ЗАГРУЗОК_НЕ_БОЛЬШЕ} загрузок за {ОКНО_ЗАГРУЗОК} с от {frappe.session.user}"
		)
	if загрузок > ЗАГРУЗОК_НЕ_БОЛЬШЕ:
		raise _ПределЗагрузок


class _ПределЗагрузок(ДокументНеДоступен):
	"""Повторный отказ по пределу — без записи в журнал."""


def записать(адрес: str, поля: dict) -> None:
	"""Создать или обновить запись клиента с именем-адресом.

	Одновременные первые подключения одного клиента сходятся на одной записи:
	проигравший откатывается к точке сохранения и коммитит, чтобы следующее
	чтение увидело запись соседа, а не свой старый снимок.
	"""
	frappe.db.savepoint("lms_cimd_client")
	try:
		if frappe.db.exists("OAuth Client", адрес):
			клиент = frappe.get_doc("OAuth Client", адрес)
			# Безусловное сохранение писало бы версию на каждое истечение кэша.
			if all(клиент.get(поле) == значение for поле, значение in поля.items()):
				return
			клиент.update(поля)
			клиент.save(ignore_permissions=True)
		else:
			frappe.get_doc({"doctype": "OAuth Client", **поля}).insert(
				ignore_permissions=True, set_name=адрес
			)
	except (frappe.DuplicateEntryError, frappe.TimestampMismatchError):
		frappe.db.rollback(save_point="lms_cimd_client")
		# Frappe кладёт «уже существует» в сообщения ответа — человеку на
		# странице авторизации оно ни к чему.
		frappe.clear_last_message()
	# Авторизация обычно приходит GET'ом, а GET Frappe не коммитит: без этого
	# запись исчезла бы до кнопки «Разрешить».
	frappe.db.commit()  # nosemgrep
