# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Методы страницы «Агенты и сессии» (learning-services#669): проводка к
сервису, ответ по контракту и кто может их позвать. Ветки сервиса — в
`tests/test_connections.py`."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.password import update_password

from lms_frappe_app.api import account
from lms_frappe_app.api.test_contract import примеры_ответов
from lms_frappe_app.connections import НЕВЕРНЫЙ_ПАРОЛЬ, ПОДКЛЮЧЕНИЕ_НЕ_НАЙДЕНО, отпечаток
from lms_frappe_app.tests.sample_data import создать_ученика
from lms_frappe_app.tests.test_connections import (
	есть_сессия,
	завести_клиента,
	завести_сессию,
	завести_токен,
)
from lms_frappe_app.tests.test_consent import запрос

МЕТОД = "lms_frappe_app.api.account."


class IntegrationTestAccountApi(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=8)
		self.ученик = создать_ученика(f"account-{суффикс}@example.com")
		self.пароль = f"Пароль страницы {суффикс}"
		update_password(self.ученик, self.пароль)
		self.суффикс = суффикс

	def сверить(self, метод: str, ответ: dict) -> None:
		"""Ключи ответа — те же, что в примере CONTRACT.md."""
		часть, примеры = (
			("data", примеры_ответов()) if ответ["ok"] else ("error", примеры_ответов(успех=False))
		)
		self.assertIn(frozenset(ответ[часть]), примеры.get(МЕТОД + метод, []), ответ)

	def test_методы_зовут_сервис_и_отвечают_по_контракту(self):
		клиент = завести_клиента(app_name="Claude")
		семья = f"api-{self.суффикс}"
		токен = завести_токен(клиент, self.ученик, семья)
		первая, вторая, третья = (завести_сессию(self.ученик) for _ in range(3))
		frappe.set_user(self.ученик)

		ответ = account.disconnect_agent(connection=отпечаток(клиент, семья))
		self.сверить("disconnect_agent", ответ)
		self.assertEqual(frappe.db.get_value("OAuth Bearer Token", токен, "status"), "Revoked")

		ответ = account.end_session(session=отпечаток(первая), password="не тот")
		self.assertEqual(ответ["error"]["code"], НЕВЕРНЫЙ_ПАРОЛЬ)
		self.сверить("end_session", ответ)
		self.assertTrue(есть_сессия(первая))

		ответ = account.end_session(session=отпечаток(первая), password=self.пароль)
		self.сверить("end_session", ответ)
		self.assertFalse(есть_сессия(первая))

		ответ = account.end_other_sessions(password=self.пароль)
		self.сверить("end_other_sessions", ответ)
		self.assertEqual(ответ["data"]["ended"], 2)
		self.assertEqual([есть_сессия(sid) for sid in (вторая, третья)], [False, False])

	def test_зовёт_только_сам_пользователь_со_страницы(self):
		"""Агент по токену и вход «от имени» — отказ; без токена CSRF — ошибка
		Frappe, даже если у сессии токена ещё нет."""
		frappe.set_user(self.ученик)
		вызовы = {
			"disconnect_agent": lambda: account.disconnect_agent(connection="0" * 16),
			"end_session": lambda: account.end_session(session="0" * 16, password=self.пароль),
			"end_other_sessions": lambda: account.end_other_sessions(password="не тот"),
		}
		адрес = getattr(frappe.local, "request_ip", None)
		self.addCleanup(setattr, frappe.local, "request_ip", адрес)
		self.addCleanup(frappe.session.data.pop, "impersonated_by", None)
		self.addCleanup(frappe.session.data.pop, "csrf_token", None)
		for метод, вызвать in вызовы.items():
			for что, (заголовки, свой_токен, от_имени) in {
				"токен агента": ({"Authorization": "Bearer x"}, "csrf", None),
				"вход от имени": ({"X-Frappe-CSRF-Token": "csrf"}, "csrf", "Administrator"),
				"без токена CSRF": ({}, "csrf", None),
				"токена у сессии нет": ({"X-Frappe-CSRF-Token": "csrf"}, None, None),
				"чужой токен CSRF": ({"X-Frappe-CSRF-Token": "чужой"}, "csrf", None),
			}.items():
				# Предел частоты считает вызовы по адресу и живёт в кэше час:
				# свой случайный адрес на случай — и на повторный прогон.
				frappe.local.request_ip = f"test-{frappe.generate_hash(length=8)}"
				frappe.session.data.csrf_token = свой_токен
				frappe.session.data.impersonated_by = от_имени
				with self.subTest(метод=метод, случай=что), запрос("/api/method/x", "POST", **заголовки):
					if "Authorization" in заголовки or от_имени:
						self.assertEqual(вызвать()["error"]["code"], account.ТОЛЬКО_САМ)
					else:
						self.assertRaises(frappe.CSRFTokenError, вызвать)

		with (
			self.subTest("со своим токеном CSRF — доходит до сервиса"),
			запрос("/api/method/x", "POST", **{"X-Frappe-CSRF-Token": "csrf"}),
		):
			frappe.session.data.csrf_token = "csrf"
			frappe.session.data.impersonated_by = None
			self.assertEqual(вызовы["disconnect_agent"]()["error"]["code"], ПОДКЛЮЧЕНИЕ_НЕ_НАЙДЕНО)

	def test_вызовы_с_паролем_ограничены_по_частоте(self):
		"""Предел — раньше всех проверок: подбор пароля упирается в него, даже
		если каждый вызов отклонён."""
		frappe.set_user(self.ученик)
		адрес = getattr(frappe.local, "request_ip", None)
		self.addCleanup(setattr, frappe.local, "request_ip", адрес)
		вызовы = {
			"end_session": lambda: account.end_session(session="0" * 16, password="x"),
			"end_other_sessions": lambda: account.end_other_sessions(password="x"),
		}
		for метод, вызвать in вызовы.items():
			frappe.local.request_ip = f"test-{frappe.generate_hash(length=8)}"
			with self.subTest(метод), запрос("/api/method/x", "POST"):
				for _ in range(account.ПОПЫТОК_ЗА_ЧАС):
					self.assertRaises(frappe.CSRFTokenError, вызвать)
				self.assertRaises(frappe.RateLimitExceededError, вызвать)
