# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Хеш пароля — argon2id, прежний pbkdf2 перехешируется при входе
(learning-services#676)."""

import frappe
import frappe.utils.password
from frappe.tests import IntegrationTestCase, UnitTestCase
from passlib.context import CryptContext

from lms_frappe_app import password_hash
from lms_frappe_app.password_hash import ARGON2_PREFIX, PBKDF2_ROUNDS, настроить, настройки
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

	def test_хуки(self):
		"""Контекст настраивается до входа в веб-запросе и до фоновой задачи."""
		путь = "lms_frappe_app.password_hash.настроить"
		self.assertIn(путь, frappe.get_hooks("before_request"))
		self.assertIn(путь, frappe.get_hooks("before_job"))


class IntegrationTestPasswordRehash(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		контекст = frappe.utils.password.passlibctx
		self.прежний = контекст.to_dict()
		self.addCleanup(контекст.load, self.прежний)
		self.addCleanup(setattr, password_hash, "_настроен", False)

	def test_вход_с_прежним_хешем_перехеширует(self):
		"""Пароль, сохранённый до перехода, принимается, и после входа в базе
		лежит argon2id; новый пароль сразу пишется argon2id."""
		ученик = создать_ученика("password-hash@example.com")
		frappe.db.sql(
			"""insert into `__Auth` (doctype, name, fieldname, password, encrypted)
			values ('User', %s, 'password', %s, 0)
			on duplicate key update password = values(password)""",
			(ученик, PBKDF2_FRAPPE),
		)
		настроить()
		self.assertEqual(frappe.utils.password.check_password(ученик, PASSWORD), ученик)
		self.assertTrue(self._хеш(ученик).startswith(ARGON2_PREFIX))

		frappe.utils.password.update_password(ученик, PASSWORD + "-2")
		self.assertTrue(self._хеш(ученик).startswith(ARGON2_PREFIX))
		self.assertEqual(frappe.utils.password.check_password(ученик, PASSWORD + "-2"), ученик)

	@staticmethod
	def _хеш(пользователь: str) -> str:
		return frappe.db.sql(
			"select password from `__Auth` where doctype = 'User' and name = %s and fieldname = 'password'",
			пользователь,
		)[0][0]
