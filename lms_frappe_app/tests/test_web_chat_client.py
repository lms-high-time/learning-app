# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""OAuth-клиент веб-чата из конфигурации сайта (lms-platform#142).

`Why:` клиент — данные, а не код: заведённый кликами, он теряется при
пересоздании сайта, а оболочки контейнера на стенде нет. Здесь проверяется,
что клиент приезжает из конфигурации, переживает повторные миграции и не
появляется там, где ключей нет.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.install import (
	КЛЮЧ_ЧАТА_ID,
	КЛЮЧ_ЧАТА_ВОЗВРАТ,
	КЛЮЧ_ЧАТА_СЕКРЕТ,
	обеспечить_клиента_веб_чата,
)

ID = "webchatclient01"
СЕКРЕТ = "chat-secret-1"
ВОЗВРАТ = "https://lms.example.com/chat/callback"


class IntegrationTestWebChatClient(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(self._вернуть_конфигурацию)
		self.прежние = {
			ключ: frappe.conf.get(ключ) for ключ in (КЛЮЧ_ЧАТА_ID, КЛЮЧ_ЧАТА_СЕКРЕТ, КЛЮЧ_ЧАТА_ВОЗВРАТ)
		}
		if frappe.db.exists("OAuth Client", ID):
			frappe.delete_doc("OAuth Client", ID, force=True, ignore_permissions=True)

	def _вернуть_конфигурацию(self):
		for ключ, значение in self.прежние.items():
			if значение is None:
				frappe.conf.pop(ключ, None)
			else:
				frappe.conf[ключ] = значение

	def _задать(self, client_id=ID, secret=СЕКРЕТ, redirect=ВОЗВРАТ):
		for ключ, значение in ((КЛЮЧ_ЧАТА_ID, client_id), (КЛЮЧ_ЧАТА_СЕКРЕТ, secret), (КЛЮЧ_ЧАТА_ВОЗВРАТ, redirect)):
			if значение is None:
				frappe.conf.pop(ключ, None)
			else:
				frappe.conf[ключ] = значение

	def test_ключи_наполовину_клиент_не_создаётся(self):
		# Клиент без секрета или адреса возврата — вход, который молча не работает.
		self._задать(secret=None)

		обеспечить_клиента_веб_чата()

		self.assertFalse(frappe.db.exists("OAuth Client", ID))

	def test_клиент_создаётся_из_конфигурации(self):
		self._задать()

		обеспечить_клиента_веб_чата()

		клиент = frappe.get_doc("OAuth Client", ID)
		# id совпадает с тем, что знает MCP-сервис: Frappe пишет client_id из имени.
		self.assertEqual(клиент.client_id, ID)
		self.assertEqual(клиент.client_secret, СЕКРЕТ)
		self.assertEqual(клиент.redirect_uris, ВОЗВРАТ)
		self.assertEqual(клиент.default_redirect_uri, ВОЗВРАТ)
		# Без экрана согласия: ученик в чат попадает переадресациями.
		self.assertTrue(клиент.skip_authorization)
		# Секрет в теле: заголовок Basic Frappe принимает за вход пользователя.
		self.assertEqual(клиент.token_endpoint_auth_method, "Client Secret Post")
		self.assertEqual((клиент.grant_type, клиент.response_type), ("Authorization Code", "Code"))
		self.assertIn("LMS Student", {р.role for р in клиент.allowed_roles})

	def test_повторный_вызов_не_плодит_версий(self):
		"""after_migrate зовётся на каждый старт контейнера."""
		self._задать()
		обеспечить_клиента_веб_чата()
		было = frappe.db.get_value("OAuth Client", ID, "modified")

		обеспечить_клиента_веб_чата()

		self.assertEqual(frappe.db.get_value("OAuth Client", ID, "modified"), было)

	def test_смена_секрета_и_адреса_подхватывается(self):
		self._задать()
		обеспечить_клиента_веб_чата()

		self._задать(secret="chat-secret-2", redirect="https://lms2.example.com/chat/callback")
		обеспечить_клиента_веб_чата()

		клиент = frappe.get_doc("OAuth Client", ID)
		self.assertEqual(клиент.client_secret, "chat-secret-2")
		self.assertEqual(клиент.redirect_uris, "https://lms2.example.com/chat/callback")

	def test_ручная_правка_флагов_возвращается(self):
		# Сняли Skip Authorization в desk — у ученика снова экран согласия.
		self._задать()
		обеспечить_клиента_веб_чата()
		frappe.db.set_value("OAuth Client", ID, "skip_authorization", 0)

		обеспечить_клиента_веб_чата()

		self.assertTrue(frappe.db.get_value("OAuth Client", ID, "skip_authorization"))
