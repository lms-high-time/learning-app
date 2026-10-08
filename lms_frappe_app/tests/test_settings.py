# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Параметры методики из `Agent Learning Settings` (lms-platform#198).

Проверяется договор чтения: заполненная настройка применяется, пустая уступает
запасному значению из кода. Поведение каждого параметра — в тестах того метода,
который им пользуется.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_url

from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	НАСТРОЙКИ,
	ПРОБНЫХ_УРОКОВ,
	пробных_уроков,
)
from lms_frappe_app.api.student import (
	ГЛУБИНА_ПЕРЕНОСА,
	ЛИМИТ_ЗАМЕТОК,
	глубина_переноса,
	лимит_заметок,
)
from lms_frappe_app.install import обеспечить_значения_настроек
from lms_frappe_app.tests.sample_data import политика_по_умолчанию
from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	адрес_сервиса,
)

#: Числовой параметр методики: поле настроек, читалка, запасное значение.
ПАРАМЕТРЫ = (
	("carry_over_depth", глубина_переноса, ГЛУБИНА_ПЕРЕНОСА),
	("student_notes_limit", лимит_заметок, ЛИМИТ_ЗАМЕТОК),
	("web_demo_lessons", пробных_уроков, ПРОБНЫХ_УРОКОВ),
)


class IntegrationTestSettings(IntegrationTestCase):
	"""Чтение настроек и значения по умолчанию при установке."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(политика_по_умолчанию)
		frappe.set_user("Administrator")

	def задать(self, поле: str, значение) -> None:
		frappe.db.set_single_value(НАСТРОЙКИ, поле, значение)
		frappe.clear_document_cache(НАСТРОЙКИ, НАСТРОЙКИ)

	def test_значение_из_настроек_применяется(self):
		for поле, читалка, запасное in ПАРАМЕТРЫ:
			with self.subTest(поле=поле):
				self.задать(поле, запасное + 1)
				self.assertEqual(читалка(), запасное + 1)

	def test_пустая_настройка_даёт_запасное(self):
		for поле, читалка, запасное in ПАРАМЕТРЫ:
			with self.subTest(поле=поле):
				self.задать(поле, "")
				self.assertEqual(читалка(), запасное)

	# --- значения по умолчанию при установке и миграции ---

	def test_поле_без_строки_получает_значение_из_схемы(self):
		"""Поле, появившееся миграцией, перестаёт быть пустым в админке."""
		frappe.db.delete("Singles", {"doctype": НАСТРОЙКИ, "field": "carry_over_depth"})
		frappe.clear_document_cache(НАСТРОЙКИ, НАСТРОЙКИ)

		обеспечить_значения_настроек()

		значения = frappe.db.get_singles_dict(НАСТРОЙКИ)
		self.assertEqual(значения["carry_over_depth"], str(ГЛУБИНА_ПЕРЕНОСА))

	def test_адрес_сервиса_по_умолчанию_ведёт_на_этот_сайт(self):
		"""Адрес зависит от сайта, поэтому в схеме его нет, а умолчание есть."""
		frappe.db.delete("Singles", {"doctype": НАСТРОЙКИ, "field": "agent_service_url"})
		frappe.clear_document_cache(НАСТРОЙКИ, НАСТРОЙКИ)

		обеспечить_значения_настроек()

		self.assertEqual(адрес_сервиса(), get_url().rstrip("/"))

	def test_заполненное_значение_не_переписывается(self):
		"""`after_migrate` идёт на каждый старт контейнера — правка админа живёт."""
		self.задать("carry_over_depth", ГЛУБИНА_ПЕРЕНОСА + 2)
		self.задать("agent_service_url", "https://agent.example.com")

		обеспечить_значения_настроек()
		обеспечить_значения_настроек()

		self.assertEqual(глубина_переноса(), ГЛУБИНА_ПЕРЕНОСА + 2)
		self.assertEqual(адрес_сервиса(), "https://agent.example.com")

	def test_очищенное_поле_остаётся_пустым(self):
		"""Пустое значение — решение админа, а не недосмотр установки."""
		self.задать("carry_over_depth", "")

		обеспечить_значения_настроек()

		self.assertEqual(frappe.db.get_singles_dict(НАСТРОЙКИ)["carry_over_depth"], "")

	def test_патч_стирает_значение_убранного_поля(self):
		from lms_frappe_app.patches.v0_1.drop_authoring_guide_tool import execute

		frappe.db.set_single_value(НАСТРОЙКИ, "authoring_guide_tool", "authoring_guide", update_modified=False)
		self.assertIn("authoring_guide_tool", frappe.db.get_singles_dict(НАСТРОЙКИ))

		было = frappe.db.get_singles_dict(НАСТРОЙКИ)
		execute()
		execute()  # повторный запуск не падает

		# Ушло ровно это значение: остальные настройки на месте.
		было.pop("authoring_guide_tool")
		self.assertEqual(frappe.db.get_singles_dict(НАСТРОЙКИ), было)
