# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Хеш пароля — argon2id, прежний pbkdf2 перехешируется при входе
(learning-services#676)."""

from unittest.mock import patch

import frappe
import frappe.utils.password
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import set_request
from passlib.context import CryptContext

from lms_frappe_app import password_hash
from lms_frappe_app.password_hash import ARGON2_PREFIX, PBKDF2_ROUNDS, настройки
from lms_frappe_app.tests.sample_data import создать_ученика

PASSWORD = "корова-забор-синий-трамвай"
#: Хеш Frappe по умолчанию — тот, что лежит у пользователей до перехода.
PBKDF2_FRAPPE = CryptContext(["pbkdf2_sha256"]).hash(PASSWORD)


class UnitTestPasswordHashSettings(UnitTestCase):
	def test_argon2id_по_умолчанию(self):
		"""Новый хеш — argon2id с параметрами модуля; хеш Frappe проверяется
		и помечен к перехешу, свой — нет."""
		контекст = CryptContext(**настройки(есть_argon2=True))
		hashed_password = контекст.hash(PASSWORD)
		self.assertTrue(hashed_password.startswith(ARGON2_PREFIX), hashed_password)
		self.assertTrue(контекст.verify(PASSWORD, hashed_password))
		self.assertFalse(контекст.needs_update(hashed_password))
		self.assertTrue(контекст.verify(PASSWORD, PBKDF2_FRAPPE))
		self.assertTrue(контекст.needs_update(PBKDF2_FRAPPE))

	def test_без_argon2_pbkdf2_с_раундами(self):
		"""Без библиотеки argon2 — pbkdf2 с числом раундов не ниже порога, и
		хеш Frappe с меньшим числом помечен к перехешу."""
		контекст = CryptContext(**настройки(есть_argon2=False))
		hashed_password = контекст.hash(PASSWORD)
		self.assertTrue(hashed_password.startswith(f"$pbkdf2-sha256${PBKDF2_ROUNDS}$"), hashed_password)
		self.assertFalse(контекст.needs_update(hashed_password))
		self.assertTrue(контекст.verify(PASSWORD, PBKDF2_FRAPPE))
		self.assertTrue(контекст.needs_update(PBKDF2_FRAPPE))

	def test_сбой_проверки_argon2_как_её_отсутствие(self):
		"""Исключение из проверки backend не роняет запрос: работает pbkdf2."""
		with (
			patch.object(password_hash.argon2, "has_backend", side_effect=AttributeError("__version__")),
			patch.object(frappe, "log_error") as журнал,
		):
			self.assertFalse(password_hash._есть_argon2())
		журнал.assert_called_once()

	def test_хуки(self):
		"""Контекст настраивается до проверки пароля при входе, до смены пароля в
		веб-запросе и до фоновой задачи."""
		путь = "lms_frappe_app.password_hash.настроить"
		for хук in ("before_login", "before_request", "before_job"):
			with self.subTest(хук):
				self.assertIn(путь, frappe.get_hooks(хук))


class IntegrationTestPasswordRehash(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		контекст = frappe.utils.password.passlibctx
		self.прежний = контекст.to_dict()
		self.addCleanup(контекст.load, self.прежний)
		self.addCleanup(setattr, password_hash, "_настроен", False)
		for имя in ("request", "request_ip", "form_dict", "cookie_manager", "login_manager"):
			self.addCleanup(setattr, frappe.local, имя, getattr(frappe.local, имя, None))
		self.addCleanup(frappe.set_user, "Administrator")
		# Свежий воркер: контекст Frappe по умолчанию, хуки ещё не звались.
		контекст.load(CryptContext(["pbkdf2_sha256", "argon2"]).to_dict())
		password_hash._настроен = False

	def test_вход_с_прежним_хешем_перехеширует(self):
		"""Первый же вход воркера — путём входа Frappe (`LoginManager`), где
		пароль проверяется раньше `before_request`, — принимает пароль,
		сохранённый до перехода, и пишет argon2id; новый пароль — тоже argon2id."""
		from frappe.auth import CookieManager, LoginManager

		ученик = self._ученик_с_прежним_хешем()
		set_request(method="POST", path="/api/method/login")
		# Адрес клиента Frappe ставит при разборе запроса; счётчик входов — по нему.
		frappe.local.request_ip = "127.0.0.1"
		frappe.local.form_dict = frappe._dict(cmd="login", usr=ученик, pwd=PASSWORD)
		frappe.local.cookie_manager = CookieManager()
		frappe.local.login_manager = LoginManager()

		self.assertEqual(frappe.session.user, ученик)
		self.assertTrue(self._хеш(ученик).startswith(ARGON2_PREFIX))
		frappe.db.delete("Sessions", {"user": ученик})

		frappe.utils.password.update_password(ученик, PASSWORD + "-2")
		self.assertTrue(self._хеш(ученик).startswith(ARGON2_PREFIX))
		self.assertEqual(frappe.utils.password.check_password(ученик, PASSWORD + "-2"), ученик)

	def test_неверный_пароль_хеш_не_меняет(self):
		ученик = self._ученик_с_прежним_хешем()
		password_hash.настроить()
		with self.assertRaises(frappe.AuthenticationError):
			frappe.utils.password.check_password(ученик, PASSWORD + "-неверный")
		self.assertEqual(self._хеш(ученик), PBKDF2_FRAPPE)

	def test_без_argon2_вход_пишет_pbkdf2_с_порогом(self):
		ученик = self._ученик_с_прежним_хешем()
		with patch.object(password_hash.argon2, "has_backend", return_value=False):
			password_hash.настроить()
		self.assertEqual(frappe.utils.password.check_password(ученик, PASSWORD), ученик)
		self.assertTrue(self._хеш(ученик).startswith(f"$pbkdf2-sha256${PBKDF2_ROUNDS}$"))

	def _ученик_с_прежним_хешем(self) -> str:
		ученик = создать_ученика(f"password-hash-{frappe.generate_hash(length=6)}@example.com")
		frappe.db.sql(
			"""insert into `__Auth` (doctype, name, fieldname, password, encrypted)
			values ('User', %s, 'password', %s, 0)
			on duplicate key update password = values(password)""",
			(ученик, PBKDF2_FRAPPE),
		)
		return ученик

	@staticmethod
	def _хеш(пользователь: str) -> str:
		return frappe.db.sql(
			"select password from `__Auth` where doctype = 'User' and name = %s and fieldname = 'password'",
			пользователь,
		)[0][0]
