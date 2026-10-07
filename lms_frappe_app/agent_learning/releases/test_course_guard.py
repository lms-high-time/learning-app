# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Поля курса, которые ставит публикация релиза: хук `validate` у `LMS Course` (learning-services#500)."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.doctype.agent_course_release.test_agent_course_release import (
	вставить_релиз,
)
from lms_frappe_app.agent_learning.releases import service
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import создать_куратора, создать_курс


class IntegrationTestПоляРелизаУКурса(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"rel-guard-{суффикс}@example.com")
		self.курс = создать_курс(f"Правила {суффикс}")
		# Куратор — преподаватель курса: Learning даёт ему править курс.
		курс = frappe.get_doc("LMS Course", self.курс)
		курс.append("instructors", {"instructor": self.куратор})
		курс.save()

	def test_ключ_курса_правкой_не_ставится(self):
		курс = frappe.get_doc("LMS Course", self.курс)
		курс.course_key = f"hand-{frappe.generate_hash(length=6)}"
		with self.assertRaises(frappe.ValidationError):
			курс.save()

	def test_ключ_курса_из_релиза_правкой_не_меняется(self):
		frappe.set_user(self.куратор)
		ответ = service.опубликовать(
			пример_релиза(f"guard-{frappe.generate_hash(length=6)}"), None, self.куратор
		)
		курс = frappe.get_doc("LMS Course", ответ["course"])
		курс.course_key = "other-key"
		with self.assertRaises(frappe.ValidationError):
			курс.save()
		курс.reload()
		курс.short_introduction = "Правка карточки в desk"
		курс.save()

	def test_действующий_релиз_только_свой(self):
		чужой = вставить_релиз(создать_курс(f"Чужой {frappe.generate_hash(length=6)}"))
		курс = frappe.get_doc("LMS Course", self.курс)
		курс.active_release = чужой
		курс.flags.from_release = True
		with self.assertRaises(frappe.ValidationError):
			курс.save()
