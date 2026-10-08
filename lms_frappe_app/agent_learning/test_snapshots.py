# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Снимки уроков — «как было» для отметок «изменено» в кабинете автора.

`Why:` разница честна, только если снимок берёт ровно тот текст, что видит
автор на странице урока: материал, поле директивы, вопрос с вариантами, блок
документа (lms-high-time/learning-services#271).
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.snapshots import снимок_урока
from lms_frappe_app.api import authoring
from lms_frappe_app.tests.sample_data import создать_куратора


class IntegrationTestSnapshots(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		frappe.set_user(создать_куратора(f"snap-{суффикс}@example.com"))
		self.курс = authoring.create_course(title=f"Снимки {суффикс}", summary="к")["data"]["id"]
		глава = authoring.add_chapter(course=self.курс, title="Рамка")["data"]["id"]
		self.урок = authoring.add_lesson(chapter=глава, title="Первый", body="# Первый\n\nТекст.")["data"]["id"]
		authoring.set_directive(lesson=self.урок, teaching_directive="Веди", objectives="Цель один\nЦель два")
		authoring.add_quiz(
			lesson=self.урок,
			questions=[
				{
					"text": "Что не так?",
					"options": [{"text": "a", "correct": True, "explanation": "потому"}, {"text": "b"}],
				}
			],
		)
		self.вопрос = authoring.get_lesson(lesson=self.урок)["data"]["quiz"]["questions"][0]["id"]
		authoring.set_course_artifact(
			course=self.курс,
			artifact="register",
			title="Реестр",
			blocks=[{"key": "risks", "title": "Риски", "hint": "Пять записей", "lesson": self.урок}],
		)

	def test_снимок_урока_собирает_его_места(self):
		места = снимок_урока(self.курс, self.урок)["places"]

		self.assertEqual(
			list(места),
			[
				"material",
				"directive.teaching_directive",
				"directive.objectives",
				f"question.{self.вопрос}",
				"block.register/risks",
			],
		)
		self.assertEqual(места["material"], {"text": "# Первый\n\nТекст.", "mode": "text"})
		self.assertEqual(места["directive.teaching_directive"], {"text": "Веди", "mode": "text"})
		self.assertEqual(места["directive.objectives"], {"text": "Цель один\nЦель два", "mode": "lines"})
		self.assertEqual(места[f"question.{self.вопрос}"]["text"].split("\n"), ["Что не так?", "✓ a — потому", "· b"])
		self.assertEqual(места["block.register/risks"], {"text": "Риски\n\nПять записей", "mode": "text"})

	def test_у_пропавшего_урока_снимка_нет(self):
		self.assertIsNone(снимок_урока(self.курс, "урок-которого-нет"))
