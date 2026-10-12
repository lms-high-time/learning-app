# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Наша трактовка структуры курса против трактовки Frappe Learning.

`Why:` три ошибки подряд появились из-за того, что тестовые данные строились
не так, как их строит настоящая система, а код читал структуру по-своему.
Порядок уроков оказался случайным, и заметили это лишь когда понадобился
метод, который его показывает.

Проверка ловит **класс** ошибок, а не случай: если Frappe Learning считает
состав или порядок иначе, чем мы, тест краснеет — независимо от того, какое
именно поле или таблицу поменяли в очередной версии.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import (
	зачислить,
	привязать_урок,
	создать_ученика,
	создать_урок,
)
from lms_frappe_app.agent_learning.structure import главы_курса, уроки_курса


class IntegrationTestCourseStructure(IntegrationTestCase):
	"""Состав и порядок совпадают с тем, что показывает браузер."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"struct-{суффикс}@example.com")
		первый = создать_урок(f"Первый {суффикс}")
		self.курс = зачислить(self.ученик, первый)
		self.глава = frappe.db.get_value("Course Lesson", первый, "chapter")
		self.уроки = [первый]
		for имя in ("Второй", "Третий"):
			урок = frappe.get_doc(
				{"doctype": "Course Lesson", "title": имя, "chapter": self.глава}
			).insert(ignore_permissions=True).name
			привязать_урок(self.глава, урок)
			self.уроки.append(урок)

	def test_состав_и_порядок_совпадают_с_frappe_learning(self):
		from lms.lms.utils import get_lessons

		их = [урок["name"] for урок in get_lessons(self.курс)]
		наш = уроки_курса(self.курс)

		self.assertEqual(наш, их, "структура курса разошлась с Frappe Learning")
		# Явная проверка того, что сломалось: idx у самих записей всегда ноль,
		# и сортировка по нему давала случайный порядок.
		self.assertEqual(их, self.уроки)

	def test_главы_читаются_тем_же_правилом(self):
		from lms.lms.utils import get_chapters

		их = [глава["name"] for глава in get_chapters(self.курс)]
		наш = [глава["name"] for глава in главы_курса(self.курс)]

		self.assertEqual(наш, их)

	def test_урок_вне_порядка_не_теряется(self):
		"""Запасной путь для курсов, собранных импортом или миграцией."""
		сирота = frappe.get_doc(
			{"doctype": "Course Lesson", "title": "Без ссылки", "chapter": self.глава}
		).insert(ignore_permissions=True).name

		# Ни один урок не теряется: известный порядок идёт первым, остальное —
		# следом. Потерять урок хуже, чем показать его последним.
		пустая = frappe.get_doc(
			{"doctype": "Course Chapter", "title": "Без ссылок", "course": self.курс}
		).insert(ignore_permissions=True)
		одинокий = frappe.get_doc(
			{"doctype": "Course Lesson", "title": "Одинокий", "chapter": пустая.name}
		).insert(ignore_permissions=True).name

		состав = уроки_курса(self.курс)

		self.assertEqual(состав[: len(self.уроки)], self.уроки, "порядок сбился")
		self.assertIn(сирота, состав, "урок без строки порядка потерян")
		self.assertIn(одинокий, состав, "глава без строк порядка потеряна")
