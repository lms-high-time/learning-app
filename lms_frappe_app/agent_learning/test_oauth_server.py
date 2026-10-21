# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Сервер OAuth с валидатором платформы (learning-services#661, #665).

Вход проходит через сервер oauthlib с нашим валидатором напрямую, без HTTP:
эндпоинты Frappe только передают ему запрос, а их подмена проверена отдельно.
"""

import base64
import hashlib
import json
import time
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

import frappe
from frappe.integrations import oauth2
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime, set_request
from oauthlib.common import Request as ЗапросOAuth
from oauthlib.oauth2 import (
	InvalidClientIdError,
	InvalidRedirectURIError,
	MismatchingRedirectURIError,
	OAuth2Error,
)
from oauthlib.oauth2.rfc6749.errors import MissingCodeChallengeError, UnsupportedCodeChallengeMethodError

from lms_frappe_app.agent_learning import cimd, oauth_server
from lms_frappe_app.agent_learning.test_cimd import ДОКУМЕНТ as ДОКУМЕНТ_CLAUDE_CODE
from lms_frappe_app.tests.sample_data import создать_ученика

АДРЕС = "https://agent.example.com/oauth/client-metadata"
ДОКУМЕНТ = {**ДОКУМЕНТ_CLAUDE_CODE, "client_id": АДРЕС}
САЙТ = "https://cenda.test/api/method/frappe.integrations.oauth2"
ФОРМА = {"Content-Type": "application/x-www-form-urlencoded"}
ПРОВЕРКА = "v" * 64
ВЫЗОВ = base64.urlsafe_b64encode(hashlib.sha256(ПРОВЕРКА.encode()).digest()).decode().rstrip("=")
ЛОКАЛЬНЫЙ = "http://localhost:54321/callback"
ВОЗВРАТ_АГЕНТА = "https://agent.example/cb"


class IntegrationTestOAuthServer(IntegrationTestCase):
	def setUp(self):
		frappe.cache.delete_value(cimd.КЛЮЧ_КЭША + АДРЕС)
		# Счётчик загрузок живёт в redis и переживает откат: без сброса третий
		# прогон подряд упирался бы в предел.
		frappe.cache.delete_value(cimd.КЛЮЧ_СЧЁТЧИКА + "Administrator")
		# Валидатор Frappe коммитит сам, а тесту нужен откат.
		коммит = mock.patch.object(frappe.db, "commit")
		коммит.start()
		self.addCleanup(коммит.stop)
		загрузка = mock.patch.object(cimd, "загрузить", return_value=(ДОКУМЕНТ, 300))
		self.загрузить = загрузка.start()
		self.addCleanup(загрузка.stop)
		self.addCleanup(frappe.set_user, "Administrator")
		self.сервер = oauth_server.Сервер()

	def _войти(self):
		frappe.set_user(создать_ученика(f"cimd-{frappe.generate_hash(length=6)}@example.com"))

	def _авторизовать(self, возврат=ЛОКАЛЬНЫЙ, **поправки):
		поля = {
			"response_type": "code",
			"client_id": АДРЕС,
			"redirect_uri": возврат,
			"scope": "all",
			"state": "s",
			"code_challenge": ВЫЗОВ,
			"code_challenge_method": "S256",
			**поправки,
		}
		адрес = f"{САЙТ}.authorize?" + urlencode({к: з for к, з in поля.items() if з is not None})
		права, данные = self.сервер.validate_authorization_request(адрес, "GET", "", {})
		заголовки, _, _ = self.сервер.create_authorization_response(адрес, "GET", "", {}, права, данные)
		return parse_qs(urlsplit(заголовки["Location"]).query)["code"][0]

	def _токен(self, client_id=АДРЕС, заголовки=None, **поля):
		тело = urlencode({к: з for к, з in {"client_id": client_id, **поля}.items() if з is not None})
		_, ответ, статус = self.сервер.create_token_response(
			f"{САЙТ}.get_token", "POST", тело, {**ФОРМА, **(заголовки or {})}
		)
		return статус, json.loads(ответ)

	def _обменять(self, код, возврат=ЛОКАЛЬНЫЙ, проверка=ПРОВЕРКА, client_id=АДРЕС, **поля):
		return self._токен(
			client_id=client_id,
			grant_type="authorization_code",
			code=код,
			redirect_uri=возврат,
			code_verifier=проверка,
			**поля,
		)

	def _клиент(self, метод="None"):
		"""Клиент динамической регистрации или из desk — не по адресу."""
		return frappe.get_doc(
			{
				"doctype": "OAuth Client",
				"app_name": "Агент",
				"redirect_uris": ВОЗВРАТ_АГЕНТА,
				"default_redirect_uri": ВОЗВРАТ_АГЕНТА,
				"grant_type": "Authorization Code",
				"response_type": "Code",
				"scopes": "all",
				"token_endpoint_auth_method": метод,
			}
		).insert(ignore_permissions=True)

	def _вход_клиента(self, клиент, **поля):
		"""Код клиента `клиент` и обмен его на токен; конфиденциальный — с секретом в теле."""
		секрет = клиент.client_secret if клиент.token_endpoint_auth_method != "None" else None
		код = self._авторизовать(client_id=клиент.name, возврат=ВОЗВРАТ_АГЕНТА)
		статус, токен = self._обменять(
			код, возврат=ВОЗВРАТ_АГЕНТА, client_id=клиент.name, client_secret=секрет, **поля
		)
		self.assertEqual(статус, 200, токен)
		return код, токен

	def _статус(self, токен) -> str:
		return frappe.db.get_value("OAuth Bearer Token", токен["access_token"], "status")

	def _сбросить_кэш(self):
		frappe.cache.delete_value(cimd.КЛЮЧ_КЭША + АДРЕС)

	def test_клиент_по_адресу_входит_и_обновляет_токен(self):
		self._войти()
		код = self._авторизовать()

		клиент = frappe.get_doc("OAuth Client", АДРЕС)
		self.assertEqual((клиент.client_id, клиент.app_name), (АДРЕС, "Claude Code"))
		self.загрузить.assert_called_once_with(АДРЕС)

		статус, токен = self._обменять(код)
		self.assertEqual(статус, 200, токен)

		статус, новый = self._токен(grant_type="refresh_token", refresh_token=токен["refresh_token"])
		self.assertEqual(статус, 200, новый)

		# Публичному клиенту токен обновления — одноразовый (спецификация MCP).
		with self.subTest("прежний токен обновления отозван"):
			статус, ответ = self._токен(grant_type="refresh_token", refresh_token=токен["refresh_token"])
			self.assertEqual((статус, ответ.get("error")), (400, "invalid_grant"))
			статус, _ = self._токен(grant_type="refresh_token", refresh_token=новый["refresh_token"])
			self.assertEqual(статус, 200)

		with self.subTest("документ — из кэша"):
			self._авторизовать()
			self.загрузить.assert_called_once()

	def test_мёртвый_и_чужой_токен_обновления_invalid_grant(self):
		"""Неизвестный, отозванный или выданный другому клиенту токен обновления —
		`invalid_grant`: Frappe отвечал ошибкой «запись не найдена», и агент не
		шёл на повторный вход (learning-services#661). Чужим токеном клиент не
		обновляется — иначе токен, утёкший от одного агента, работал бы у любого."""
		self._войти()
		_, токен = self._обменять(self._авторизовать())
		чужой = frappe.get_doc(
			{
				"doctype": "OAuth Client",
				"app_name": "Другой агент",
				"redirect_uris": "https://agent.example/cb",
				"default_redirect_uri": "https://agent.example/cb",
				"grant_type": "Authorization Code",
				"response_type": "Code",
				"scopes": "all",
				"token_endpoint_auth_method": "None",
			}
		).insert(ignore_permissions=True)
		with self.subTest("чужой"):
			статус, ответ = self._токен(
				client_id=чужой.name, grant_type="refresh_token", refresh_token=токен["refresh_token"]
			)
			self.assertEqual((статус, ответ.get("error")), (400, "invalid_grant"))

		frappe.db.set_value("OAuth Bearer Token", токен["access_token"], "status", "Revoked")
		for что, refresh in (("неизвестный", "нет-такого"), ("отозванный", токен["refresh_token"])):
			with self.subTest(что):
				статус, ответ = self._токен(grant_type="refresh_token", refresh_token=refresh)
				self.assertEqual((статус, ответ.get("error")), (400, "invalid_grant"))

	def test_запрос_без_client_id_с_чужим_токеном_invalid_client(self):
		"""Без `client_id` Frappe ищет клиента по токену: неизвестный давал 403
		мимо OAuth, а без токена и заголовка — 500 (learning-services#663)."""
		for что, форма in (
			("неизвестный токен обновления", {"grant_type": "refresh_token", "refresh_token": "нет-такого"}),
			(
				"код с client_assertion",
				{"grant_type": "authorization_code", "code": "x", "client_assertion": "jwt"},
			),
		):
			with self.subTest(что):
				статус, ответ = self._токен(client_id=None, **форма)
				self.assertEqual((статус, ответ.get("error")), (401, "invalid_client"))

	def test_код_обменивается_только_на_свой_адрес_возврата(self):
		"""Обмен сверяет адрес с тем, к которому привязан код (RFC 6749 §4.1.3)."""
		self._войти()
		статус, ответ = self._обменять(self._авторизовать(), возврат="http://localhost:60000/callback")
		self.assertEqual(статус, 400)
		self.assertNotIn("access_token", ответ)

	def test_pkce_только_s256_и_обязателен_публичным(self):
		"""Публичному клиенту вход держит только PKCE: прежде он был обязателен
		лишь клиентам по адресу, а `plain` принимался (learning-services#665)."""
		self._войти()
		публичный = self._клиент()
		конфиденциальный = self._клиент("Client Secret Post")
		без_pkce = {"code_challenge": None, "code_challenge_method": None}
		plain = {"code_challenge": ПРОВЕРКА, "code_challenge_method": "plain"}
		for что, поправки, ошибка in (
			("клиент по адресу без PKCE", {**без_pkce}, MissingCodeChallengeError),
			("публичный без PKCE", {"client_id": публичный.name, **без_pkce}, MissingCodeChallengeError),
			(
				"plain у публичного",
				{"client_id": публичный.name, **plain},
				UnsupportedCodeChallengeMethodError,
			),
			(
				"plain у конфиденциального",
				{"client_id": конфиденциальный.name, **plain},
				UnsupportedCodeChallengeMethodError,
			),
			(
				"вызов без метода — это plain",
				{"client_id": публичный.name, "code_challenge_method": None},
				UnsupportedCodeChallengeMethodError,
			),
		):
			with self.subTest(что), self.assertRaises(ошибка):
				возврат = ЛОКАЛЬНЫЙ if "client_id" not in поправки else ВОЗВРАТ_АГЕНТА
				self._авторизовать(возврат=возврат, **поправки)

		with self.subTest("код с plain, выданный до learning-services#665"):
			код = self._авторизовать()
			frappe.db.set_value(
				"OAuth Authorization Code",
				код,
				{"code_challenge": ПРОВЕРКА, "code_challenge_method": "plain"},
			)
			статус, ответ = self._обменять(код)
			self.assertEqual((статус, ответ.get("error")), (400, "invalid_grant"))

		with self.subTest("неверная проверка S256"):
			статус, ответ = self._обменять(self._авторизовать(), проверка="w" * 64)
			self.assertEqual((статус, ответ.get("error")), (400, "invalid_grant"))

	def test_отказы_клиенту_по_адресу(self):
		self._войти()
		with self.subTest("адрес возврата не из документа"):
			with self.assertRaises((InvalidRedirectURIError, MismatchingRedirectURIError)):
				self._авторизовать(возврат="https://evil.example/cb")

		self._сбросить_кэш()
		frappe.delete_doc("OAuth Client", АДРЕС, force=True, ignore_permissions=True)
		with self.subTest("документ не принят — клиента нет"):
			self.загрузить.return_value = ({**ДОКУМЕНТ, "client_id": "https://evil.example/meta"}, 300)
			with self.assertRaises(InvalidClientIdError):
				self._авторизовать()
			self.assertFalse(frappe.db.exists("OAuth Client", АДРЕС))

		with self.subTest("загрузка дольше срока"):
			self.загрузить.side_effect = lambda адрес: time.sleep(1)
			with mock.patch.object(cimd, "СРОК_ЗАГРУЗКИ", 0.1):
				начало = time.monotonic()
				self.assertFalse(cimd.обеспечить_клиента(АДРЕС))
				self.assertLess(time.monotonic() - начало, 0.9)

	def test_запись_клиента_следует_за_документом(self):
		self.assertTrue(cimd.обеспечить_клиента(АДРЕС))
		изменена = frappe.db.get_value("OAuth Client", АДРЕС, "modified")

		with self.subTest("кэш истёк, документ тот же — запись не пишется"):
			self._сбросить_кэш()
			self.assertTrue(cimd.обеспечить_клиента(АДРЕС))
			self.assertEqual(frappe.db.get_value("OAuth Client", АДРЕС, "modified"), изменена)

		with self.subTest("кэш истёк, документ изменился — запись обновлена"):
			self._сбросить_кэш()
			self.загрузить.return_value = ({**ДОКУМЕНТ, "client_name": "Claude Code 2"}, 300)
			self.assertTrue(cimd.обеспечить_клиента(АДРЕС))
			self.assertEqual(frappe.db.get_value("OAuth Client", АДРЕС, "app_name"), "Claude Code 2")

		with self.subTest("кэш истёк, документ сломан — отказ, хотя запись есть"):
			self._сбросить_кэш()
			self.загрузить.return_value = ({**ДОКУМЕНТ, "redirect_uris": []}, 300)
			self.assertFalse(cimd.обеспечить_клиента(АДРЕС))

		with self.subTest("документ не загрузился — вход по прежней записи"):
			self._сбросить_кэш()
			self.загрузить.side_effect = cimd.ДокументНеДоступен("ответ 503")
			self.assertTrue(cimd.обеспечить_клиента(АДРЕС))
			self.загрузить.side_effect = None

		with self.subTest("все потоки загрузки заняты — тоже по прежней записи"):
			with mock.patch.object(cimd._места_загрузки, "acquire", return_value=False):
				self.assertTrue(cimd.обеспечить_клиента(АДРЕС))

		with self.subTest("кэш есть, записи нет — загрузка заново"):
			self.загрузить.return_value = (ДОКУМЕНТ, 300)
			self.assertTrue(cimd.обеспечить_клиента(АДРЕС))
			frappe.delete_doc("OAuth Client", АДРЕС, force=True, ignore_permissions=True)
			загрузок = self.загрузить.call_count
			self.assertTrue(cimd.обеспечить_клиента(АДРЕС))
			self.assertEqual(self.загрузить.call_count, загрузок + 1)
			self.assertTrue(frappe.db.exists("OAuth Client", АДРЕС))

	def test_одновременное_первое_подключение(self):
		"""Запись создал соседний запрос — второй не падает и видит её."""
		поля = cimd.поля_клиента(АДРЕС, ДОКУМЕНТ)
		cimd.записать(АДРЕС, поля)
		with mock.patch.object(frappe.db, "exists", return_value=None):
			cimd.записать(АДРЕС, поля)
		self.assertEqual(frappe.db.get_value("OAuth Client", АДРЕС, "app_name"), "Claude Code")

	def test_адрес_в_другом_регистре_не_занимает_запись(self):
		"""Имена записей сравниваются без регистра: чужой `/OAuth/...` иначе
		переписал бы адреса возврата клиента `/oauth/...`. Держится на
		collation MariaDB: на Postgres имена различаются, и это другая запись."""
		self.assertTrue(cimd.обеспечить_клиента(АДРЕС))
		другой = АДРЕС.replace("/oauth/", "/OAuth/")
		self.загрузить.return_value = (
			{**ДОКУМЕНТ, "client_id": другой, "redirect_uris": ["https://evil.example/cb"]},
			300,
		)
		self.assertFalse(cimd.обеспечить_клиента(другой))
		self.assertEqual(
			frappe.db.get_value("OAuth Client", АДРЕС, "default_redirect_uri"), "http://localhost/callback"
		)

	def test_предел_загрузок_от_пользователя(self):
		self._войти()
		with mock.patch.object(cimd, "ЗАГРУЗОК_НЕ_БОЛЬШЕ", 1):
			self.assertTrue(cimd.обеспечить_клиента(АДРЕС))
			self._сбросить_кэш()
			frappe.delete_doc("OAuth Client", АДРЕС, force=True, ignore_permissions=True)
			self.assertFalse(cimd.обеспечить_клиента(АДРЕС))
			self.assertFalse(cimd.обеспечить_клиента(АДРЕС))
		self.загрузить.assert_called_once()

	def test_адреса_клиента_через_перевод_строки(self):
		"""Динамическая регистрация Frappe пишет адреса через перевод строки, а
		проверка Frappe делила их пробелом: клиент с двумя адресами не входил
		ни по одному (learning-services#661)."""
		клиент = frappe.get_doc(
			{
				"doctype": "OAuth Client",
				"app_name": "Агент",
				"redirect_uris": "https://a.example/cb\nhttps://b.example/cb",
				"default_redirect_uri": "https://a.example/cb",
				"grant_type": "Authorization Code",
				"response_type": "Code",
				"scopes": "all",
			}
		).insert(ignore_permissions=True)
		for адрес in ("https://a.example/cb", "https://b.example/cb"):
			with self.subTest(адрес):
				self.assertTrue(oauth_server.Валидатор().validate_redirect_uri(клиент.name, адрес, None))

	def test_эндпоинты_с_валидатором_платформы(self):
		for метод in (
			"authorize",
			"approve",
			"get_token",
			"revoke_token",
			"introspect_token",
			"register_client",
		):
			with self.subTest(метод):
				self.assertEqual(
					frappe.get_hooks("override_whitelisted_methods")[f"frappe.integrations.oauth2.{метод}"],
					[f"lms_frappe_app.agent_learning.oauth_server.{метод}"],
				)
		self.addCleanup(setattr, frappe.local, "oauth_server", None)
		oauth_server._свой_сервер()
		сервер = oauth2.get_oauth_server()
		self.assertIsInstance(сервер, oauth_server.Сервер)
		self.assertIsInstance(сервер.request_validator, oauth_server.Валидатор)

	def test_метаданные_обещают_клиентов_по_адресу(self):
		with (
			mock.patch.object(oauth2, "is_oauth_metadata_enabled", return_value=True),
			mock.patch.object(oauth2, "get_resource_url", return_value="https://cenda.test"),
		):
			метаданные = json.loads(oauth_server.authorization_server_metadata().get_data())
		self.assertIs(метаданные["client_id_metadata_document_supported"], True)
		# Claude выбирает клиента по адресу, только если сервер принимает публичных.
		self.assertIn("none", метаданные["token_endpoint_auth_methods_supported"])
		self.assertEqual(метаданные["issuer"], "https://cenda.test")

	def test_только_код_авторизации_и_обновление(self):
		"""Frappe разрешал всем клиентам вход по паролю — мимо экрана согласия —
		и ответы implicit и hybrid (learning-services#665)."""
		self._войти()
		for ответ in ("token", "id_token", "id_token token", "code id_token", "code token id_token", "none"):
			with self.subTest(ответ), self.assertRaises(OAuth2Error):
				self._авторизовать(response_type=ответ)

		клиент = self._клиент("Client Secret Post")
		for что, поля in (
			("пароль", {"grant_type": "password", "username": "Administrator", "password": "admin"}),
			("учётные данные клиента", {"grant_type": "client_credentials"}),
		):
			with (
				self.subTest(что),
				mock.patch("frappe.oauth.LoginManager") as пароль,
			):
				статус, ответ = self._токен(client_id=клиент.name, client_secret=клиент.client_secret, **поля)
				self.assertEqual(статус, 400, ответ)
				self.assertNotIn("access_token", ответ)
				# Пароль не сверяется вовсе: иначе эндпоинт отвечал бы, верен ли он.
				пароль.assert_not_called()

	def test_конфиденциальный_клиент_входит_только_с_секретом(self):
		"""Frappe не сверял секрет: конфиденциальный клиент входил по одному
		`client_id` (learning-services#665). Секрет — в теле, как шлют веб-чат и
		сервис MCP, или заголовком Basic."""
		self._войти()
		пост = self._клиент("Client Secret Post")
		базовый = self._клиент("Client Secret Basic")

		def basic(клиент, секрет):
			return {"Authorization": "Basic " + base64.b64encode(f"{клиент.name}:{секрет}".encode()).decode()}

		for что, клиент, поля, ожидается in (
			("без секрета", пост, {}, 401),
			("неверный секрет", пост, {"client_secret": "не-тот"}, 401),
			("секрет в теле", пост, {"client_secret": пост.client_secret}, 200),
			("секрет в теле у клиента Basic", базовый, {"client_secret": базовый.client_secret}, 200),
			(
				"заголовок Basic",
				базовый,
				{"client_id": None, "заголовки": basic(базовый, базовый.client_secret)},
				200,
			),
			(
				"Basic с неверным секретом",
				базовый,
				{"client_id": None, "заголовки": basic(базовый, "x")},
				401,
			),
		):
			with self.subTest(что):
				код = self._авторизовать(
					client_id=клиент.name,
					возврат=ВОЗВРАТ_АГЕНТА,
					code_challenge=None,
					code_challenge_method=None,
				)
				статус, ответ = self._обменять(
					код, **{"возврат": ВОЗВРАТ_АГЕНТА, "проверка": None, "client_id": клиент.name, **поля}
				)
				self.assertEqual(статус, ожидается, ответ)
				if ожидается == 401:
					self.assertEqual(ответ.get("error"), "invalid_client")

		with self.subTest("обновление без секрета"):
			_, токен = self._вход_клиента(пост)
			статус, ответ = self._токен(
				client_id=пост.name, grant_type="refresh_token", refresh_token=токен["refresh_token"]
			)
			self.assertEqual((статус, ответ.get("error")), (401, "invalid_client"))
			self.assertEqual(self._статус(токен), "Active")

	def test_код_живёт_десять_минут_и_обменивается_один_раз(self):
		"""У кода не было срока, а повторный обмен не отзывал выданное по нему
		(learning-services#665)."""
		self._войти()
		клиент = self._клиент()

		with self.subTest("срок ставится при выдаче"):
			код = self._авторизовать(client_id=клиент.name, возврат=ВОЗВРАТ_АГЕНТА)
			срок = frappe.db.get_value("OAuth Authorization Code", код, "expiration_time")
			ожидается = add_to_date(now_datetime(), seconds=oauth_server.СРОК_КОДА)
			self.assertLess(abs((срок - ожидается).total_seconds()), 60)

		with self.subTest("истёкший"):
			frappe.db.set_value(
				"OAuth Authorization Code", код, "expiration_time", add_to_date(now_datetime(), seconds=-1)
			)
			статус, ответ = self._обменять(код, возврат=ВОЗВРАТ_АГЕНТА, client_id=клиент.name)
			self.assertEqual((статус, ответ.get("error")), (400, "invalid_grant"))

		for что, удалить_код in (("повтор", False), ("повтор после ежечасной чистки кодов", True)):
			with self.subTest(что):
				код, токен = self._вход_клиента(клиент)
				_, обновлённый = self._токен(
					client_id=клиент.name, grant_type="refresh_token", refresh_token=токен["refresh_token"]
				)
				if удалить_код:
					frappe.db.delete("OAuth Authorization Code", {"name": код})
				статус, ответ = self._обменять(код, возврат=ВОЗВРАТ_АГЕНТА, client_id=клиент.name)
				self.assertEqual((статус, ответ.get("error")), (400, "invalid_grant"))
				# Отозвана вся семья, и токен, полученный обновлением, тоже.
				self.assertEqual(self._статус(обновлённый), "Revoked")

	def test_токен_обновления_одноразовый_у_всех_клиентов(self):
		"""Ротация была только у клиентов по адресу, повтор заменённого токена
		не отзывал ничего, а срока у семьи не было (learning-services#665)."""
		self._войти()
		клиент = self._клиент()

		def обновить(токен):
			return self._токен(
				client_id=клиент.name, grant_type="refresh_token", refresh_token=токен["refresh_token"]
			)

		def постарить(токен, **сдвиг):
			frappe.db.set_value(
				"OAuth Bearer Token", токен["access_token"], "creation", add_to_date(now_datetime(), **сдвиг)
			)

		with self.subTest("ротация переносит семью"):
			код, первый = self._вход_клиента(клиент)
			статус, второй = обновить(первый)
			self.assertEqual(статус, 200, второй)
			self.assertEqual(self._статус(первый), "Revoked")
			семья = frappe.db.get_value(
				"OAuth Bearer Token",
				второй["access_token"],
				[oauth_server.ПОЛЕ_СЕМЬИ, oauth_server.ПОЛЕ_НАЧАЛА, oauth_server.ПОЛЕ_ПРЕЖНЕГО],
			)
			начало = frappe.db.get_value(
				"OAuth Bearer Token", первый["access_token"], oauth_server.ПОЛЕ_НАЧАЛА
			)
			self.assertEqual(семья, (код, начало, oauth_server.отпечаток(первый["refresh_token"])))

		with self.subTest("повтор в окне гонки — отказ без отзыва семьи"):
			статус, ответ = обновить(первый)
			self.assertEqual((статус, ответ.get("error")), (400, "invalid_grant"))
			self.assertEqual(self._статус(второй), "Active")

		with self.subTest("повтор заменённого отзывает семью, и после ежечасной чистки"):
			постарить(второй, minutes=-2)
			frappe.db.delete("OAuth Bearer Token", {"name": первый["access_token"]})
			статус, ответ = обновить(первый)
			self.assertEqual((статус, ответ.get("error")), (400, "invalid_grant"))
			self.assertEqual(self._статус(второй), "Revoked")

		with self.subTest("семья старше предельного срока"):
			_, токен = self._вход_клиента(клиент)
			frappe.db.set_value(
				"OAuth Bearer Token",
				токен["access_token"],
				oauth_server.ПОЛЕ_НАЧАЛА,
				now_datetime() - oauth_server.СРОК_СЕМЬИ - timedelta(minutes=1),
			)
			статус, ответ = обновить(токен)
			self.assertEqual((статус, ответ.get("error")), (400, "invalid_grant"))
			self.assertEqual(self._статус(токен), "Revoked")

		with self.subTest("токен до learning-services#665 — сам себе семья"):
			прежний = {"access_token": frappe.generate_hash(), "refresh_token": frappe.generate_hash()}
			frappe.get_doc(
				{
					"doctype": "OAuth Bearer Token",
					"client": клиент.name,
					"user": frappe.session.user,
					"scopes": "all",
					"expires_in": 3600,
					"status": "Active",
					**прежний,
				}
			).insert(ignore_permissions=True)
			статус, новый = обновить(прежний)
			self.assertEqual(статус, 200, новый)
			выдан = frappe.db.get_value("OAuth Bearer Token", прежний["access_token"], "creation")
			self.assertEqual(
				frappe.db.get_value(
					"OAuth Bearer Token",
					новый["access_token"],
					[oauth_server.ПОЛЕ_СЕМЬИ, oauth_server.ПОЛЕ_НАЧАЛА],
				),
				(прежний["access_token"], выдан),
			)

	def test_отзыв_только_своему_клиенту(self):
		"""Frappe отзывал любой токен, кто бы о нём ни попросил
		(learning-services#665). Ответ один — 200, по нему не узнать, чей токен."""
		self._войти()
		_, токен = self._обменять(self._авторизовать())
		чужой = self._клиент()
		конфиденциальный = self._клиент("Client Secret Post")

		def отозвать(**поля):
			_, _, статус = self.сервер.create_revocation_response(
				f"{САЙТ}.revoke_token", "POST", urlencode(поля), ФОРМА
			)
			return статус

		for что, поля, ожидается in (
			("чужой клиент", {"client_id": чужой.name}, 200),
			("конфиденциальный без секрета", {"client_id": конфиденциальный.name}, 401),
		):
			with self.subTest(что):
				self.assertEqual(отозвать(token=токен["access_token"], **поля), ожидается)
				self.assertEqual(self._статус(токен), "Active")

		with self.subTest("свой клиент — токен и его семья"):
			_, новый = self._токен(grant_type="refresh_token", refresh_token=токен["refresh_token"])
			self.assertEqual(
				отозвать(token=новый["refresh_token"], token_type_hint="refresh_token", client_id=АДРЕС), 200
			)
			self.assertEqual(self._статус(новый), "Revoked")

	def test_интроспекция_только_клиенту_с_секретом(self):
		"""Интроспекция отвечала любому гостю; `exp` уходил на 5 часов вперёд
		(наивное время сайта в Asia/Samarkand как UTC), а истёкший токен
		назывался действующим (learning-services#665)."""
		self._войти()
		_, токен = self._обменять(self._авторизовать())
		сервис = self._клиент("Client Secret Basic")

		def спросить(**поля):
			запрос = ЗапросOAuth(f"{САЙТ}.introspect_token", "POST", urlencode(поля), ФОРМА)
			return oauth_server.ответ_интроспекции(запрос, поля.get("token"), поля.get("token_type_hint"))

		for что, поля in (
			("без клиента", {}),
			("публичный клиент", {"client_id": АДРЕС}),
			("неверный секрет", {"client_id": сервис.name, "client_secret": "не-тот"}),
		):
			with self.subTest(что):
				self.assertEqual(
					спросить(token=токен["access_token"], **поля), (401, {"error": "invalid_client"})
				)

		свой = {"client_id": сервис.name, "client_secret": сервис.client_secret}
		with self.subTest("действующий"):
			статус, ответ = спросить(token=токен["access_token"], **свой)
			self.assertEqual((статус, ответ["active"], ответ["client_id"]), (200, True, АДРЕС))
			self.assertLess(abs(ответ["exp"] - (time.time() + токен["expires_in"])), 60)
		with self.subTest("токен обновления"):
			_, ответ = спросить(token=токен["refresh_token"], token_type_hint="refresh_token", **свой)
			self.assertIs(ответ["active"], True)

		frappe.db.set_value(
			"OAuth Bearer Token",
			токен["access_token"],
			"expiration_time",
			add_to_date(now_datetime(), seconds=-1),
		)
		with self.subTest("истёкший"):
			self.assertEqual(спросить(token=токен["access_token"], **свой), (200, {"active": False}))
		frappe.db.set_value("OAuth Bearer Token", токен["access_token"], "status", "Revoked")
		for что, значение in (("отозванный", токен["refresh_token"]), ("неизвестный", "нет-такого")):
			with self.subTest(что):
				self.assertEqual(спросить(token=значение, **свой), (200, {"active": False}))

	def test_регистрация_клиента_с_областью_all_и_пределом(self):
		"""Клиент выбирал область сам, вплоть до `openid`, а регистрация была
		без предела (learning-services#665)."""
		настоящий = frappe.get_cached_value
		with (
			mock.patch.object(
				frappe,
				"get_cached_value",
				side_effect=lambda *а, **к: 1
				if а[-1:] == ("enable_dynamic_client_registration",)
				else настоящий(*а, **к),
			),
			mock.patch.object(oauth_server, "РЕГИСТРАЦИЙ_ЗА_ЧАС", 1),
		):
			for имя in ("request", "request_ip", "form_dict"):
				self.addCleanup(setattr, frappe.local, имя, getattr(frappe.local, имя, None))
			frappe.local.request_ip = f"test-{frappe.generate_hash(length=8)}"
			frappe.local.form_dict = frappe._dict(cmd="frappe.integrations.oauth2.register_client")
			заявка = {"client_name": "Агент", "redirect_uris": [ВОЗВРАТ_АГЕНТА], "scope": "openid all"}

			set_request(method="POST", path="/", json=заявка)
			ответ = json.loads(oauth_server.register_client().get_data())
			self.assertEqual(ответ["scope"], "all")
			self.assertEqual(frappe.db.get_value("OAuth Client", ответ["client_id"], "scopes"), "all")

			set_request(method="POST", path="/", json=заявка)
			with self.assertRaises(frappe.RateLimitExceededError):
				oauth_server.register_client()

	def test_токены_отзываются_при_смене_доступа(self):
		"""Отключённый аккаунт сохранял токены агента, и токен обновления выдавал
		новые (learning-services#631); смена пароля их тоже не трогала
		(learning-services#665)."""
		клиент = self._клиент()

		def с_токеном():
			пользователь = frappe.get_doc(
				"User", создать_ученика(f"revoke-{frappe.generate_hash(length=6)}@example.com")
			)
			токен = {"access_token": frappe.generate_hash(), "refresh_token": frappe.generate_hash()}
			frappe.get_doc(
				{
					"doctype": "OAuth Bearer Token",
					"client": клиент.name,
					"user": пользователь.name,
					"scopes": "all",
					"expires_in": 3600,
					**токен,
				}
			).insert(ignore_permissions=True)
			return пользователь, токен

		for что, правка, ожидается in (
			("сохранение без изменений", lambda п: None, "Active"),
			("смена пароля в desk", lambda п: setattr(п, "new_password", "Kx9#vLq2!mPz7-рыба"), "Revoked"),
			("отключение", lambda п: setattr(п, "enabled", 0), "Revoked"),
		):
			with self.subTest(что):
				пользователь, токен = с_токеном()
				правка(пользователь)
				пользователь.save(ignore_permissions=True)
				self.assertEqual(self._статус(токен), ожидается)

		with self.subTest("удаление"):
			пользователь, токен = с_токеном()
			frappe.delete_doc("User", пользователь.name, ignore_permissions=True)
			self.assertFalse(frappe.db.exists("OAuth Bearer Token", токен["access_token"]))
