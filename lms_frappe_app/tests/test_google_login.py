# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Вход через Google: настройка и проверка аккаунта.

`Why:` ключ провайдера — данные, а не код, и заводить его кликами значит
терять при пересоздании сайта. Здесь проверяется, что настройка приезжает из
конфигурации сайта, переживает повторные миграции и не требует секретов там,
где их нет. И что вход пускает только подтверждённый адрес и тот же аккаунт
Google (learning-services#666).
"""

from unittest.mock import patch

import frappe
from frappe.integrations import oauth2_logins
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import set_request
from frappe.utils.password import get_decrypted_password

from lms_frappe_app import google_login
from lms_frappe_app.google_login import (
	ДРУГОЙ_АККАУНТ,
	ДРУГОЙ_ПРОВАЙДЕР,
	НЕ_ПОДТВЕРЖДЁН,
	ПРОВАЙДЕРЫ_FRAPPE,
	СО_ВТОРЫМ_ФАКТОРОМ,
	причина_отказа,
)
from lms_frappe_app.install import (
	БЕЗ_2FA,
	КЛЮЧ_ID,
	КЛЮЧ_СЕКРЕТ,
	выключить_прочих_провайдеров,
	обеспечить_вход_через_google,
)
from lms_frappe_app.passwords import ВХОД_ЧЕРЕЗ_GOOGLE
from lms_frappe_app.tests.sample_data import создать_пользователя, создать_ученика

ЗАПИСЬ = ("Social Login Key", "google")


class IntegrationTestGoogleLogin(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(self._вернуть_конфигурацию)
		self.прежние = {ключ: frappe.conf.get(ключ) for ключ in (КЛЮЧ_ID, КЛЮЧ_СЕКРЕТ)}
		if frappe.db.exists(*ЗАПИСЬ):
			frappe.delete_doc(*ЗАПИСЬ, force=True, ignore_permissions=True)

	def _вернуть_конфигурацию(self):
		for ключ, значение in self.прежние.items():
			if значение is None:
				frappe.conf.pop(ключ, None)
			else:
				frappe.conf[ключ] = значение

	def _задать(self, client_id: str, client_secret: str):
		frappe.conf[КЛЮЧ_ID] = client_id
		frappe.conf[КЛЮЧ_СЕКРЕТ] = client_secret

	def test_без_ключей_ничего_не_создаётся(self):
		"""Локальная разработка не обязана держать секреты Google."""
		frappe.conf.pop(КЛЮЧ_ID, None)
		frappe.conf.pop(КЛЮЧ_СЕКРЕТ, None)

		обеспечить_вход_через_google()

		self.assertFalse(frappe.db.exists(*ЗАПИСЬ))

	def test_ключ_создаётся_из_конфигурации(self):
		self._задать("id-1.apps.googleusercontent.com", "secret-1")

		обеспечить_вход_через_google()

		ключ = frappe.get_doc(*ЗАПИСЬ)
		self.assertEqual(ключ.client_id, "id-1.apps.googleusercontent.com")
		self.assertTrue(ключ.enable_social_login)
		self.assertEqual(ключ.sign_ups, "Allow", "пускаем всех — решение куратора")
		# Адреса приходят от Frappe, а не из нашей копии.
		self.assertIn("accounts.google.com", ключ.authorize_url)
		self.assertEqual(
			ключ.redirect_url,
			"/api/method/frappe.integrations.oauth2_logins.login_via_google",
		)
		# `/login` рисует кнопку, только если секрет лежит в самой записи.
		self.assertEqual(
			get_decrypted_password(*ЗАПИСЬ, "client_secret"),
			"secret-1",
		)

	def test_повторный_вызов_не_плодит_версий(self):
		"""after_migrate зовётся на каждый старт контейнера."""
		self._задать("id-1", "secret-1")
		обеспечить_вход_через_google()
		было = frappe.db.get_value(*ЗАПИСЬ, "modified")

		обеспечить_вход_через_google()

		self.assertEqual(frappe.db.get_value(*ЗАПИСЬ, "modified"), было)

	def test_смена_секрета_подхватывается(self):
		self._задать("id-1", "secret-1")
		обеспечить_вход_через_google()

		self._задать("id-1", "secret-2")
		обеспечить_вход_через_google()

		self.assertEqual(get_decrypted_password(*ЗАПИСЬ, "client_secret"), "secret-2")

	def test_выключенный_ключ_включается_обратно(self):
		"""Иначе случайное выключение в админке переживёт все миграции."""
		self._задать("id-1", "secret-1")
		обеспечить_вход_через_google()
		frappe.db.set_value(*ЗАПИСЬ, "enable_social_login", 0)

		обеспечить_вход_через_google()

		self.assertTrue(frappe.db.get_value(*ЗАПИСЬ, "enable_social_login"))

	def test_прочие_провайдеры_выключаются(self):
		"""Включённым остаётся только ключ Google (learning-services#691)."""
		# Мимо проверок документа: включённому ключу они требуют адресов провайдера.
		ключ = frappe.get_doc(
			{
				"doctype": "Social Login Key",
				"name": f"other-{frappe.generate_hash(length=6)}",
				"social_login_provider": "Custom",
				"provider_name": "Other",
				"enable_social_login": 1,
			}
		)
		ключ.db_insert()
		self._задать("id-1", "secret-1")
		обеспечить_вход_через_google()

		выключить_прочих_провайдеров()

		self.assertEqual(frappe.db.get_value("Social Login Key", ключ.name, "enable_social_login"), 0)
		self.assertEqual(frappe.db.get_value(*ЗАПИСЬ, "enable_social_login"), 1)


class UnitTestGoogleAccount(UnitTestCase):
	def test_причина_отказа(self):
		"""Ответ `oauth2/v2/userinfo`, которым пользуется Frappe, — `verified_email`
		и `id`; OpenID Connect — `email_verified` и `sub`. Подтверждение — только
		булево `True`."""
		v2 = {"email": "a@gmail.com", "verified_email": True, "id": "111"}
		случаи = {
			"v2, первый вход": (v2, None, None),
			"v2, тот же аккаунт": (v2, "111", None),
			"OIDC, тот же аккаунт": ({"email_verified": True, "sub": "111"}, "111", None),
			"число и строка — один идентификатор": ({**v2, "id": 111}, "111", None),
			"адрес не подтверждён": ({**v2, "verified_email": False}, None, НЕ_ПОДТВЕРЖДЁН),
			"флага нет": ({"email": "a@gmail.com", "id": "111"}, None, НЕ_ПОДТВЕРЖДЁН),
			"флаг строкой": ({**v2, "verified_email": "true"}, None, НЕ_ПОДТВЕРЖДЁН),
			"другой аккаунт Google": ({**v2, "id": "222"}, "111", ДРУГОЙ_АККАУНТ),
		}
		for что, (данные, привязанный, причина) in случаи.items():
			with self.subTest(что):
				self.assertEqual(причина_отказа(данные, привязанный), причина)


class IntegrationTestGoogleAccount(IntegrationTestCase):
	def test_вход_сверяет_аккаунт_привязки(self):
		"""Адрес, уже привязанный к аккаунту Google, впускает только этот
		аккаунт; отказ — страницей, без входа (learning-services#666)."""
		подмены = frappe.get_hooks("override_whitelisted_methods")
		self.assertEqual(
			подмены["frappe.integrations.oauth2_logins.login_via_google"][-1],
			"lms_frappe_app.google_login.login_via_google",
		)
		пользователь = создать_ученика(f"google-{frappe.generate_hash(length=8)}@example.com")
		документ = frappe.get_doc("User", пользователь)
		документ.set_social_login_userid("google", userid="111")
		документ.save(ignore_permissions=True)

		флаги = []
		for что, идентификатор, впущен in (("тот же", "111", True), ("другой", "222", False)):
			данные = {"email": пользователь.upper(), "verified_email": True, "id": идентификатор}
			with (
				self.subTest(что),
				patch.object(google_login, "get_info_via_oauth", return_value=данные),
				patch.object(
					google_login,
					"login_oauth_user",
					side_effect=lambda *_, **__: флаги.append(frappe.flags.get(ВХОД_ЧЕРЕЗ_GOOGLE)),
				) as вход,
				patch.object(google_login, "consume_oauth_state"),
				patch("frappe.respond_as_web_page") as страница,
			):
				google_login.login_via_google(code="код", state="состояние")

				self.assertEqual(вход.called, впущен)
				self.assertEqual(страница.called, not впущен)
				if not впущен:
					self.assertEqual(страница.call_args.args[1], ДРУГОЙ_АККАУНТ)
		# Вход Frappe идёт под флагом: случайный пароль нового аккаунта не проверяется.
		self.assertEqual(флаги, [True])
		self.assertIsNone(frappe.flags.get(ВХОД_ЧЕРЕЗ_GOOGLE))

	def test_роли_со_вторым_фактором_входят_паролем(self):
		"""Вход через Google кода второго фактора не спрашивает, поэтому ролям
		со вторым фактором он закрыт (learning-services#691)."""
		ученик = создать_ученика(f"google-{frappe.generate_hash(length=8)}@example.com")
		админ = создать_пользователя(
			f"google-sm-{frappe.generate_hash(length=8)}@example.com", "System Manager"
		)
		for что, пользователь, впущен, без_2fa in (
			("ученик", ученик, True, 0),
			("System Manager", админ, False, 0),
			("System Manager, второй фактор выключен на сайте", админ, True, 1),
		):
			данные = {"email": пользователь, "verified_email": True, "id": "333"}
			with (
				self.subTest(что),
				patch.dict(frappe.conf, {БЕЗ_2FA: без_2fa}),
				patch.object(google_login, "get_info_via_oauth", return_value=данные),
				patch.object(google_login, "login_oauth_user") as вход,
				patch.object(google_login, "consume_oauth_state") as гашение,
				patch("frappe.respond_as_web_page") as страница,
			):
				google_login.login_via_google(code="код", state="состояние")

				self.assertEqual(вход.called, впущен)
				self.assertEqual(гашение.called, not впущен)
				if not впущен:
					self.assertEqual(страница.call_args.args[1], СО_ВТОРЫМ_ФАКТОРОМ)

	def test_другие_провайдеры_отклоняются(self):
		"""Вход — только Google: у прочих провайдеров Frappe подтверждения
		адреса не проверяет (learning-services#691). Подменены оба имени
		каждого метода входа."""
		подмены = frappe.get_hooks("override_whitelisted_methods")
		for провайдер in ПРОВАЙДЕРЫ_FRAPPE:
			for модуль in ("frappe.integrations.oauth2_logins", "frappe.www.login"):
				with self.subTest(f"{модуль}.login_via_{провайдер}"):
					self.assertEqual(
						подмены[f"{модуль}.login_via_{провайдер}"][-1],
						"lms_frappe_app.google_login.отклонить_провайдера",
					)
		with patch("frappe.respond_as_web_page") as страница:
			google_login.отклонить_провайдера(code="код", state="состояние")
		self.assertEqual(страница.call_args.args[1], ДРУГОЙ_ПРОВАЙДЕР)
		self.assertEqual(страница.call_args.kwargs["http_status_code"], 403)

	def test_custom_google_идёт_через_проверку(self):
		"""`custom/google` вёл во вход Frappe мимо подмены `login_via_google`
		(learning-services#666). Имя провайдера — без регистра и пробелов, как
		его находит MariaDB; прочие провайдеры отклоняются
		(learning-services#691)."""
		подмены = frappe.get_hooks("override_whitelisted_methods")
		for метод, наш in (
			("frappe.www.login.login_via_google", "login_via_google"),
			("frappe.integrations.oauth2_logins.custom", "custom"),
		):
			with self.subTest(метод):
				self.assertEqual(подмены[метод][-1], f"lms_frappe_app.google_login.{наш}")

		прежний = getattr(frappe.local, "request", None)
		self.addCleanup(setattr, frappe.local, "request", прежний)
		for провайдер, наш in (("google", True), ("Google%20", True), ("github", False), ("", False)):
			set_request(path=f"/api/method/frappe.integrations.oauth2_logins.custom/{провайдер}")
			with (
				self.subTest(провайдер),
				patch.object(google_login, "login_via_google") as проверка,
				patch.object(google_login, "отклонить_провайдера") as отказ,
				patch.object(oauth2_logins, "custom") as frappe_custom,
			):
				google_login.custom(code="код", state="состояние")

				self.assertEqual(проверка.called, наш)
				self.assertEqual(отказ.called, not наш)
				frappe_custom.assert_not_called()
