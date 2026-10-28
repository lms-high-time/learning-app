# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Оригиналы подменённых методов не вызываются ни под каким путём, подмены
живы (learning-services#692)."""

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from lms_frappe_app import whitelist_guard


def оригиналы() -> dict[str, object]:
	"""HTTP-имя подменённого метода → его исходная функция, если она не цель
	другой подмены и есть в установке — как в `снять_подменённые`."""
	подмены = frappe.get_hooks("override_whitelisted_methods")
	цели = {whitelist_guard._функция(пути[-1]) for пути in подмены.values() if пути}
	найдены = {}
	for исходное in подмены:
		функция = whitelist_guard._функция(исходное)
		if функция is not None and функция not in цели:
			найдены[исходное] = функция
	return найдены


class TestСнятиеПодменённых(UnitTestCase):
	def test_хук_стоит_на_запросе_и_задаче(self):
		путь = "lms_frappe_app.whitelist_guard.снять_подменённые"
		self.assertIn(путь, frappe.get_hooks("before_request"))
		self.assertIn(путь, frappe.get_hooks("before_job"))

	def test_оригиналов_нет_в_реестре_а_подмены_живы(self):
		"""Ни один оригинал не whitelisted; метод, в который уходит каждое
		подменённое имя, — whitelisted: OAuth, регистрация, смена пароля,
		редактор Learning, отметка урока, версии, чтения Learning."""
		whitelist_guard.снять_подменённые()

		self.assertTrue(оригиналы())
		for исходное, функция in оригиналы().items():
			with self.subTest(исходное):
				self.assertNotIn(функция, frappe.whitelisted)
				self.assertNotIn(функция, frappe.guest_methods)
		for исходное in frappe.get_hooks("override_whitelisted_methods"):
			with self.subTest(подмена=исходное):
				путь = frappe.override_whitelisted_method(исходное)
				цель = whitelist_guard._функция(путь)
				if цель is None and not путь.startswith("lms_frappe_app."):
					continue  # подмена другого приложения, которой нет в установке
				self.assertIn(цель, frappe.whitelisted)

	def test_недоступная_подмена_не_роняет_запрос(self):
		"""Подмена с путём, которого нет в установке (короткое имя), не роняет
		снятие и не оставляет сообщения в ответе."""
		настоящие = frappe.get_hooks

		def хуки(имя=None, *args, **kwargs):
			значение = настоящие(имя, *args, **kwargs)
			if имя == "override_whitelisted_methods":
				значение = {**значение, "frappe.utils.change_log.get_versions": ["download_file"]}
			return значение

		frappe.local.message_log = []
		with patch("frappe.get_hooks", side_effect=хуки):
			whitelist_guard.снять_подменённые()
		self.assertEqual(frappe.local.message_log, [])

	def test_оригинал_под_другим_путём_отклоняется(self):
		"""`/api/method/lms.tests.test_utils.get_courses` — та же функция
		`get_courses` Learning под другим путём модуля: гостю — отказ."""
		from lms.lms import utils as learning

		функция = frappe.get_attr("lms.tests.test_utils.get_courses")
		self.assertIs(функция, learning.get_courses)
		whitelist_guard.снять_подменённые()
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			frappe.is_whitelisted(функция)



class TestСнятиеМетодовPayments(UnitTestCase):
	"""Приложение payments по HTTP не отвечает (learning-services#679)."""

	ГОСТЕВОЙ = "payments.templates.pages.stripe_checkout.make_payment"
	ACCEPT = "frappe.website.doctype.web_form.web_form.accept"

	def test_хук_стоит_на_запросе_и_задаче(self):
		путь = "lms_frappe_app.whitelist_guard.снять_методы_payments"
		self.assertIn(путь, frappe.get_hooks("before_request"))
		self.assertIn(путь, frappe.get_hooks("before_job"))

	def test_в_реестре_нет_функций_payments(self):
		whitelist_guard.снять_методы_payments()

		self.assertTrue(whitelist_guard._модули_payments)
		оставшиеся = [ф for ф in frappe.whitelisted if whitelist_guard._из_payments(ф)]
		self.assertEqual(оставшиеся, [])

	def test_импортированы_все_модули_с_методами(self):
		"""Модуль, не импортированный заранее, зарегистрировал бы методы уже
		после снятия: каждый модуль payments, где в тексте есть `whitelist`, —
		в списке импортированных."""
		from pathlib import Path

		корень = Path(frappe.get_app_path("payments")).parent
		с_методами = {
			".".join(файл.relative_to(корень).with_suffix("").parts).removesuffix(".__init__")
			for файл in (корень / "payments").rglob("*.py")
			if "whitelist" in файл.read_text(encoding="utf-8") and not файл.name.startswith("test_")
		}
		whitelist_guard.снять_методы_payments()

		self.assertIn("payments.templates.pages.stripe_checkout", с_методами)
		self.assertLessEqual(с_методами, set(whitelist_guard._модули_payments))

	def test_гостю_отказ_на_методе_шлюза(self):
		"""Так же, как `cmd` из тела запроса: `execute_cmd` сверяет реестр."""
		from frappe.handler import execute_cmd

		whitelist_guard.снять_методы_payments()
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			execute_cmd(self.ГОСТЕВОЙ)

	def test_метод_документа_payments_не_вызывается(self):
		"""Методы классов DocType payments — путь `run_doc_method`, и под
		администратором."""
		whitelist_guard.снять_методы_payments()
		документ = frappe.get_doc("Razorpay Settings")
		with self.assertRaises(frappe.PermissionError):
			документ.is_whitelisted("clear")

	def test_веб_форма_отправляется_методом_frappe(self):
		"""Подмена payments отстала от проверок Frappe 16 — действует подмена
		приложения на штатный метод, и он открыт гостю."""
		from frappe.website.doctype.web_form import web_form

		whitelist_guard.снять_подменённые()
		whitelist_guard.снять_методы_payments()

		путь = frappe.override_whitelisted_method(self.ACCEPT)
		self.assertEqual(путь, self.ACCEPT)
		self.assertIs(frappe.get_attr(путь), web_form.accept)
		self.assertIn(web_form.accept, frappe.whitelisted)
		self.assertIn(web_form.accept, frappe.guest_methods)
		self.assertNotIn(frappe.get_attr("payments.overrides.payment_webform.accept"), frappe.whitelisted)

	def _запрос(self, путь: str):
		"""Запрос страницы на время проверки: рендерер читает `frappe.local.request`,
		а соседние тесты оставляют там `None`."""
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		прежний = getattr(frappe.local, "request", None)
		self.addCleanup(setattr, frappe.local, "request", прежний)
		frappe.local.request = Request(EnvironBuilder(path=f"/{путь}").get_environ())

	def test_страницы_шлюзов_отвечают_404(self):
		from frappe.website.path_resolver import PathResolver

		for путь in ("stripe_checkout", "razorpay_checkout", "payment-cancel", "payment-success"):
			with self.subTest(путь):
				self._запрос(путь)
				_, страница = PathResolver(путь, http_status_code=200).resolve()
				self.assertIsInstance(страница, whitelist_guard.СтраницаPayments)
				self.assertEqual(страница.http_status_code, 404)

	def test_прочие_страницы_не_задеты(self):
		from frappe.website.path_resolver import PathResolver

		for путь in ("login", "lms"):
			with self.subTest(путь):
				self._запрос(путь)
				_, страница = PathResolver(путь).resolve()
				self.assertNotIsInstance(страница, whitelist_guard.СтраницаPayments)
