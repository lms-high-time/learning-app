# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Тестеры курса: доступ к курсу до публикации из кабинета автора (#393)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import notices
from lms_frappe_app.agent_learning.access import доступен_курс
from lms_frappe_app.api import authoring, public
from lms_frappe_app.tests.sample_data import (
	зачислить,
	создать_куратора,
	создать_урок,
	создать_ученика,
)


class IntegrationTestCourseTesters(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.письма = patch("frappe.sendmail").start()
		patch.object(notices, "почта_есть", return_value=True).start()
		self.addCleanup(patch.stopall)
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"ts-c-{суффикс}@example.com")
		self.тестер = создать_ученика(f"ts-t-{суффикс}@example.com")
		self.второй = создать_ученика(f"ts-u-{суффикс}@example.com")
		self.урок = создать_урок(f"Тестеры {суффикс}")
		глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		self.курс = frappe.db.get_value("Course Chapter", глава, "course")

	def от_имени(self, кто: str, метод, **аргументы) -> dict:
		frappe.set_user(кто)
		try:
			return метод(**аргументы)
		finally:
			frappe.set_user("Administrator")

	def добавить(self, users) -> dict:
		return self.от_имени(self.куратор, authoring.add_testers, course=self.курс, users=users)

	def test_тестер_занимается_неопубликованным_курсом(self):
		self.assertFalse(frappe.db.get_value("LMS Course", self.курс, "published"))
		self.assertFalse(доступен_курс(self.тестер, self.курс)[0])

		данные = self.добавить(self.тестер)["data"]

		self.assertEqual(данные["added"], [{"user": self.тестер, "notified": True}])
		self.assertTrue(доступен_курс(self.тестер, self.курс)[0])
		self.assertEqual([т["user"] for т in данные["testers"]], [self.тестер])
		self.assertEqual(self.письма.call_args.kwargs["recipients"], [self.тестер])

	def test_адреса_текстом_без_повторов_и_с_причинами(self):
		обычный = создать_ученика(f"ts-e-{frappe.generate_hash(length=6)}@example.com")
		зачислить(обычный, self.урок)
		данные = self.добавить(
			f"{self.тестер.upper()}, {self.второй}\nnobody@example.com; {self.тестер} {обычный}"
		)["data"]

		self.assertEqual(sorted(д["user"] for д in данные["added"]), sorted([self.тестер, self.второй]))
		self.assertEqual(
			{п["user"]: п["reason"] for п in данные["skipped"]},
			{"nobody@example.com": "user_not_found", обычный: "already_enrolled"},
		)
		повтор = self.добавить([self.тестер])["data"]
		self.assertEqual(повтор["skipped"], [{"user": self.тестер, "reason": "already_tester"}])

	def test_без_адресов_отказ(self):
		ответ = self.добавить("  ,  ")
		self.assertEqual(ответ["error"]["code"], authoring.НЕТ_АДРЕСОВ)

	def test_без_почты_доступ_есть_а_письма_нет(self):
		with patch.object(notices, "почта_есть", return_value=False):
			данные = self.добавить(self.тестер)["data"]
		self.assertEqual(данные["added"], [{"user": self.тестер, "notified": False}])
		self.письма.assert_not_called()

	def test_страница_курса_знает_тестера(self):
		self.добавить(self.тестер)
		карта = self.от_имени(self.тестер, public.course_map, course=self.курс)["data"]
		self.assertTrue(карта["tester"])

		frappe.db.set_value("LMS Course", self.курс, "published", 1)
		зачислить(self.второй, self.урок)
		карта = self.от_имени(self.второй, public.course_map, course=self.курс)["data"]
		self.assertNotIn("tester", карта)

	def test_закрыть_доступ_тестеру(self):
		self.добавить(self.тестер)
		данные = self.от_имени(
			self.куратор, authoring.remove_tester, course=self.курс, user=self.тестер
		)["data"]
		self.assertEqual(данные["testers"], [])
		self.assertFalse(frappe.db.exists("LMS Enrollment", {"member": self.тестер, "course": self.курс}))
		self.assertFalse(доступен_курс(self.тестер, self.курс)[0])

	def test_ученика_не_тестера_не_убрать(self):
		зачислить(self.второй, self.урок)
		ответ = self.от_имени(self.куратор, authoring.remove_tester, course=self.курс, user=self.второй)
		self.assertEqual(ответ["error"]["code"], authoring.ТЕСТЕР_НЕ_НАЙДЕН)
		self.assertTrue(frappe.db.exists("LMS Enrollment", {"member": self.второй, "course": self.курс}))

	def test_ученик_тестеров_не_назначает(self):
		with self.assertRaises(frappe.PermissionError):
			self.от_имени(self.тестер, authoring.add_testers, course=self.курс, users=self.второй)

	def test_кабинет_показывает_тестеров(self):
		from lms_frappe_app.www.author import сведения

		self.добавить(self.тестер)
		frappe.set_user(self.куратор)
		с = сведения(self.куратор, course=self.курс, view="testers")
		self.assertEqual(с["view"], "testers")
		self.assertEqual([т["user"] for т in с["testers"]], [self.тестер])
		self.assertEqual(с["course"]["testers_count"], 1)
