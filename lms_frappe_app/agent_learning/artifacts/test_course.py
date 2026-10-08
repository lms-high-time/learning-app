# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Запись схемы документа курса (`artifacts.course.записать_схему`) — тем же путём, что у релиза."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import создать_урок, схема_документа

БЛОКИ = [{"key": "goal", "title": "Цель"}]


class IntegrationTestЗаписьСхемы(IntegrationTestCase):
	def setUp(self):
		self.урок = создать_урок(f"Урок {frappe.generate_hash(length=6)}")
		self.курс = frappe.db.get_value("Course Lesson", self.урок, "course")

	def действующая(self, ключ: str = "plan"):
		return frappe.db.get_value(
			"Agent Course Artifact",
			{"course": self.курс, "slug": ключ, "is_active": 1},
			["name", "slug", "version", "purpose"],
			as_dict=True,
		)

	def test_урок_блока_не_найден_отказ_до_записи(self):
		ответ = схема_документа(self.курс, "plan", "План", [{**БЛОКИ[0], "lesson": "нет-такого-урока"}])

		self.assertEqual(
			(ответ["error"]["code"], ответ["error"]["id"]), ("lesson_not_found", "нет-такого-урока")
		)
		self.assertFalse(frappe.db.exists("Agent Course Artifact", {"course": self.курс}))

	def test_ключ_документа_приводится_к_нижнему_регистру(self):
		"""Ключ без пробелов по краям и в нижнем регистре: `Plan` и `plan` — один документ."""
		первая = схема_документа(self.курс, "  Plan ", "План", БЛОКИ)["data"]
		вторая = схема_документа(self.курс, "PLAN", "План", БЛОКИ)["data"]

		self.assertEqual((первая["artifact"], вторая["artifact"]), ("plan", "plan"))
		self.assertEqual(вторая["version"], 2)
		self.assertEqual(self.действующая().name, вторая["id"])

	def test_назначение_без_нового_остаётся_прежним(self):
		"""`purpose=None` — назначение прежней версии; пустая строка его убирает."""
		схема_документа(self.курс, "plan", "План", БЛОКИ, purpose="  Зачем ученику план  ")
		self.assertEqual(self.действующая().purpose, "Зачем ученику план")

		схема_документа(self.курс, "plan", "План", БЛОКИ)
		self.assertEqual((self.действующая().version, self.действующая().purpose), (2, "Зачем ученику план"))

		схема_документа(self.курс, "plan", "План", БЛОКИ, purpose="")
		self.assertEqual((self.действующая().version, self.действующая().purpose), (3, None))
