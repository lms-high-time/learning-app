# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Фикстуры курса из релиза для тестов ученика (learning-services#506)."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.releases import index
from lms_frappe_app.tests.release_sample import релиз_двух_целей
from lms_frappe_app.tests.sample_data import (
	зачислить_на_курс,
	занятие_релиза,
	курс_из_релиза,
	отметить_все_пункты,
	создать_куратора,
	создать_ученика,
	урок_релиза,
)


class IntegrationTestФикстурыРелиза(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"fx-rel-pupil-{суффикс}@example.com")

	def test_курс_из_образца_с_новым_ключом(self):
		курс, релиз = курс_из_релиза()
		другой, _ = курс_из_релиза()

		self.assertNotEqual(курс, другой)
		self.assertEqual(frappe.db.get_value("LMS Course", курс, "active_release"), релиз)
		self.assertEqual(index.ключи(релиз)["lessons"], ["l-1", "l-2", "l-3"])

	def test_курс_от_автора_и_пользователь_прежний(self):
		куратор = создать_куратора(f"fx-rel-cur-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(self.ученик)

		курс, релиз = курс_из_релиза(куратор, релиз=релиз_двух_целей(f"fx-{frappe.generate_hash(length=6)}"))

		self.assertEqual(frappe.session.user, self.ученик)
		self.assertEqual(frappe.db.get_value(index.РЕЛИЗ, релиз, "published_by"), куратор)
		self.assertEqual(index.ключи(релиз)["lessons"], ["l-1"])

	def test_урок_по_ключу_и_зачисление(self):
		курс, релиз = курс_из_релиза()

		урок = урок_релиза(курс, "l-2")
		зачислить_на_курс(self.ученик, курс)
		зачислить_на_курс(self.ученик, курс)

		self.assertEqual(урок, index.урок(релиз, "l-2")["lesson"])
		self.assertEqual(frappe.db.count("LMS Enrollment", {"member": self.ученик, "course": курс}), 1)

	def test_занятие_по_прохождению(self):
		курс, _ = курс_из_релиза()

		занятие = frappe.get_doc("Agent Learning Session", занятие_релиза(self.ученик, курс, "l-1"))

		run = frappe.get_doc("Agent Lesson Run", занятие.run)
		self.assertEqual((run.student, run.course, run.lesson_key), (self.ученик, курс, "l-1"))
		self.assertEqual((занятие.student, занятие.course, занятие.lesson), (self.ученик, курс, run.lesson))

	def test_все_обязательные_пункты_отмечены(self):
		курс, _ = курс_из_релиза()
		run = frappe.db.get_value("Agent Learning Session", занятие_релиза(self.ученик, курс, "l-1"), "run")

		ответ = отметить_все_пункты(run)

		self.assertEqual(ответ["lesson"]["status"], "covered")
		self.assertIsNone(ответ["next"])
		пункты = {п.goal_key: п.status for п in frappe.get_doc("Agent Lesson Run", run).goals}
		self.assertEqual(пункты, {"term:T1": "done", "l-1-D1/V1": "done", "refute:M1": "open"})
		self.assertIsNone(отметить_все_пункты(run))
