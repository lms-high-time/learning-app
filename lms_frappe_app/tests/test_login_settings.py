# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Настройки входа из кода (learning-services#666)."""

from unittest.mock import patch

import frappe
from frappe.core.doctype.system_settings.system_settings import clear_system_settings_cache
from frappe.tests import IntegrationTestCase

from lms_frappe_app.install import БЕЗ_2FA, НАСТРОЙКИ_ВХОДА, РОЛИ_С_2FA, обеспечить_настройки_входа

НАСТРОЙКИ = "System Settings"


class IntegrationTestLoginSettings(IntegrationTestCase):
	def setUp(self):
		# Значения возвращаются вручную: откат класса не сбросит кэш настроек,
		# и соседние тесты читали бы из него включённую 2FA.
		было = frappe.db.get_singles_dict(НАСТРОЙКИ, cast=True)
		прежние = {поле: было.get(поле) for поле in НАСТРОЙКИ_ВХОДА}
		роли = {роль: frappe.db.get_value("Role", роль, "two_factor_auth") for роль in (*РОЛИ_С_2FA, "All")}
		self.addCleanup(self._вернуть, прежние, роли)

	def _вернуть(self, прежние: dict, роли: dict):
		frappe.db.set_single_value(НАСТРОЙКИ, прежние)
		for поле, значение in прежние.items():
			frappe.db.set_default(поле, значение)
		for роль, значение in роли.items():
			frappe.db.set_value("Role", роль, "two_factor_auth", значение)
		frappe.clear_document_cache(НАСТРОЙКИ, НАСТРОЙКИ)
		clear_system_settings_cache()
		frappe.local.system_settings = None

	def test_настройки_приводятся_и_не_пишутся_зря(self):
		"""Ручная правка возвращается миграцией; совпадающие значения не пишутся:
		`after_migrate` зовётся на каждый старт контейнера. Второй фактор —
		только ролям из списка, у роли `All` (все пользователи) он снимается."""
		frappe.db.set_single_value(
			НАСТРОЙКИ,
			{"login_with_email_link": 1, "allow_login_after_fail": 60, "enable_two_factor_auth": 0},
		)
		frappe.db.set_value("Role", "System Manager", "two_factor_auth", 0)
		frappe.db.set_value("Role", "All", "two_factor_auth", 1)
		frappe.local.system_settings = None

		обеспечить_настройки_входа()

		for поле, значение in НАСТРОЙКИ_ВХОДА.items():
			with self.subTest(поле):
				self.assertEqual(frappe.get_system_settings(поле), значение)
		self.assertEqual(frappe.db.get_default("enable_two_factor_auth"), "1")
		self.assertEqual(frappe.db.get_value("Role", "System Manager", "two_factor_auth"), 1)
		self.assertEqual(frappe.db.get_value("Role", "All", "two_factor_auth"), 0)

		записано = frappe.db.get_singles_dict(НАСТРОЙКИ)["modified"]
		роль = frappe.db.get_value("Role", "System Manager", "modified")
		обеспечить_настройки_входа()
		self.assertEqual(frappe.db.get_singles_dict(НАСТРОЙКИ)["modified"], записано)
		self.assertEqual(frappe.db.get_value("Role", "System Manager", "modified"), роль)

	def test_без_2fa_для_локальной_разработки(self):
		"""Ключ конфигурации выключает второй фактор и не ставит его ролям:
		письмо с QR-кодом Administrator локального сайта не получит."""
		frappe.db.set_single_value(НАСТРОЙКИ, "enable_two_factor_auth", 1)
		frappe.db.set_value("Role", "System Manager", "two_factor_auth", 0)
		frappe.local.system_settings = None

		with patch.dict(frappe.conf, {БЕЗ_2FA: 1}):
			обеспечить_настройки_входа()

		self.assertEqual(frappe.get_system_settings("enable_two_factor_auth"), 0)
		self.assertEqual(frappe.get_system_settings("allow_error_traceback"), 0)
		self.assertEqual(frappe.db.get_value("Role", "System Manager", "two_factor_auth"), 0)
