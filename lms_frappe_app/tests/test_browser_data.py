# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Данные сайта в браузере: выход и приватные файлы (learning-services#688)."""

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import set_request
from werkzeug.wrappers import Response

from lms_frappe_app.browser_data import CLEAR_SITE_DATA, заголовки_ответа, запомнить_выход
from lms_frappe_app.tests.sample_data import создать_ученика


class _ЗапросНаВремя:
	ИМЕНА = ("request", "cookie_manager", "login_manager")

	def setUp(self):
		super().setUp()
		self.прежнее = {имя: getattr(frappe.local, имя, None) for имя in self.ИМЕНА}
		self.addCleanup(frappe.local.flags.pop, "выход_из", None)
		self.addCleanup(frappe.local.response.pop, "session_expired", None)

	def tearDown(self):
		frappe.set_user("Administrator")
		for имя, значение in self.прежнее.items():
			setattr(frappe.local, имя, значение)
		super().tearDown()


class UnitTestBrowserData(_ЗапросНаВремя, UnitTestCase):
	def test_очистка_после_выхода(self):
		"""Заголовок — только когда вышла сама сессия запроса: при выходе «за
		другого» (отключение пользователя администратором) сессия остаётся
		администратора, и его браузер не трогается."""
		случаи = {
			"свой выход": ("student@example.com", "Guest", True),
			"выход за другого": ("Administrator", "Administrator", False),
			"без выхода": (None, "Guest", False),
			"выход гостя": ("Guest", "Guest", False),
		}
		for что, (вышел, после, очистка) in случаи.items():
			with self.subTest(что):
				set_request(method="POST", path="/api/method/logout")
				frappe.local.flags.pop("выход_из", None)
				if вышел:
					frappe.set_user(вышел)
					запомнить_выход()
				frappe.set_user(после)
				ответ = Response()

				заголовки_ответа(ответ, frappe.request)

				self.assertEqual(ответ.headers.get("Clear-Site-Data"), CLEAR_SITE_DATA if очистка else None)

	def test_очистка_когда_сессия_истекла(self):
		"""Пришёл `sid`, которого уже нет: Frappe отвечает гостю с
		`session_expired`, и браузер стирает данные сайта."""
		for что, истекла in (("истекла", True), ("жива", False)):
			with self.subTest(что):
				set_request(path="/lms/courses")
				frappe.local.flags.pop("выход_из", None)
				frappe.local.response.pop("session_expired", None)
				if истекла:
					frappe.local.response["session_expired"] = 1
				ответ = Response()

				заголовки_ответа(ответ, frappe.request)

				self.assertEqual(ответ.headers.get("Clear-Site-Data"), CLEAR_SITE_DATA if истекла else None)

	def test_приватные_файлы_не_кэшируются(self):
		"""`no-store` вместо `private,max-age=3600` Frappe; прочие ответы — как были."""
		случаи = {
			"/private/files/homework.pdf": "no-store",
			"/files/logo.png": "public,max-age=60",
			"/lms/courses": "public,max-age=60",
		}
		for путь, итог in случаи.items():
			with self.subTest(путь):
				set_request(path=путь)
				ответ = Response()
				ответ.headers["Cache-Control"] = "public,max-age=60"

				заголовки_ответа(ответ, frappe.request)

				self.assertEqual(ответ.headers["Cache-Control"], итог)

	def test_хуки_подключены(self):
		self.assertIn("lms_frappe_app.browser_data.запомнить_выход", frappe.get_hooks("on_logout"))
		self.assertIn("lms_frappe_app.browser_data.заголовки_ответа", frappe.get_hooks("after_request"))


class IntegrationTestLogoutClearsSiteData(_ЗапросНаВремя, IntegrationTestCase):
	def test_выход_очищает_данные_сайта(self):
		"""Сквозь `LoginManager.logout`: хук `on_logout` видит выходящего, а к
		`after_request` сессия запроса уже гостевая."""
		from frappe.auth import CookieManager, LoginManager

		пользователь = создать_ученика(f"logout-data-{frappe.generate_hash(length=8)}@example.com")
		set_request(method="POST", path="/api/method/logout")
		frappe.local.cookie_manager = CookieManager()
		frappe.local.login_manager = LoginManager()
		frappe.local.login_manager.login_as(пользователь)
		ответ = Response()

		frappe.local.login_manager.logout()
		заголовки_ответа(ответ, frappe.request)

		self.assertEqual(ответ.headers.get("Clear-Site-Data"), CLEAR_SITE_DATA)

	def test_пришедший_sid_без_сессии_очищает_данные_сайта(self):
		"""Сквозь `Session`: сессию удалили (истекла, завершена с другого
		устройства), а браузер пришёл с её `sid`."""
		from frappe.auth import CookieManager
		from frappe.sessions import Session

		set_request(path="/lms/courses", headers={"Cookie": "sid=deleted-session-sid"})
		frappe.local.cookie_manager = CookieManager()
		frappe.local.response.pop("session_expired", None)
		Session(user=None, resume=True)
		ответ = Response()

		заголовки_ответа(ответ, frappe.request)

		self.assertEqual(ответ.headers.get("Clear-Site-Data"), CLEAR_SITE_DATA)
