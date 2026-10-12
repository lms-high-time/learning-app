# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.doctype.course_allocation.course_allocation import (
	назначения_пользователя,
)
from lms_frappe_app.tests.sample_data import (
	политика_по_умолчанию,
	добавить_в_организацию,
	создать_курс,
	создать_организацию,
	создать_ученика,
)

ПЕРВЫЙ = "sotrudnik-odin@example.com"
ВТОРОЙ = "sotrudnik-dva@example.com"


class IntegrationTestCourseAllocation(IntegrationTestCase):
	"""Членство и назначение курсов: кому назначение порождает зачисление.

	Политика квиза и прочие свойства организации — в тестах
	`Learning Organization`, у своего доктайпа.
	"""

	@classmethod
	def setUpClass(cls):
		"""Сотрудников тесты только читают: членство и назначения — в своих
		организации и курсе."""
		super().setUpClass()
		frappe.set_user("Administrator")
		создать_ученика(ПЕРВЫЙ)
		создать_ученика(ВТОРОЙ)

	def setUp(self):
		политика_по_умолчанию()

	def заново(self) -> None:
		"""Своя организация и свой курс — у теста или у строки таблицы."""
		self.организация = создать_организацию(f"Компания {frappe.generate_hash(length=6)}")
		self.курс = создать_курс(f"Курс {frappe.generate_hash(length=6)}")

	def назначить(self, **поля):
		return frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.организация,
				"course": self.курс,
				**поля,
			}
		).insert(ignore_permissions=True)

	# --- членство ---

	def test_повторное_членство_отклоняется(self):
		self.заново()
		добавить_в_организацию(ПЕРВЫЙ, self.организация)
		with self.assertRaises(frappe.DuplicateEntryError):
			добавить_в_организацию(ПЕРВЫЙ, self.организация)

	# --- назначение порождает зачисление ---

	def test_назначение_записывает_адресатов(self):
		"""На организацию — всех участников; поимённое не задевает остальных.
		У каждого случая свои организация и курс."""
		случаи = {
			"на организацию — все участники": (
				"Manager",
				{"audience": "Whole Organization", "mandatory": 1},
				{ПЕРВЫЙ: True, ВТОРОЙ: True},
			),
			"поимённое не задевает остальных": (
				"Member",
				{"audience": "Selected Members", "members": [{"user": ПЕРВЫЙ}]},
				{ПЕРВЫЙ: True, ВТОРОЙ: False},
			),
		}
		for что, (роль_второго, поля, записаны) in случаи.items():
			with self.subTest(что):
				self.заново()
				добавить_в_организацию(ПЕРВЫЙ, self.организация)
				добавить_в_организацию(ВТОРОЙ, self.организация, role=роль_второго)

				self.назначить(**поля)

				self.assertEqual(
					{
						участник: bool(
							frappe.db.exists("LMS Enrollment", {"member": участник, "course": self.курс})
						)
						for участник in записаны
					},
					записаны,
				)

	def test_повторное_сохранение_не_плодит_зачисления(self):
		self.заново()
		# У зачисления свой прогресс: пересоздание обнулило бы пройденное.
		добавить_в_организацию(ПЕРВЫЙ, self.организация)
		назначение = self.назначить()
		назначение.deadline = "2026-12-31"
		назначение.save(ignore_permissions=True)

		записи = frappe.get_all("LMS Enrollment", filters={"member": ПЕРВЫЙ, "course": self.курс})
		self.assertEqual(len(записи), 1)

	# --- ограничения ---

	def test_назначение_отклоняется(self):
		"""Курс вне списка организации и курс приостановленной организации не
		назначаются. У каждого случая своя организация."""

		def вне_списка():
			организация = frappe.get_doc("Learning Organization", self.организация)
			организация.append("allowed_courses", {"course": создать_курс("Единственный доступный")})
			организация.save(ignore_permissions=True)

		def приостановлена():
			frappe.db.set_value("Learning Organization", self.организация, "status", "Suspended")
			frappe.clear_document_cache("Learning Organization", self.организация)

		for что, подготовить in {
			"курс вне списка организации": вне_списка,
			"организация приостановлена": приостановлена,
		}.items():
			with self.subTest(что):
				self.заново()
				подготовить()

				with self.assertRaises(frappe.ValidationError):
					self.назначить()

	# --- дедлайны ---

	def test_чужое_поимённое_назначение_не_попадает_в_список(self):
		self.заново()
		добавить_в_организацию(ПЕРВЫЙ, self.организация)
		добавить_в_организацию(ВТОРОЙ, self.организация)
		назначение = self.назначить(audience="Selected Members", members=[{"user": ПЕРВЫЙ}])

		имена = lambda user: [н.name for н in назначения_пользователя(user)]

		self.assertIn(назначение.name, имена(ПЕРВЫЙ))
		self.assertNotIn(назначение.name, имена(ВТОРОЙ))
