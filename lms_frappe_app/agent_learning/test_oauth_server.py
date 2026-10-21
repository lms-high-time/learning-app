# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Сервер OAuth с валидатором платформы (learning-services#661).

Вход проходит через сервер oauthlib с нашим валидатором напрямую, без HTTP:
эндпоинты Frappe только передают ему запрос, а их подмена проверена отдельно.
"""

import base64
import hashlib
import json
import time
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

import frappe
from frappe.integrations import oauth2
from frappe.tests import IntegrationTestCase
from oauthlib.oauth2 import (
	InvalidClientIdError,
	InvalidRedirectURIError,
	MismatchingRedirectURIError,
)
from oauthlib.oauth2.rfc6749.errors import MissingCodeChallengeError

from lms_frappe_app.agent_learning import cimd, oauth_server
from lms_frappe_app.agent_learning.test_cimd import ДОКУМЕНТ as ДОКУМЕНТ_CLAUDE_CODE
from lms_frappe_app.tests.sample_data import создать_ученика

АДРЕС = "https://agent.example.com/oauth/client-metadata"
ДОКУМЕНТ = {**ДОКУМЕНТ_CLAUDE_CODE, "client_id": АДРЕС}
САЙТ = "https://cenda.test/api/method/frappe.integrations.oauth2"
ФОРМА = {"Content-Type": "application/x-www-form-urlencoded"}
ПРОВЕРКА = "v" * 64
ВЫЗОВ = base64.urlsafe_b64encode(hashlib.sha256(ПРОВЕРКА.encode()).digest()).decode().rstrip("=")


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
		self.сервер = oauth_server.Server(oauth_server.Валидатор())

	def _войти(self):
		frappe.set_user(создать_ученика(f"cimd-{frappe.generate_hash(length=6)}@example.com"))

	def _авторизовать(self, возврат="http://localhost:54321/callback", **поправки):
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

	def _токен(self, client_id=АДРЕС, **поля):
		_, тело, статус = self.сервер.create_token_response(
			f"{САЙТ}.get_token", "POST", urlencode({"client_id": client_id, **поля}), ФОРМА
		)
		return статус, json.loads(тело)

	def _обменять(self, код, возврат="http://localhost:54321/callback", проверка=ПРОВЕРКА):
		return self._токен(
			grant_type="authorization_code", code=код, redirect_uri=возврат, code_verifier=проверка
		)

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

	def test_код_обменивается_только_на_свой_адрес_возврата(self):
		"""Обмен сверяет адрес с тем, к которому привязан код (RFC 6749 §4.1.3)."""
		self._войти()
		статус, ответ = self._обменять(self._авторизовать(), возврат="http://localhost:60000/callback")
		self.assertEqual(статус, 400)
		self.assertNotIn("access_token", ответ)

	def test_клиенту_по_адресу_нужен_pkce_s256(self):
		"""Клиент по адресу публичный, и вход держит только PKCE."""
		self._войти()
		with self.subTest("без PKCE"), self.assertRaises(MissingCodeChallengeError):
			self._авторизовать(code_challenge=None, code_challenge_method=None)
		with self.subTest("plain"):
			код = self._авторизовать(code_challenge=ПРОВЕРКА, code_challenge_method="plain")
			статус, ответ = self._обменять(код)
			self.assertEqual(статус, 400)
			self.assertNotIn("access_token", ответ)

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
		for метод in ("authorize", "approve", "get_token", "revoke_token"):
			with self.subTest(метод):
				self.assertEqual(
					frappe.get_hooks("override_whitelisted_methods")[f"frappe.integrations.oauth2.{метод}"],
					[f"lms_frappe_app.agent_learning.oauth_server.{метод}"],
				)
		self.addCleanup(setattr, frappe.local, "oauth_server", None)
		oauth_server._свой_сервер()
		сервер = oauth2.get_oauth_server()
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
