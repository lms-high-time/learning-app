# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Журнал событий безопасности (learning-services#680)."""

import json
from types import SimpleNamespace

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import set_request
from werkzeug.exceptions import HTTPException

from lms_frappe_app import security_log
from lms_frappe_app.agent_learning import oauth_server
from lms_frappe_app.agent_learning.errors import НЕТ_ПРАВА, ЧУЖОЕ_ЗАНЯТИЕ, Отказ
from lms_frappe_app.api import контракт
from lms_frappe_app.session_guard import отклонить_sid_из_запроса

IP = "203.0.113.7"
#: Значения, которых не бывает в записи ни при каком событии.
СЕКРЕТЫ = ("BEARER-VALUE", "KEY-VALUE", "SID-VALUE", "PWD-VALUE", "REFRESH-VALUE", "API:SECRET")


class _Запрос(UnitTestCase):
	"""Запрос, IP, ответ и `form_dict` — только на время теста."""

	ИМЕНА = ("request", "request_ip", "response", "form_dict")

	def setUp(self):
		super().setUp()
		self.прежнее = {имя: getattr(frappe.local, имя, None) for имя in self.ИМЕНА}
		self.addCleanup(frappe.set_user, "Administrator")

	def tearDown(self):
		for имя, значение in self.прежнее.items():
			setattr(frappe.local, имя, значение)
		super().tearDown()

	def запрос(self, path="/api/method/x", query_string="", headers=None, method="POST", **форма):
		set_request(path=path, query_string=query_string, headers=headers or {}, method=method)
		frappe.local.request_ip = IP
		frappe.local.form_dict = frappe._dict(форма)
		frappe.local.response = frappe._dict()

	def записи(self, действие) -> list[dict]:
		"""Записи журнала за время действия; ни в одной нет секрета."""
		with self.assertLogs(security_log.ИМЯ_ЖУРНАЛА, "INFO") as журнал:
			действие()
			# assertLogs требует хотя бы одну запись: пустой итог — маркер.
			security_log._ЖУРНАЛ.info(json.dumps({"event": "-"}))
		строки = [з.getMessage() for з in журнал.records]
		for строка in строки:
			for секрет in СЕКРЕТЫ:
				self.assertNotIn(секрет, строка)
		return [з for з in map(json.loads, строки) if з["event"] != "-"]


class UnitTestSecurityLog(_Запрос):
	def test_запись_без_секретов(self):
		"""Контекст запроса — в записи; параметры, значение заголовка и поля с
		секретными именами — нет."""
		self.запрос(
			path="/update-password",
			query_string="key=KEY-VALUE&sid=SID-VALUE",
			headers={"Authorization": "Bearer BEARER-VALUE"},
		)

		(запись,) = self.записи(
			lambda: security_log.записать(
				"x", refresh_token="REFRESH-VALUE", details={"api_key": "KEY-VALUE", "session": "s-1"}
			)
		)

		self.assertTrue(запись["ts"].endswith("Z"), запись["ts"])
		self.assertEqual(
			{к: запись[к] for к in ("event", "user", "ip", "method", "path", "auth")},
			{
				"event": "x",
				"user": "Administrator",
				"ip": IP,
				"method": "POST",
				"path": "/update-password",
				"auth": "bearer",
			},
		)
		self.assertEqual(запись["refresh_token"], "***")
		self.assertEqual(запись["details"], {"api_key": "***", "session": "s-1"})

	def test_отказ_контракта_о_доступе(self):
		"""Пишутся отказы «не ваше»; прочие отказы — ход занятия, их нет."""
		self.запрос()

		@контракт
		def метод(код):
			raise Отказ(код, "нельзя", session="s-1")

		for код, пишется in ((НЕТ_ПРАВА, True), (ЧУЖОЕ_ЗАНЯТИЕ, True), ("attempts_exhausted", False)):
			with self.subTest(код):
				записи = self.записи(lambda: метод(код))
				if not пишется:
					self.assertEqual(записи, [])
					continue
				(запись,) = записи
				self.assertEqual(
					(запись["event"], запись["reason"], запись["ip"], запись["details"]),
					("contract_denied", код, IP, {"session": "s-1"}),
				)
				self.assertTrue(запись["function"].endswith("метод"), запись["function"])

	def test_ответы_с_отказом(self):
		"""`after_request`: 401, 403, 429, неудачный вход и блокировка; прочее — нет."""
		вход = "/api/method/login"
		случаи = {
			"403": ("/api/method/x", 403, "PermissionError", {"event": "http_403", "exc": "PermissionError"}),
			"429": ("/api/method/x", 429, "TooManyRequestsError", {"event": "http_429"}),
			"401 по токену": ("/api/method/x", 401, "AuthenticationError", {"event": "http_401"}),
			"неудачный вход": (вход, 401, "AuthenticationError", {"event": "login_failed", "login": "u@x"}),
			"блокировка входа": (вход, 500, "SecurityException", {"event": "login_locked", "login": "u@x"}),
			"200": ("/api/method/x", 200, None, None),
			"404": ("/api/method/x", 404, "DoesNotExistError", None),
			"500 не на входе": ("/api/method/x", 500, "SecurityException", None),
		}
		for что, (путь, статус, тип, ждём) in случаи.items():
			with self.subTest(что):
				self.запрос(
					path=путь, headers={"Authorization": "Bearer BEARER-VALUE"}, usr="u@x", pwd="PWD-VALUE"
				)
				frappe.local.response.exc_type = тип
				записи = self.записи(
					lambda: security_log.записать_отказ(SimpleNamespace(status_code=статус), frappe.request)
				)
				if ждём is None:
					self.assertEqual(записи, [])
					continue
				(запись,) = записи
				self.assertEqual({к: запись.get(к) for к in ждём}, ждём)
				self.assertEqual((запись["ip"], запись["status"], запись["auth"]), (IP, статус, "bearer"))

	def test_вход_по_заголовку(self):
		"""Ключ API или токен — схемой; гость и запрос без заголовка — без записи."""
		for что, заголовок, пользователь, ждём in (
			("ключ API", "token API:SECRET", "Administrator", "token"),
			("токен", "Bearer BEARER-VALUE", "Administrator", "bearer"),
			("не вошёл", "Bearer BEARER-VALUE", "Guest", None),
			("cookie", None, "Administrator", None),
		):
			with self.subTest(что):
				self.запрос(headers={"Authorization": заголовок} if заголовок else {})
				frappe.set_user(пользователь)
				записи = self.записи(security_log.по_заголовку)
				self.assertEqual([(з["event"], з["auth"]) for з in записи], [("auth", ждём)] if ждём else [])

	def test_вход_в_сессию(self):
		self.запрос(path="/api/method/login")
		(запись,) = self.записи(lambda: security_log.при_входе(SimpleNamespace(user="u@x")))
		self.assertEqual((запись["event"], запись["user"], запись["ip"]), ("login", "u@x", IP))

	def test_отказ_sid(self):
		self.запрос(path="/lms", query_string="sid=SID-VALUE", method="GET")

		def отклонить():
			with self.assertRaises(HTTPException):
				отклонить_sid_из_запроса()

		(запись,) = self.записи(отклонить)
		self.assertEqual((запись["event"], запись["path"], запись["ip"]), ("sid_rejected", "/lms", IP))

	def test_отказ_в_токене(self):
		"""Отказ эндпоинта токенов: код ошибки и клиент, без токена из формы."""
		self.запрос(
			path="/api/method/frappe.integrations.oauth2.get_token",
			grant_type="refresh_token",
			refresh_token="REFRESH-VALUE",
			client_id="c-1",
		)
		frappe.local.response = frappe._dict(error="invalid_grant", http_status_code=400)

		(запись,) = self.записи(oauth_server.записать_отказ_в_токене)

		self.assertEqual(
			{к: запись[к] for к in ("event", "error", "grant", "client", "status")},
			{
				"event": "token_denied",
				"error": "invalid_grant",
				"grant": "refresh_token",
				"client": "c-1",
				"status": 400,
			},
		)

	def test_сбой_записи_не_роняет_запрос(self):
		self.запрос()
		with self.assertNoLogs(security_log.ИМЯ_ЖУРНАЛА):
			security_log.записать("x", поле=object.__new__(_Сломанный))

	def test_хуки_подключены(self):
		self.assertIn("lms_frappe_app.security_log.записать_отказ", frappe.get_hooks("after_request"))
		self.assertIn("lms_frappe_app.security_log.при_входе", frappe.get_hooks("on_login"))
		self.assertIn("lms_frappe_app.security_log.по_заголовку", frappe.get_hooks("auth_hooks"))


class _Сломанный:
	def __str__(self):
		raise RuntimeError("сбой")
