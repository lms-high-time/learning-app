# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Архив курса: занятия закрыты, данные целы (learning-services#500)."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import access
from lms_frappe_app.api import authoring, student
from lms_frappe_app.tests.sample_data import (
	зачислить,
	политика_по_умолчанию,
	создать_куратора,
	создать_урок,
	создать_ученика,
)


class IntegrationTestАрхивКурса(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"rel-arch-{суффикс}@example.com")
		self.куратор = создать_куратора(f"rel-arch-curator-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(self.ученик, self.урок)
		ответ = authoring.set_course_artifact(
			course=self.курс,
			artifact="notebook",
			title="Тетрадь",
			blocks=[{"key": "log", "title": "Журнал"}],
		)
		self.assertTrue(ответ["ok"], ответ)
		frappe.set_user(self.ученик)
		self.assertTrue(
			student.update_artifact(self.курс, "notebook", "log", content="Записано до архива")["ok"]
		)
		frappe.set_user("Administrator")
		# Как скрипт на стенде: сохранением, чтобы сбросить кэш документа курса.
		курс = frappe.get_doc("LMS Course", self.курс)
		курс.update({"archived": 1, "published": 0})
		курс.save(ignore_permissions=True)

	def test_курса_нет_в_списке_ученика(self):
		frappe.set_user(self.ученик)
		ответ = student.list_my_courses()
		self.assertTrue(ответ["ok"], ответ)
		self.assertNotIn(self.курс, [к["id"] for к in ответ["data"]["courses"]])
		self.assertEqual(access.доступен_курс(self.ученик, self.курс), (False, "course_archived"))

	def test_занятие_не_начинается(self):
		frappe.set_user(self.ученик)
		ответ = student.start_lesson(lesson=self.урок)
		self.assertFalse(ответ["ok"], ответ)
		self.assertEqual(ответ["error"]["code"], "course_archived")

	def test_документ_ученика_читается_но_не_пишется(self):
		frappe.set_user(self.ученик)
		ответ = student.artifact(course=self.курс, artifact="notebook")
		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(ответ["data"]["blocks"][0]["content"], "Записано до архива")
		ответ = student.update_artifact(self.курс, "notebook", "log", content="После архива")
		self.assertEqual(ответ["error"]["code"], "course_archived")

	def test_автор_не_открывает_и_не_анонсирует(self):
		frappe.set_user(self.куратор)
		for метод in (authoring.publish_course, authoring.announce_course):
			ответ = метод(course=self.курс)
			self.assertFalse(ответ["ok"], метод.__name__)
			self.assertEqual(ответ["error"]["code"], "course_archived", метод.__name__)

	def test_снятый_признак_возвращает_доступ(self):
		курс = frappe.get_doc("LMS Course", self.курс)
		курс.archived = 0
		курс.save(ignore_permissions=True)
		self.assertEqual(access.доступен_курс(self.ученик, self.курс), (True, None))
