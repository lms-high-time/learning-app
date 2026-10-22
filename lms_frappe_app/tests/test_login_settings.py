# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Настройки входа и сайта (learning-services#666, #691), файлов (#683) и часовой пояс (#681) из кода."""

from unittest.mock import patch

import frappe
from frappe.core.doctype.system_settings.system_settings import clear_system_settings_cache
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.install import (
	БЕЗ_2FA,
	НАСТРОЙКИ_OAUTH,
	НАСТРОЙКИ_ВХОДА,
	НАСТРОЙКИ_ФАЙЛОВ,
	НАСТРОЙКИ_СИСТЕМЫ,
	РОЛИ_С_2FA,
	САЙТ_РАЗРАБОТКИ,
	ФЛАГИ_РАЗРАБОТКИ,
	обеспечить_настройки_oauth,
	обеспечить_настройки_входа,
	проверить_флаги_разработки,
)

НАСТРОЙКИ = "System Settings"


class IntegrationTestLoginSettings(IntegrationTestCase):
	def setUp(self):
		# Значения возвращаются вручную: откат класса не сбросит кэш настроек,
		# и соседние тесты читали бы из него включённую 2FA.
		было = frappe.db.get_singles_dict(НАСТРОЙКИ, cast=True)
		прежние = {поле: было.get(поле) for поле in {**НАСТРОЙКИ_ВХОДА, **НАСТРОЙКИ_ФАЙЛОВ, **НАСТРОЙКИ_СИСТЕМЫ}}
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
			{
				"login_with_email_link": 1,
				"allow_login_after_fail": 60,
				"enable_two_factor_auth": 0,
				"allowed_file_extensions": "",
				"time_zone": "",
			},
		)
		frappe.db.set_value("Role", "System Manager", "two_factor_auth", 0)
		frappe.db.set_value("Role", "All", "two_factor_auth", 1)
		frappe.local.system_settings = None

		обеспечить_настройки_входа()

		for поле, значение in {**НАСТРОЙКИ_ВХОДА, **НАСТРОЙКИ_ФАЙЛОВ, **НАСТРОЙКИ_СИСТЕМЫ}.items():
			with self.subTest(поле):
				self.assertEqual(frappe.get_system_settings(поле), значение)
		self.assertEqual(frappe.utils.get_system_timezone(), НАСТРОЙКИ_СИСТЕМЫ["time_zone"])
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


class IntegrationTestOAuthSettings(IntegrationTestCase):
	def setUp(self):
		прежние = {
			запись: {поле: frappe.db.get_single_value(запись, поле) for поле in поля}
			for запись, поля in НАСТРОЙКИ_OAUTH.items()
		}
		self.addCleanup(self._вернуть, прежние)

	def _вернуть(self, прежние: dict):
		for запись, поля in прежние.items():
			frappe.db.set_single_value(запись, поля)
			frappe.clear_document_cache(запись, запись)

	def test_настройки_oauth_приводятся_и_не_пишутся_зря(self):
		"""Экран согласия, метаданные и регистрация клиентов — из кода; ручная
		правка возвращается миграцией (learning-services#691)."""
		frappe.db.set_single_value(
			"OAuth Settings", {"skip_authorization": 1, "show_auth_server_metadata": 0}
		)
		frappe.db.set_single_value("OAuth Provider Settings", "skip_authorization", "Auto")
		frappe.clear_document_cache("OAuth Settings", "OAuth Settings")

		обеспечить_настройки_oauth()

		for запись, поля in НАСТРОЙКИ_OAUTH.items():
			for поле, значение in поля.items():
				with self.subTest(f"{запись}.{поле}"):
					self.assertEqual(frappe.get_cached_value(запись, запись, поле), значение)
		записано = frappe.db.get_singles_dict("OAuth Settings")["modified"]
		обеспечить_настройки_oauth()
		self.assertEqual(frappe.db.get_singles_dict("OAuth Settings")["modified"], записано)


class UnitTestDevelopmentFlags(UnitTestCase):
	def test_флаги_проверяются_до_миграции(self):
		self.assertIn("lms_frappe_app.install.проверить_флаги_разработки", frappe.get_hooks("before_migrate"))

	def test_флаги_разработки_останавливают_миграцию(self):
		"""Режим разработчика, тесты на сайте и серверные скрипты на стенде —
		отказ миграции; сайт разработки ключом конфигурации их держит
		(learning-services#691)."""
		выключены = dict.fromkeys(ФЛАГИ_РАЗРАБОТКИ, 0)
		for что, конфигурация, отказ in (
			("флагов нет", {}, False),
			*((флаг, {флаг: 1}, True) for флаг in ФЛАГИ_РАЗРАБОТКИ),
			("флаг строкой", {"developer_mode": "1"}, True),
			("сайт разработки", {"allow_tests": True, САЙТ_РАЗРАБОТКИ: 1}, False),
		):
			with (
				self.subTest(что),
				patch.dict(frappe.conf, {**выключены, САЙТ_РАЗРАБОТКИ: 0, **конфигурация}),
			):
				if отказ:
					with self.assertRaises(frappe.ValidationError):
						проверить_флаги_разработки()
				else:
					проверить_флаги_разработки()
