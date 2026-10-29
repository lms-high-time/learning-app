# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Журнал событий безопасности (learning-services#680)."""

import base64
import json
from types import SimpleNamespace
from unittest import mock

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import set_request
from werkzeug.exceptions import HTTPException

from lms_frappe_app import security_log
from lms_frappe_app.agent_learning import oauth_server
from lms_frappe_app.agent_learning.errors import НЕТ_ПРАВА, ЧУЖОЕ_ЗАНЯТИЕ, Отказ
from lms_frappe_app.api import контракт
from lms_frappe_app.session_guard import отклонить_sid_из_запроса

IP = "203.0.113.7"
#: Значения, которых не бывает в записи ни при каком событии.
СЕКРЕТЫ = (
	"BEARER-VALUE",
	"KEY-VALUE",
	"SID-VALUE",
	"PWD-VALUE",
	"REFRESH-VALUE",
	"API:SECRET",
	"RAW-VALUE",
	"CLIENT-SECRET",
)


class _Запрос(UnitTestCase):
	"""Запрос, IP, ответ и `form_dict` — только на время теста."""

	ИМЕНА = ("request", "request_ip", "response", "form_dict")

	def setUp(self):
		super().setUp()
		self.прежнее = {имя: getattr(frappe.local, имя, None) for имя in self.ИМЕНА}
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(frappe.flags.pop, "lms_security_logged", None)

	def tearDown(self):
		for имя, значение in self.прежнее.items():
			setattr(frappe.local, имя, значение)
		super().tearDown()

	def запрос(self, path="/api/method/x", query_string="", headers=None, method="POST", **форма):
		set_request(path=path, query_string=query_string, headers=headers or {}, method=method)
		frappe.local.request_ip = IP
		frappe.local.form_dict = frappe._dict(форма)
		frappe.local.response = frappe._dict()
		frappe.flags.pop("lms_security_logged", None)

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

	def test_схема_авторизации_по_белому_списку(self):
		"""Заголовок без схемы — сам ключ: в запись идёт `other`, а не его начало."""
		for заголовок, ждём in (
			("Bearer BEARER-VALUE", "bearer"),
			("token API:SECRET", "token"),
			("RAW-VALUE", "other"),
			("Digest RAW-VALUE", "other"),
		):
			with self.subTest(заголовок):
				self.запрос(headers={"Authorization": заголовок})
				(запись,) = self.записи(lambda: security_log.записать("x"))
				self.assertEqual(запись["auth"], ждём)

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
		"""`after_request`: 401, 403, 429, неудачный вход, блокировка и сбой входа;
		прочее — нет."""
		вход = "/api/method/login"
		пользователь = frappe.session.user
		случаи = {
			"403": ("/api/method/x", 403, "PermissionError", {"event": "http_403", "exc": "PermissionError"}),
			"429": ("/api/method/x", 429, "TooManyRequestsError", {"event": "http_429"}),
			"401 по токену": ("/api/method/x", 401, "AuthenticationError", {"event": "http_401"}),
			"неудачный вход": (
				вход,
				401,
				"AuthenticationError",
				{"event": "login_failed", "login": пользователь},
			),
			"блокировка входа": (вход, 500, "SecurityException", {"event": "login_locked"}),
			"блокировка, HTML-ответ": ("/", 500, None, {"event": "login_locked"}),
			"сбой входа": (вход, 500, "ValueError", {"event": "login_error"}),
			"200": ("/api/method/x", 200, None, None),
			"404": ("/api/method/x", 404, "DoesNotExistError", None),
			"500 не на входе": ("/api/method/x", 500, "SecurityException", None),
		}
		for что, (путь, статус, тип, ждём) in случаи.items():
			with self.subTest(что):
				форма = {"usr": пользователь, "pwd": "PWD-VALUE"}
				if путь == "/":
					форма["cmd"] = "login"
				self.запрос(path=путь, headers={"Authorization": "Bearer BEARER-VALUE"}, **форма)
				frappe.local.response.exc_type = тип
				закрыт = что == "блокировка, HTML-ответ"
				with mock.patch.object(security_log, "_заблокирован", return_value=закрыт):
					записи = self.записи(
						lambda: security_log.записать_отказ(
							SimpleNamespace(status_code=статус), frappe.request
						)
					)
				if ждём is None:
					self.assertEqual(записи, [])
					continue
				(запись,) = записи
				self.assertEqual({к: запись.get(к) for к in ждём}, ждём)
				self.assertEqual((запись["ip"], запись["status"], запись["auth"]), (IP, статус, "bearer"))

	def test_неизвестное_имя_входа_не_пишется(self):
		"""В поле имени бывает пароль: неизвестное имя — только отпечатком."""
		self.запрос(path="/api/method/login", usr="RAW-VALUE", pwd="PWD-VALUE")
		(запись,) = self.записи(
			lambda: security_log.записать_отказ(SimpleNamespace(status_code=401), frappe.request)
		)
		self.assertEqual((запись["event"], запись["login_unknown"]), ("login_failed", True))
		self.assertEqual(len(запись["login_hmac"]), 12)
		self.assertNotIn("login", запись)

	def test_отпечаток_имени_с_ключом_сайта(self):
		"""Отпечаток — не голый sha256: словарём его не подобрать; одно имя — один отпечаток."""
		import hashlib

		self.запрос(path="/api/method/login", usr="RAW-VALUE", pwd="PWD-VALUE")
		отказ = lambda: security_log.записать_отказ(SimpleNamespace(status_code=401), frappe.request)  # noqa: E731
		(первая,) = self.записи(отказ)
		(вторая,) = self.записи(отказ)
		self.assertEqual(первая["login_hmac"], вторая["login_hmac"])
		self.assertNotEqual(первая["login_hmac"], hashlib.sha256(b"RAW-VALUE").hexdigest()[:12])

	def test_отпечаток_на_своём_секрете(self):
		"""Отпечаток — на своём секрете сайта, не на `encryption_key`; без
		секрета (до первой миграции) — без отпечатка (learning-services#690)."""
		from unittest.mock import patch

		from lms_frappe_app.site_secrets import ЖУРНАЛ_ВХОДА

		self.запрос(path="/api/method/login", usr="RAW-VALUE", pwd="PWD-VALUE")
		отказ = lambda: security_log.записать_отказ(SimpleNamespace(status_code=401), frappe.request)  # noqa: E731
		with patch.dict(frappe.conf, {ЖУРНАЛ_ВХОДА: "0f" * 32}):
			(прежняя,) = self.записи(отказ)
			with patch.dict(frappe.conf, {"encryption_key": "смена-ключа-шифрования"}):
				(после_смены_ключа,) = self.записи(отказ)
		with patch.dict(frappe.conf, {ЖУРНАЛ_ВХОДА: "1e" * 32}):
			(другой_секрет,) = self.записи(отказ)
		with patch.dict(frappe.conf):
			frappe.conf.pop(ЖУРНАЛ_ВХОДА, None)
			(без_секрета,) = self.записи(отказ)

		self.assertEqual(после_смены_ключа["login_hmac"], прежняя["login_hmac"])
		self.assertNotEqual(другой_секрет["login_hmac"], прежняя["login_hmac"])
		self.assertEqual(без_секрета["login_unknown"], True)
		self.assertNotIn("login_hmac", без_секрета)

	def test_имя_входа_не_строкой(self):
		self.запрос(path="/api/method/login", usr={"name": ["like", "%"]}, pwd="PWD-VALUE")
		(запись,) = self.записи(
			lambda: security_log.записать_отказ(SimpleNamespace(status_code=401), frappe.request)
		)
		self.assertEqual(запись["login_unknown"], True)
		self.assertNotIn("login_hmac", запись)

	def test_записанный_отказ_не_пишется_второй_раз(self):
		self.запрос()
		frappe.flags.lms_security_logged = True
		записи = self.записи(
			lambda: security_log.записать_отказ(SimpleNamespace(status_code=401), frappe.request)
		)
		self.assertEqual(записи, [])

	def test_вход_по_заголовку(self):
		"""Вход ключом API — схемой; Bearer, гость и cookie — без записи."""
		for что, заголовок, пользователь, ждём in (
			("ключ API", "token API:SECRET", "Administrator", "token"),
			(
				"ключ API в Basic",
				"Basic " + base64.b64encode(b"API:SECRET").decode(),
				"Administrator",
				"basic",
			),
			("токен", "Bearer BEARER-VALUE", "Administrator", None),
			("не вошёл", "token API:SECRET", "Guest", None),
			("cookie", None, "Administrator", None),
		):
			with self.subTest(что):
				self.запрос(headers={"Authorization": заголовок} if заголовок else {})
				frappe.set_user(пользователь)
				записи = self.записи(security_log.по_заголовку)
				self.assertEqual([(з["event"], з["auth"]) for з in записи], [("auth", ждём)] if ждём else [])

	def test_вход_в_сессию(self):
		"""Вход — пользователем, который входит; «Войти как» — с `actor`;
		выход (вход гостя) не пишется."""
		self.запрос(path="/api/method/login")
		frappe.set_user("Guest")
		(запись,) = self.записи(lambda: security_log.при_входе(SimpleNamespace(user="u@x")))
		self.assertEqual((запись["event"], запись["user"], запись["ip"]), ("login", "u@x", IP))
		self.assertNotIn("actor", запись)

		frappe.set_user("Administrator")
		(запись,) = self.записи(lambda: security_log.при_входе(SimpleNamespace(user="u@x")))
		self.assertEqual((запись["user"], запись["actor"]), ("u@x", "Administrator"))

		self.assertEqual(self.записи(lambda: security_log.при_входе(SimpleNamespace(user="Guest"))), [])

	def test_отказ_sid(self):
		self.запрос(path="/lms", query_string="sid=SID-VALUE", method="GET")

		def отклонить():
			with self.assertRaises(HTTPException):
				отклонить_sid_из_запроса()

		(запись,) = self.записи(отклонить)
		self.assertEqual((запись["event"], запись["path"], запись["ip"]), ("sid_rejected", "/lms", IP))

	def test_отказ_в_токене(self):
		"""Отказ эндпоинта токенов: код ошибки и клиент — из тела или Basic, без
		токена и секрета; 401 того же запроса второй раз не пишется."""
		basic = "Basic " + base64.b64encode(b"c-basic:CLIENT-SECRET").decode()
		for что, заголовки, форма, клиент in (
			("клиент в теле", {}, {"client_id": "c-1", "client_secret": "CLIENT-SECRET"}, "c-1"),
			("клиент в Basic", {"Authorization": basic}, {}, "c-basic"),
		):
			with self.subTest(что):
				self.запрос(
					path="/api/method/frappe.integrations.oauth2.get_token",
					headers=заголовки,
					grant_type="refresh_token",
					refresh_token="REFRESH-VALUE",
					**форма,
				)
				frappe.local.request = _форма(frappe.request, frappe.local.form_dict)
				frappe.local.response = frappe._dict(error="invalid_grant", http_status_code=400)

				(запись,) = self.записи(oauth_server.записать_отказ_в_токене)

				self.assertEqual(
					{к: запись[к] for к in ("event", "error", "grant", "client", "status")},
					{
						"event": "token_denied",
						"error": "invalid_grant",
						"grant": "refresh_token",
						"client": клиент,
						"status": 400,
					},
				)
				повтор = self.записи(
					lambda: security_log.записать_отказ(SimpleNamespace(status_code=401), frappe.request)
				)
				self.assertEqual(повтор, [])

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


def _форма(запрос, поля: dict):
	"""Тот же запрос с телом формы — как его видит эндпоинт токенов."""
	from werkzeug.test import EnvironBuilder
	from werkzeug.wrappers import Request

	return Request(
		EnvironBuilder(
			path=запрос.path, method="POST", headers=dict(запрос.headers), data=dict(поля)
		).get_environ()
	)


class IntegrationTestОтказАвторскогоМетода(_Запрос, IntegrationTestCase):
	"""Ученик вызвал авторский метод: ответ Frappe на отказ — тот, что строит
	`frappe.app.handle_exception`, и в записи — ученик и его адрес."""

	def test_ученик_вызвал_авторский_метод(self):
		from frappe.app import handle_exception

		from lms_frappe_app.api import authoring
		from lms_frappe_app.tests.sample_data import создать_ученика

		ученик = создать_ученика(f"sl-{frappe.generate_hash(length=6)}@example.com")
		self.запрос(
			path="/api/method/lms_frappe_app.api.authoring.create_course",
			headers={"Accept": "application/json"},
		)
		frappe.set_user(ученик)
		# `frappe.app.init_request` ставит признак запроса API до обработчика.
		прежний = getattr(frappe.local, "is_ajax", None)
		self.addCleanup(setattr, frappe.local, "is_ajax", прежний)
		frappe.local.is_ajax = True

		def вызов():
			try:
				authoring.create_course(title="Курс", summary="Кратко")
			except frappe.PermissionError as отказ:
				ответ = handle_exception(отказ)
				security_log.записать_отказ(ответ, frappe.request)
			else:
				self.fail("авторский метод ученику открыт")

		записи = [з for з in self.записи(вызов) if з["event"] == "http_403"]
		self.assertEqual(len(записи), 1, записи)
		self.assertEqual((записи[0]["user"], записи[0]["ip"], записи[0]["status"]), (ученик, IP, 403))
