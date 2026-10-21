# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Сервер OAuth с валидатором платформы (learning-services#661).

Вход проходит через сервер oauthlib с нашим валидатором напрямую, без HTTP:
эндпоинты Frappe только передают ему запрос, а их подмена проверена отдельно.
"""

import base64
import hashlib
import json
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

import frappe
from frappe.integrations import oauth2
from frappe.tests import IntegrationTestCase
from oauthlib.oauth2 import InvalidClientIdError, InvalidRedirectURIError, MismatchingRedirectURIError

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
		# Валидатор Frappe коммитит сам, а тесту нужен откат.
		коммит = mock.patch.object(frappe.db, "commit")
		коммит.start()
		self.addCleanup(коммит.stop)
		загрузка = mock.patch.object(cimd, "загрузить", return_value=(ДОКУМЕНТ, 300))
		self.загрузить = загрузка.start()
		self.addCleanup(загрузка.stop)
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user(создать_ученика(f"cimd-{frappe.generate_hash(length=6)}@example.com"))
		self.сервер = oauth_server.Server(oauth_server.Валидатор())

	def _авторизовать(self, возврат="http://localhost:54321/callback"):
		адрес = f"{САЙТ}.authorize?" + urlencode(
			{
				"response_type": "code",
				"client_id": АДРЕС,
				"redirect_uri": возврат,
				"scope": "all",
				"state": "s",
				"code_challenge": ВЫЗОВ,
				"code_challenge_method": "S256",
			}
		)
		права, данные = self.сервер.validate_authorization_request(адрес, "GET", "", {})
		заголовки, _, _ = self.сервер.create_authorization_response(адрес, "GET", "", {}, права, данные)
		return parse_qs(urlsplit(заголовки["Location"]).query)["code"][0]

	def _токен(self, **поля):
		_, тело, статус = self.сервер.create_token_response(
			f"{САЙТ}.get_token", "POST", urlencode({"client_id": АДРЕС, **поля}), ФОРМА
		)
		return статус, json.loads(тело)

	def test_клиент_по_адресу_входит_и_обновляет_токен(self):
		код = self._авторизовать()

		клиент = frappe.get_doc("OAuth Client", АДРЕС)
		self.assertEqual((клиент.client_id, клиент.app_name), (АДРЕС, "Claude Code"))
		self.загрузить.assert_called_once_with(АДРЕС)

		статус, токен = self._токен(
			grant_type="authorization_code",
			code=код,
			redirect_uri="http://localhost:54321/callback",
			code_verifier=ПРОВЕРКА,
		)
		self.assertEqual(статус, 200, токен)

		статус, новый = self._токен(grant_type="refresh_token", refresh_token=токен["refresh_token"])
		self.assertEqual(статус, 200, новый)

		with self.subTest("документ — из кэша"):
			self._авторизовать()
			self.загрузить.assert_called_once()

	def test_мёртвый_токен_обновления_invalid_grant(self):
		"""Неизвестный или отозванный токен обновления — `invalid_grant`: Frappe
		отвечал ошибкой «запись не найдена», и агент не шёл на повторный вход
		(learning-services#661)."""
		код = self._авторизовать()
		_, токен = self._токен(
			grant_type="authorization_code",
			code=код,
			redirect_uri="http://localhost:54321/callback",
			code_verifier=ПРОВЕРКА,
		)
		frappe.db.set_value("OAuth Bearer Token", токен["access_token"], "status", "Revoked")
		for что, refresh in (("неизвестный", "нет-такого"), ("отозванный", токен["refresh_token"])):
			with self.subTest(что):
				статус, ответ = self._токен(grant_type="refresh_token", refresh_token=refresh)
				self.assertEqual((статус, ответ.get("error")), (400, "invalid_grant"))

	def test_код_обменивается_только_на_свой_адрес_возврата(self):
		"""Обмен сверяет адрес с тем, к которому привязан код (RFC 6749 §4.1.3)."""
		код = self._авторизовать()
		статус, ответ = self._токен(
			grant_type="authorization_code",
			code=код,
			redirect_uri="http://localhost:60000/callback",
			code_verifier=ПРОВЕРКА,
		)
		self.assertEqual(статус, 400)
		self.assertNotIn("access_token", ответ)

	def test_отказы_клиенту_по_адресу(self):
		with self.subTest("адрес возврата не из документа"):
			with self.assertRaises((InvalidRedirectURIError, MismatchingRedirectURIError)):
				self._авторизовать(возврат="https://evil.example/cb")

		frappe.cache.delete_value(cimd.КЛЮЧ_КЭША + АДРЕС)
		frappe.delete_doc("OAuth Client", АДРЕС, force=True, ignore_permissions=True)
		with self.subTest("документ не принят — клиента нет"):
			self.загрузить.return_value = ({**ДОКУМЕНТ, "client_id": "https://evil.example/meta"}, 300)
			with self.assertRaises(InvalidClientIdError):
				self._авторизовать()
			self.assertFalse(frappe.db.exists("OAuth Client", АДРЕС))

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

	def test_экран_подтверждения_называет_хост_возврата(self):
		ШАБЛОН = "templates/includes/oauth_confirmation.html"
		КОНТЕКСТ = {
			"client_id": "Claude Code",
			"success_url": "/ok",
			"failure_url": "/no",
			"details": ["all"],
		}
		self.addCleanup(lambda: frappe.flags.pop("oauth_credentials", None))
		for что, возврат, есть, нет in (
			(
				"сайт",
				"https://claude.ai/api/mcp/auth_callback",
				"Доступ получит сайт <b>claude.ai</b>",
				"этом компьютере",
			),
			(
				"этот компьютер",
				"http://localhost:54321/callback",
				"программа на&nbsp;этом компьютере (localhost)",
				"сайт <b>",
			),
			("разметка в хосте", "https://<b>x/cb", "", "<b>x"),
		):
			with self.subTest(что):
				frappe.flags.oauth_credentials = {"redirect_uri": возврат}
				страница = frappe.render_template(ШАБЛОН, КОНТЕКСТ)
				self.assertIn(есть, страница)
				self.assertNotIn(нет, страница)
