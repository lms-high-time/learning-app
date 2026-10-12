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

	def setUp(self):
		политика_по_умолчанию()
		self.организация = создать_организацию(f"Компания {frappe.generate_hash(length=6)}")
		self.курс = создать_курс(f"Курс {frappe.generate_hash(length=6)}")
		создать_ученика(ПЕРВЫЙ)
		создать_ученика(ВТОРОЙ)

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
		добавить_в_организацию(ПЕРВЫЙ, self.организация)
		with self.assertRaises(frappe.DuplicateEntryError):
			добавить_в_организацию(ПЕРВЫЙ, self.организация)

	# --- назначение порождает зачисление ---

	def test_назначение_на_организацию_записывает_всех_участников(self):
		добавить_в_организацию(ПЕРВЫЙ, self.организация)
		добавить_в_организацию(ВТОРОЙ, self.организация, role="Manager")

		self.назначить(audience="Whole Organization", mandatory=1)

		for участник in (ПЕРВЫЙ, ВТОРОЙ):
			self.assertTrue(
				frappe.db.exists("LMS Enrollment", {"member": участник, "course": self.курс}),
				f"{участник} не записан на курс",
			)

	def test_поимённое_назначение_не_задевает_остальных(self):
		добавить_в_организацию(ПЕРВЫЙ, self.организация)
		добавить_в_организацию(ВТОРОЙ, self.организация)

		self.назначить(audience="Selected Members", members=[{"user": ПЕРВЫЙ}])

		self.assertTrue(frappe.db.exists("LMS Enrollment", {"member": ПЕРВЫЙ, "course": self.курс}))
		self.assertFalse(frappe.db.exists("LMS Enrollment", {"member": ВТОРОЙ, "course": self.курс}))

	def test_повторное_сохранение_не_плодит_зачисления(self):
		# У зачисления свой прогресс: пересоздание обнулило бы пройденное.
		добавить_в_организацию(ПЕРВЫЙ, self.организация)
		назначение = self.назначить()
		назначение.deadline = "2026-12-31"
		назначение.save(ignore_permissions=True)

		записи = frappe.get_all("LMS Enrollment", filters={"member": ПЕРВЫЙ, "course": self.курс})
		self.assertEqual(len(записи), 1)

	# --- ограничения ---

	def test_курс_вне_списка_организации_не_назначается(self):
		организация = frappe.get_doc("Learning Organization", self.организация)
		организация.append("allowed_courses", {"course": создать_курс("Единственный доступный")})
		организация.save(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			self.назначить()

	def test_приостановленной_организации_курс_не_назначить(self):
		frappe.db.set_value("Learning Organization", self.организация, "status", "Suspended")
		frappe.clear_document_cache("Learning Organization", self.организация)
		with self.assertRaises(frappe.ValidationError):
			self.назначить()

	# --- дедлайны ---

	def test_чужое_поимённое_назначение_не_попадает_в_список(self):
		добавить_в_организацию(ПЕРВЫЙ, self.организация)
		добавить_в_организацию(ВТОРОЙ, self.организация)
		назначение = self.назначить(audience="Selected Members", members=[{"user": ПЕРВЫЙ}])

		имена = lambda user: [н.name for н in назначения_пользователя(user)]

		self.assertIn(назначение.name, имена(ПЕРВЫЙ))
		self.assertNotIn(назначение.name, имена(ВТОРОЙ))
