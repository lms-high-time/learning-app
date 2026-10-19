# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_to_date, now_datetime

from lms_frappe_app.agent_learning.oauth_client import ВЕБ_ЧАТ, ДРУГОЙ_АГЕНТ, агент_клиента
from lms_frappe_app.agent_learning.report.agent_connections import agent_connections
from lms_frappe_app.install import КЛЮЧ_ЧАТА_ID
from lms_frappe_app.tests.sample_data import (
	зачислить,
	курс_из_релиза,
	создать_занятие,
	создать_ученика,
	урок_релиза,
)

CLAUDE = "https://claude.ai/api/mcp/auth_callback"
CHATGPT = "https://chatgpt.com/connector/oauth/abc"


class TestАгентКлиента(UnitTestCase):
	def test_агент_по_адресу_возврата(self):
		"""Адреса — как у клиентов на стенде (learning-services#586)."""
		for клиент, адреса, ожидается in (
			("c1", CLAUDE, "Claude"),
			("c2", CHATGPT, "ChatGPT"),
			("c3", f"https://example.com/cb\n{CLAUDE}", "Claude"),
			("c4", "https://www.chatgpt.com/cb", "ChatGPT"),
			("c5", "https://claude.ai.example.com/cb", ДРУГОЙ_АГЕНТ),
			("c6", "http://localhost/cb", ДРУГОЙ_АГЕНТ),
			("c7", None, ДРУГОЙ_АГЕНТ),
			("чат", "https://cenda.app/chat/callback", ВЕБ_ЧАТ),
		):
			with self.subTest(адреса=адреса):
				self.assertEqual(агент_клиента(клиент, адреса, "чат"), ожидается)

	def test_без_клиента_чата_в_конфигурации(self):
		self.assertEqual(агент_клиента("чат", "https://cenda.app/chat/callback", None), ДРУГОЙ_АГЕНТ)


class IntegrationTestAgentConnections(IntegrationTestCase):
	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"connections-{суффикс}@example.com")
		self.курс, _ = курс_из_релиза()
		self.уроки = [урок_релиза(self.курс, ключ) for ключ in ("l-1", "l-2")]
		зачислить(self.ученик, self.уроки[0])
		self.сейчас = now_datetime()

	def tearDown(self):
		frappe.set_user("Administrator")

	def назад(self, часов: int):
		return add_to_date(self.сейчас, hours=-часов)

	def клиент(self, адрес: str, app_name: str, зарегистрирован: int) -> str:
		имя = (
			frappe.get_doc(
				{
					"doctype": "OAuth Client",
					"app_name": app_name,
					"redirect_uris": адрес,
					"default_redirect_uri": адрес,
					"grant_type": "Authorization Code",
					"response_type": "Code",
					"scopes": "all",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.set_value("OAuth Client", имя, "creation", self.назад(зарегистрирован))
		return имя

	def токен(self, клиент: str, выдан: int, истекает: int, статус: str = "Active") -> None:
		"""Токен, выданный `выдан` часов назад и истекающий через `истекает` часов
		от текущего момента (отрицательное — уже истёк). Метки явные: отчёт
		сортирует по ним, а часы контейнера тестов отступают назад
		(learning-services#509)."""
		имя = (
			frappe.get_doc(
				{
					"doctype": "OAuth Bearer Token",
					"client": клиент,
					"user": self.ученик,
					"scopes": "all",
					"access_token": frappe.generate_hash(length=30),
					"refresh_token": frappe.generate_hash(length=30),
					"expires_in": 3600,
					"expiration_time": self.назад(-истекает),
					"status": статус,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.set_value("OAuth Bearer Token", имя, "creation", self.назад(выдан))

	def строки(self, клиент_чата: str | None = None) -> list[tuple]:
		with patch.dict(frappe.conf, {КЛЮЧ_ЧАТА_ID: клиент_чата}):
			колонки, строки, сообщение = agent_connections.execute()
		self.assertEqual([к["fieldname"] for к in колонки][0], "user")
		self.assertTrue(сообщение)
		return [
			(
				с["agent"],
				с["app_name"],
				с["connections"],
				с["first_connected"],
				с["last_activity"],
				с["live"],
				с["lessons"],
			)
			for с in строки
			if с["user"] == self.ученик
		]

	def test_подключения_свёрнуты_до_агента(self):
		"""Переподключения одного агента — одна строка; незнакомые агенты не
		сворачиваются друг с другом; клиент без токена строки не даёт."""
		первый = self.клиент(CLAUDE, "Claude", зарегистрирован=50)
		второй = self.клиент(CLAUDE, "Claude", зарегистрирован=30)
		self.клиент(CLAUDE, "Claude", зарегистрирован=20)
		chatgpt = self.клиент(CHATGPT, "ChatGPT", зарегистрирован=40)
		свой = self.клиент("https://agent.example.com/cb", "Свой агент", зарегистрирован=10)
		чужой = self.клиент("https://other.example.com/cb", "Чужой агент", зарегистрирован=9)
		чат = self.клиент("https://cenda.app/chat/callback", "Cenda Web Chat", зарегистрирован=8)
		self.токен(первый, выдан=49, истекает=-48)
		self.токен(второй, выдан=2, истекает=1)
		self.токен(второй, выдан=29, истекает=-28)
		self.токен(chatgpt, выдан=5, истекает=-4)
		self.токен(свой, выдан=3, истекает=1, статус="Revoked")
		self.токен(чужой, выдан=4, истекает=-3)
		self.токен(чат, выдан=1, истекает=1)
		# Через агентов — два разных урока, один из них дважды; в веб-чате — один.
		for урок in (*self.уроки, self.уроки[0]):
			создать_занятие(self.ученик, урок)
		в_чате = создать_занятие(self.ученик, self.уроки[1])
		frappe.db.set_value("Agent Learning Session", в_чате, "web_chat", 1)

		self.assertEqual(
			self.строки(клиент_чата=чат),
			[
				(ВЕБ_ЧАТ, "Cenda Web Chat", 1, self.назад(8), self.назад(1), 1, 1),
				("Claude", "Claude", 2, self.назад(50), self.назад(2), 1, 2),
				(ДРУГОЙ_АГЕНТ, "Свой агент", 1, self.назад(10), self.назад(3), 0, 2),
				(ДРУГОЙ_АГЕНТ, "Чужой агент", 1, self.назад(9), self.назад(4), 0, 2),
				("ChatGPT", "ChatGPT", 1, self.назад(40), self.назад(5), 0, 2),
			],
		)

	def test_отчёт_только_администратору(self):
		frappe.set_user(self.ученик)

		with self.assertRaises(frappe.PermissionError):
			agent_connections.execute()
