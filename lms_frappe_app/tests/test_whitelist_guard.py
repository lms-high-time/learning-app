# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Оригиналы подменённых методов не вызываются ни под каким путём (learning-services#692)."""

import importlib
import pkgutil

import frappe
from frappe.tests import UnitTestCase

from lms_frappe_app import whitelist_guard


def оригиналы() -> dict[str, object]:
	"""HTTP-имя подменённого метода → его исходная функция."""
	найдены = {}
	for исходное, подмены in frappe.get_hooks("override_whitelisted_methods").items():
		try:
			функция = frappe.get_attr(исходное)
		except Exception:
			# Подмена метода, которого в установке нет, — как в `снять_подменённые`.
			continue
		if функция is not frappe.get_attr(подмены[-1]):
			найдены[исходное] = функция
	return найдены


def импортировать_пакеты(*пакеты: str) -> None:
	"""Импортирует модули пакетов: декоратор whitelist срабатывает при импорте,
	и функция, обёрнутая заново где-то ещё, попала бы в реестр только так."""
	for имя in пакеты:
		пакет = importlib.import_module(имя)
		for сведения in pkgutil.walk_packages(пакет.__path__, f"{имя}.", onerror=lambda _имя: None):
			try:
				importlib.import_module(сведения.name)
			except (Exception, SystemExit):
				# Модуль без нужной зависимости или с побочным выходом — не наш случай.
				continue


class TestСнятиеПодменённых(UnitTestCase):
	def test_хук_стоит_на_запросе_и_задаче(self):
		путь = "lms_frappe_app.whitelist_guard.снять_подменённые"
		self.assertIn(путь, frappe.get_hooks("before_request"))
		self.assertIn(путь, frappe.get_hooks("before_job"))

	def test_оригиналов_нет_в_реестре(self):
		"""Ни сам оригинал, ни его копия под другим модулем не whitelisted —
		после импорта всех модулей Learning и Frappe."""
		импортировать_пакеты("lms", "frappe")
		whitelist_guard.снять_подменённые()

		self.assertTrue(оригиналы())
		for исходное, функция in оригиналы().items():
			with self.subTest(исходное):
				self.assertNotIn(функция, frappe.whitelisted)
				self.assertNotIn(функция, frappe.guest_methods)
		имена = {(ф.__module__, ф.__qualname__) for ф in оригиналы().values()}
		копии = [ф for ф in frappe.whitelisted if (ф.__module__, ф.__qualname__) in имена]
		self.assertFalse(копии, копии)

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
