# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Сессия браузера: только из cookie и не дольше срока (learning-services#666)."""

from datetime import UTC, datetime
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import set_request
from werkzeug.exceptions import HTTPException

from lms_frappe_app.session_guard import (
	СРОК_СЕССИИ,
	sid_в_параметрах,
	завершить_истёкшую,
	истекла,
	конец_сессии,
	не_ставить_cookie,
	отклонить_sid_из_запроса,
)
from lms_frappe_app.tests.sample_data import создать_ученика

СЕЙЧАС = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


class _ЗапросНаВремя:
	"""Запрос, cookie и вход — только на время теста."""

	ИМЕНА = ("request", "cookie_manager", "login_manager")

	def setUp(self):
		super().setUp()
		self.прежнее = {имя: getattr(frappe.local, имя, None) for имя in self.ИМЕНА}

	def tearDown(self):
		frappe.set_user("Administrator")
		for имя, значение in self.прежнее.items():
			setattr(frappe.local, имя, значение)
		super().tearDown()


def _запрос(**параметры):
	from werkzeug.test import EnvironBuilder
	from werkzeug.wrappers import Request

	return Request(EnvironBuilder(**параметры).get_environ())


class UnitTestSidInRequest(_ЗапросНаВремя, UnitTestCase):
	def test_sid_в_параметрах(self):
		"""Те же источники, что у `form_dict` Frappe; cookie — законный путь."""
		случаи = {
			"в адресе": ({"query_string": "sid=abc"}, True),
			"в форме": ({"method": "POST", "data": {"sid": "abc"}}, True),
			"в JSON": ({"method": "POST", "json": {"sid": "abc"}}, True),
			"JSON списком": ({"method": "POST", "json": ["sid"]}, False),
			"битый JSON": ({"method": "POST", "data": '{"sid": ', "content_type": "application/json"}, False),
			"JSON глубже предела разбора": (
				{"method": "POST", "data": "[" * 100_000 + "]" * 100_000, "content_type": "application/json"},
				False,
			),
			"только cookie": ({"headers": {"Cookie": "sid=abc"}}, False),
			"другие параметры": ({"query_string": "side=1&key=sid"}, False),
		}
		for что, (параметры, итог) in случаи.items():
			with self.subTest(что):
				self.assertEqual(sid_в_параметрах(_запрос(**параметры)), итог)

	def test_отказ_без_cookie(self):
		"""Запрос с `sid` в адресе — 400, и ни одной cookie в ответе: ни
		сессии из параметра, ни удаления своей. Без `sid` — ничего не трогает."""
		from frappe.auth import CookieManager

		for что, адрес, отказ in (("с sid", "sid=чужая", True), ("без sid", "key=1", False)):
			with self.subTest(что):
				set_request(path="/lms", query_string=адрес)
				frappe.local.cookie_manager = CookieManager()
				frappe.local.cookie_manager.cookies["sid"] = {"value": "чужая"}
				frappe.local.cookie_manager.to_delete.append("sid")

				if отказ:
					with self.assertRaises(HTTPException) as ответ:
						отклонить_sid_из_запроса()
					self.assertEqual(ответ.exception.response.status_code, 400)
				else:
					отклонить_sid_из_запроса()
					не_ставить_cookie()

				self.assertEqual(bool(frappe.local.cookie_manager.cookies), not отказ)
				self.assertEqual(bool(frappe.local.cookie_manager.to_delete), not отказ)

	def test_хуки_подключены(self):
		"""Отказ — первым из хуков приложения: переадресация на экран согласия
		поставила бы cookie сессии из параметра. Снятие cookie — и на ответах,
		до которых `before_request` не дошёл."""
		перед = frappe.get_hooks("before_request")
		свои = [хук for хук in перед if хук.startswith("lms_frappe_app.")]
		self.assertEqual(
			свои[:2],
			[
				"lms_frappe_app.session_guard.отклонить_sid_из_запроса",
				"lms_frappe_app.session_guard.завершить_истёкшую",
			],
		)
		self.assertIn("lms_frappe_app.session_guard.не_ставить_cookie", frappe.get_hooks("after_request"))
		self.assertIn(
			"lms_frappe_app.session_guard.ограничить_срок_сессии", frappe.get_hooks("on_session_creation")
		)


class UnitTestSessionEnd(UnitTestCase):
	def test_конец_сессии(self):
		"""Свой срок от входа; более ранний прежний («Войти как») сохраняется."""
		наш = СЕЙЧАС + СРОК_СЕССИИ
		раньше = datetime(2026, 10, 25, tzinfo=UTC)
		случаи = {
			"без прежнего": (None, наш),
			"прежний раньше": (раньше.isoformat(), раньше),
			"прежний позже": ((наш + СРОК_СЕССИИ).isoformat(), наш),
			"прежний без пояса — UTC": ("2026-10-25T00:00:00", раньше),
		}
		for что, (прежний, итог) in случаи.items():
			with self.subTest(что):
				self.assertEqual(datetime.fromisoformat(конец_сессии(прежний, СЕЙЧАС)), итог)

	def test_истекла(self):
		случаи = {
			"срока нет": (None, False),
			"срок впереди": ((СЕЙЧАС + СРОК_СЕССИИ).isoformat(), False),
			"срок прошёл": (datetime(2026, 10, 1, tzinfo=UTC).isoformat(), True),
			"без пояса — UTC": ("2026-10-22T11:59:00", True),
		}
		for что, (конец, итог) in случаи.items():
			with self.subTest(что):
				self.assertIs(истекла(конец, СЕЙЧАС), итог)


class IntegrationTestSessionEnd(_ЗапросНаВремя, IntegrationTestCase):
	def test_вход_ставит_срок_сессии(self):
		"""Срок пишется и в кэш, откуда сессию читает Frappe, и в базу, откуда
		она вернётся в кэш (learning-services#666)."""
		from frappe.auth import CookieManager, LoginManager

		пользователь = создать_ученика(f"session-{frappe.generate_hash(length=8)}@example.com")
		set_request(method="POST", path="/")
		frappe.local.cookie_manager = CookieManager()
		frappe.local.login_manager = LoginManager()
		до = datetime.now(tz=UTC)

		frappe.local.login_manager.login_as(пользователь)

		sid = frappe.session.sid
		self.addCleanup(frappe.db.delete, "Sessions", {"sid": sid})
		в_кэше = frappe.cache.hget("session", sid)["data"]["session_end"]
		в_базе = frappe.parse_json(
			frappe.db.get_value("Sessions", {"sid": sid}, "sessiondata", order_by=None)
		)
		self.assertEqual(в_кэше, в_базе["session_end"])
		конец = datetime.fromisoformat(в_кэше)
		self.assertLessEqual(до + СРОК_СЕССИИ, конец)
		self.assertLessEqual(конец, datetime.now(tz=UTC) + СРОК_СЕССИИ)

	def test_срок_сессии_из_базы(self):
		"""Сессию, поднятую из базы без кэша, Frappe срока не сверял: истёкшая
		жила ещё запрос и возвращалась в кэш (learning-services#691)."""
		# Завершение сессии Frappe коммитит само, и пользователь пережил бы откат.
		коммит = patch.object(frappe.db, "commit")
		коммит.start()
		self.addCleanup(коммит.stop)
		from frappe.auth import CookieManager, LoginManager

		пользователь = создать_ученика(f"session-{frappe.generate_hash(length=8)}@example.com")
		set_request(method="POST", path="/")
		frappe.local.cookie_manager = CookieManager()
		frappe.local.login_manager = LoginManager()
		frappe.local.login_manager.login_as(пользователь)
		sid = frappe.session.sid
		self.addCleanup(frappe.db.delete, "Sessions", {"sid": sid})

		for что, конец, остаётся in (
			("срок впереди", datetime.now(tz=UTC) + СРОК_СЕССИИ, True),
			("срок прошёл", datetime(2026, 10, 1, tzinfo=UTC), False),
		):
			with self.subTest(что):
				данные = frappe.parse_json(
					frappe.db.get_value("Sessions", {"sid": sid}, "sessiondata", order_by=None)
				)
				данные.session_end = конец.isoformat()
				frappe.db.set_value(
					"Sessions", {"sid": sid}, "sessiondata", frappe.as_json(данные), update_modified=False
				)
				frappe.cache.hdel("session", sid)
				set_request(method="GET", path="/", headers={"Cookie": f"sid={sid}"})
				frappe.local.cookie_manager = CookieManager()
				frappe.local.login_manager = LoginManager()
				self.assertEqual(frappe.session.user, пользователь)

				завершить_истёкшую()

				self.assertEqual(frappe.session.user, пользователь if остаётся else "Guest")
				в_базе = frappe.db.sql("select count(*) from `tabSessions` where sid = %s", sid)[0][0]
				self.assertEqual(bool(в_базе), остаётся)
				if not остаётся:
					self.assertIsNone(frappe.cache.hget("session", sid))

	def test_отключение_завершает_сессии(self):
		"""Отключение аккаунта сессий браузера Frappe не завершало: `clear_sessions`
		в `User.on_update` — только при смене типа (learning-services#691)."""
		# Завершение сессии Frappe коммитит само, и пользователь пережил бы откат.
		коммит = patch.object(frappe.db, "commit")
		коммит.start()
		self.addCleanup(коммит.stop)
		from frappe.auth import CookieManager, LoginManager

		пользователь = создать_ученика(f"session-{frappe.generate_hash(length=8)}@example.com")
		set_request(method="POST", path="/")
		frappe.local.cookie_manager = CookieManager()
		frappe.local.login_manager = LoginManager()
		frappe.local.login_manager.login_as(пользователь)
		sid = frappe.session.sid
		self.addCleanup(frappe.db.delete, "Sessions", {"sid": sid})
		frappe.set_user("Administrator")

		документ = frappe.get_doc("User", пользователь)
		документ.enabled = 0
		документ.save(ignore_permissions=True)

		self.assertEqual(frappe.db.sql("select count(*) from `tabSessions` where sid = %s", sid)[0][0], 0)
		self.assertIsNone(frappe.cache.hget("session", sid))
