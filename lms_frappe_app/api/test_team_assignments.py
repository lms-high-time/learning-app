# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Назначения руководителем и письма о назначении и сроке (#365)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from lms_frappe_app.agent_learning import notices
from lms_frappe_app.api import team
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_курс,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
)


class IntegrationTestTeamAssignments(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.письма = patch("frappe.sendmail").start()
		self.addCleanup(patch.stopall)
		суффикс = frappe.generate_hash(length=6)
		self.компания = создать_организацию(f"Компания {суффикс}")
		self.руководитель = создать_менеджера(f"as-m-{суффикс}@example.com", self.компания)
		self.сотрудник = создать_ученика(f"as-e-{суффикс}@example.com")
		self.коллега = создать_ученика(f"as-c-{суффикс}@example.com")
		for человек in (self.сотрудник, self.коллега):
			добавить_в_организацию(человек, self.компания)
		self.курс = создать_курс(f"Курс {суффикс}")
		frappe.db.set_value("LMS Course", self.курс, "published", 1)

	def от_имени(self, кто: str, метод, **аргументы):
		frappe.set_user(кто)
		try:
			return метод(**аргументы)
		finally:
			frappe.set_user("Administrator")

	def назначить(self, **аргументы) -> str:
		ответ = self.от_имени(
			self.руководитель, team.assign_course, organization=self.компания, course=self.курс, **аргументы
		)
		assert ответ["ok"], ответ
		return ответ["data"]["id"]

	def письма_о(self, вид: str) -> set[str]:
		return set(frappe.get_all("Allocation Notice", filters={"kind": вид}, pluck="user"))

	def зачислен(self, кто: str) -> bool:
		return bool(frappe.db.exists("LMS Enrollment", {"member": кто, "course": self.курс}))

	def test_курс_на_всю_команду_зачисляет_и_пишет_каждому_один_раз(self):
		назначение = self.назначить(deadline="2030-01-01", mandatory=1)

		self.assertTrue(self.зачислен(self.сотрудник) and self.зачислен(self.коллега))
		self.assertTrue({self.сотрудник, self.коллега} <= self.письма_о("assigned"))
		отправлено = self.письма.call_count

		self.от_имени(self.руководитель, team.update_allocation, allocation=назначение, deadline="2030-02-01")

		self.assertEqual(self.письма.call_count, отправлено)

	def test_выбранным_и_дописанному_потом(self):
		назначение = self.назначить(members=[self.сотрудник])
		self.assertFalse(self.зачислен(self.коллега))

		self.от_имени(
			self.руководитель, team.update_allocation, allocation=назначение, members=[self.сотрудник, self.коллега]
		)

		self.assertTrue(self.зачислен(self.коллега))
		self.assertIn(self.коллега, self.письма_о("assigned"))

	def test_новичок_получает_письмо_о_назначенном_раньше(self):
		self.назначить()
		новичок = создать_ученика(f"as-n-{frappe.generate_hash(length=6)}@example.com")

		добавить_в_организацию(новичок, self.компания)

		self.assertIn(новичок, self.письма_о("assigned"))

	def test_курс_взятый_самим_без_писем(self):
		frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.компания,
				"course": self.курс,
				"audience": "Selected Members",
				"members": [{"user": self.сотрудник}],
				"chosen_by_member": 1,
			}
		).insert(ignore_permissions=True)

		self.assertEqual(self.письма.call_count, 0)

	def test_напоминание_о_сроке_непрошедшим_один_раз(self):
		self.назначить(deadline=add_days(nowdate(), 2))
		frappe.db.set_value(
			"LMS Enrollment", {"member": self.коллега, "course": self.курс}, "progress", 100
		)

		notices.напомнить_о_сроках()
		notices.напомнить_о_сроках()

		# Руководитель — тоже участник: курс всей команде назначен и ему.
		напоминания = frappe.get_all("Allocation Notice", filters={"kind": "deadline"}, pluck="user")
		self.assertEqual(sorted(напоминания), sorted([self.сотрудник, self.руководитель]))

	def test_далёкий_и_прошедший_срок_без_напоминания(self):
		self.назначить(deadline=add_days(nowdate(), 30))
		self.назначить(deadline=add_days(nowdate(), -1))

		notices.напомнить_о_сроках()

		self.assertEqual(self.письма_о("deadline"), set())

	def test_снятие_оставляет_зачисление(self):
		назначение = self.назначить()

		self.от_имени(self.руководитель, team.remove_allocation, allocation=назначение)

		self.assertFalse(frappe.db.exists("Course Allocation", назначение))
		self.assertTrue(self.зачислен(self.сотрудник))

	def test_участник_не_назначает_а_закрытый_курс_не_назначить(self):
		ответ = self.от_имени(self.сотрудник, team.assign_course, organization=self.компания, course=self.курс)
		self.assertIn(ответ["error"]["code"], ("team_not_available", "not_allowed"))

		организация = frappe.get_doc("Learning Organization", self.компания)
		организация.append("allowed_courses", {"course": создать_курс(f"Другой {frappe.generate_hash(length=6)}")})
		организация.save(ignore_permissions=True)
		ответ = self.от_имени(
			self.руководитель, team.assign_course, organization=self.компания, course=self.курс
		)
		self.assertEqual(ответ["error"]["code"], "course_not_allowed")

	def test_перечень_назначений_и_доступных_курсов(self):
		self.назначить(members=[self.сотрудник], deadline="2030-01-01")

		ответ = self.от_имени(self.руководитель, team.allocations, organization=self.компания)["data"]

		self.assertEqual(ответ["allocations"][0]["members"], [self.сотрудник])
		self.assertFalse(ответ["allocations"][0]["whole_team"])
		self.assertIn(self.курс, {к["id"] for к in ответ["courses"]})
