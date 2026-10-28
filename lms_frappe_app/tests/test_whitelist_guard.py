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
	"""Методы payments по HTTP не вызываются, кроме цели подмены
	(learning-services#679)."""

	ГОСТЕВОЙ = "payments.templates.pages.stripe_checkout.make_payment"
	ЦЕЛЬ_ПОДМЕНЫ = "payments.overrides.payment_webform.accept"

	def test_хук_стоит_на_запросе(self):
		self.assertIn(
			"lms_frappe_app.whitelist_guard.снять_методы_payments", frappe.get_hooks("before_request")
		)

	def test_в_реестре_нет_методов_payments_кроме_цели_подмены(self):
		whitelist_guard.снять_методы_payments()

		self.assertTrue(whitelist_guard._модули_payments)
		оставшиеся = {
			f"{функция.__module__}.{функция.__qualname__}"
			for функция in frappe.whitelisted
			if функция.__module__.startswith("payments.")
		}
		self.assertEqual(оставшиеся, {self.ЦЕЛЬ_ПОДМЕНЫ})
		self.assertFalse({ф for ф in frappe.guest_methods if ф.__module__.startswith("payments.")} - {
			frappe.get_attr(self.ЦЕЛЬ_ПОДМЕНЫ)
		})

	def test_гостю_отказ_на_методе_шлюза(self):
		"""Так же, как `cmd` из тела запроса: `execute_cmd` сверяет реестр."""
		from frappe.handler import execute_cmd

		whitelist_guard.снять_методы_payments()
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			execute_cmd(self.ГОСТЕВОЙ)

	def test_отправка_веб_формы_жива(self):
		whitelist_guard.снять_подменённые()
		whitelist_guard.снять_методы_payments()

		путь = frappe.override_whitelisted_method("frappe.website.doctype.web_form.web_form.accept")
		self.assertIn(frappe.get_attr(путь), frappe.whitelisted)
