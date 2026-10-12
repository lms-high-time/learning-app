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

	def test_чтение_параметра(self):
		"""Заполненная настройка применяется, пустая уступает запасному значению
		из кода. После каждого случая настройки возвращаются к умолчанию."""
		случаи = {
			"значение из настроек применяется": [
				(поле, читалка, запасное + 1, запасное + 1) for поле, читалка, запасное in ПАРАМЕТРЫ
			],
			"пустая настройка даёт запасное": [
				(поле, читалка, "", запасное) for поле, читалка, запасное in ПАРАМЕТРЫ
			],
		}
		for что, строки in случаи.items():
			try:
				for поле, читалка, значение, ожидание in строки:
					with self.subTest(что, поле=поле):
						self.задать(поле, значение)

						self.assertEqual(читалка(), ожидание)
			finally:
				политика_по_умолчанию()

	# --- значения по умолчанию при установке и миграции ---

	def test_значения_при_установке(self):
		"""Поле, появившееся миграцией, перестаёт быть пустым в админке. Адрес
		сервиса зависит от сайта, поэтому в схеме его нет, а умолчание есть.
		Пустое значение — решение админа, а не недосмотр установки. После
		каждого случая настройки возвращаются к умолчанию."""

		def поля(*имена):
			return lambda: tuple(frappe.db.get_singles_dict(НАСТРОЙКИ)[имя] for имя in имена)

		случаи = {
			"адрес сервиса по умолчанию ведёт на этот сайт": (
				("agent_service_url",),
				(),
				адрес_сервиса,
				get_url().rstrip("/"),
			),
			"очищенное поле остаётся пустым": (
				(),
				("carry_over_depth", "agent_service_url"),
				поля("carry_over_depth", "agent_service_url"),
				("", ""),
			),
			"поле без строки получает значение из схемы": (
				("carry_over_depth",),
				(),
				поля("carry_over_depth"),
				(str(ГЛУБИНА_ПЕРЕНОСА),),
			),
		}
		for что, (без_строки, очищенные, прочитать, ожидание) in случаи.items():
			with self.subTest(что):
				try:
					for поле in без_строки:
						frappe.db.delete("Singles", {"doctype": НАСТРОЙКИ, "field": поле})
						frappe.clear_document_cache(НАСТРОЙКИ, НАСТРОЙКИ)
					for поле in очищенные:
						self.задать(поле, "")

					обеспечить_значения_настроек()

					self.assertEqual(прочитать(), ожидание)
				finally:
					политика_по_умолчанию()

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
