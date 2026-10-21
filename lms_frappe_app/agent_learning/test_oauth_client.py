# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Выход из Cenda отзывает токены веб-чата (learning-services#656)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from lms_frappe_app.agent_learning.oauth_client import отозвать_чат_при_выходе
from lms_frappe_app.install import КЛЮЧ_ЧАТА_ID
from lms_frappe_app.tests.sample_data import создать_ученика


class IntegrationTestВыходЗакрываетЧат(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def клиент(self, адрес: str) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "OAuth Client",
					"app_name": адрес,
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

	def токен(self, клиент: str, пользователь: str) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "OAuth Bearer Token",
					"client": клиент,
					"user": пользователь,
					"scopes": "all",
					"access_token": frappe.generate_hash(length=30),
					"refresh_token": frappe.generate_hash(length=30),
					"expires_in": 3600,
					"expiration_time": add_to_date(now_datetime(), hours=1),
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def статус(self, токен: str) -> str:
		return frappe.db.get_value("OAuth Bearer Token", токен, "status")

	def test_выход_отзывает_только_чат_выходящего(self):
		суффикс = frappe.generate_hash(length=6)
		ученик = создать_ученика(f"logout-{суффикс}@example.com")
		сосед = создать_ученика(f"logout-other-{суффикс}@example.com")
		чат = self.клиент("https://cenda.example/chat/callback")
		агент = self.клиент("https://claude.ai/api/mcp/auth_callback")
		свой_чат = self.токен(чат, ученик)
		свой_агент = self.токен(агент, ученик)
		чужой_чат = self.токен(чат, сосед)

		frappe.set_user(ученик)
		with patch.dict(frappe.conf, {КЛЮЧ_ЧАТА_ID: чат}):
			отозвать_чат_при_выходе()

		self.assertEqual(self.статус(свой_чат), "Revoked")
		# Агент, подключённый самим учеником, работает и без браузера.
		self.assertEqual(self.статус(свой_агент), "Active")
		self.assertEqual(self.статус(чужой_чат), "Active")

	def test_без_клиента_чата_ничего_не_трогает(self):
		суффикс = frappe.generate_hash(length=6)
		ученик = создать_ученика(f"logout-noconf-{суффикс}@example.com")
		чат = self.клиент("https://cenda.example/chat/callback")
		токен = self.токен(чат, ученик)

		frappe.set_user(ученик)
		with patch.dict(frappe.conf, {КЛЮЧ_ЧАТА_ID: None}):
			отозвать_чат_при_выходе()
		self.assertEqual(self.статус(токен), "Active")

	def test_хук_выхода_подключён(self):
		self.assertIn(
			"lms_frappe_app.agent_learning.oauth_client.отозвать_чат_при_выходе",
			frappe.get_hooks("on_logout"),
		)
