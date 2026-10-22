# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Лимит пробных уроков в веб-чате (lms-platform#137).

Платформа оплачивает модель только на пробные уроки, поэтому счёт идёт по
**урокам**, а не по занятиям: занятие закрывается по бездействию, и возврат в
тот же урок на следующий день не должен тратить второй пробный урок.
Ходы внутри урока считаются отдельно — по прохождению урока за сутки.
"""

from datetime import timedelta

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from lms_frappe_app.tests.sample_data import (
	зачислить,
	политика_по_умолчанию,
	создать_ученика,
	урок_релиза,
	курс_из_релиза,
)
from lms_frappe_app.api import student


class IntegrationTestWebDemo(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		"""Курсы тесты только читают; занятия пишет ученик своего теста."""
		super().setUpClass()
		frappe.set_user("Administrator")
		# Уроки из разных курсов: лимит общий на учётную запись, а не на курс.
		# Курсы из релиза: `start_lesson` открывает только их (learning-services#506).
		cls.уроки = [урок_релиза(курс_из_релиза()[0], "l-1") for _ in range(3)]

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(политика_по_умолчанию)
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"web-{суффикс}@example.com")
		for урок in self.уроки:
			зачислить(self.ученик, урок)
		frappe.set_user(self.ученик)

	def начать(self, урок: str, channel: str = "web") -> dict:
		return student.start_lesson(lesson=урок, channel=channel)

	def test_без_канала_занятие_не_веб(self):
		данные = student.start_lesson(lesson=self.уроки[0])["data"]
		self.assertFalse(frappe.db.get_value("Agent Learning Session", данные["session"], "web_chat"))

	def test_урок_сверх_лимита_отклоняется(self):
		self.начать(self.уроки[0])
		self.начать(self.уроки[1])

		ответ = self.начать(self.уроки[2])

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ДЕМО_ИСЧЕРПАНО)
		self.assertEqual(ответ["error"]["lessons_used"], 2)
		self.assertEqual(ответ["error"]["lessons_limit"], 2)
		self.assertFalse(
			frappe.db.exists(
				"Agent Learning Session", {"student": self.ученик, "lesson": self.уроки[2]}
			),
			"отказ не должен заводить занятие",
		)

	def test_возврат_в_урок_из_счёта_бесплатен_после_закрытия(self):
		первое = self.начать(self.уроки[0])["data"]["session"]
		self.начать(self.уроки[1])
		frappe.db.set_value("Agent Learning Session", первое, "status", "Abandoned")

		ответ = self.начать(self.уроки[0])

		self.assertTrue(ответ["ok"], ответ.get("error"))

	def test_продолжение_урока_агента_в_вебе_тратит_демо(self):
		self.начать(self.уроки[0])
		занятие = self.начать(self.уроки[1], channel="agent")["data"]["session"]

		продолжение = self.начать(self.уроки[1])

		self.assertTrue(продолжение["ok"], продолжение.get("error"))
		self.assertEqual(продолжение["data"]["session"], занятие)
		self.assertEqual(self.начать(self.уроки[2])["error"]["code"], student.ДЕМО_ИСЧЕРПАНО)

	def test_свой_агент_лимитом_не_ограничен(self):
		self.начать(self.уроки[0])
		self.начать(self.уроки[1])

		ответ = self.начать(self.уроки[2], channel="agent")

		self.assertTrue(ответ["ok"], ответ.get("error"))

	def test_ходы_урока_считаются_по_прохождению_за_сутки(self):
		"""Счёт идёт по уроку через все его занятия: новое занятие того же урока
		продолжает счёт, у другого урока он свой, а через сутки начинается
		заново (learning-services#675)."""
		первое = self.начать(self.уроки[0])["data"]["session"]
		with self.subTest("ходы занятия"):
			self.assertEqual(
				student.count_lesson_turn(первое)["data"],
				{"session": первое, "lesson": self.уроки[0], "turns": 1},
			)
			self.assertEqual(student.count_lesson_turn(первое)["data"]["turns"], 2)

		frappe.db.set_value("Agent Learning Session", первое, "status", "Abandoned")
		второе = self.начать(self.уроки[0])["data"]["session"]
		with self.subTest("новое занятие того же урока"):
			self.assertNotEqual(второе, первое)
			self.assertEqual(student.count_lesson_turn(второе)["data"]["turns"], 3)

		with self.subTest("другой урок"):
			другое = self.начать(self.уроки[1])["data"]["session"]
			self.assertEqual(student.count_lesson_turn(другое)["data"]["turns"], 1)

		run = frappe.db.get_value("Agent Learning Session", второе, "run")
		with self.subTest("внутри суток"):
			frappe.db.set_value(
				"Agent Lesson Run",
				run,
				"web_turns_since",
				now_datetime() - timedelta(hours=23, minutes=59),
				update_modified=False,
			)
			self.assertEqual(student.count_lesson_turn(второе)["data"]["turns"], 4)

		with self.subTest("через сутки"):
			frappe.db.set_value(
				"Agent Lesson Run",
				run,
				"web_turns_since",
				now_datetime() - timedelta(days=1, minutes=1),
				update_modified=False,
			)
			self.assertEqual(student.count_lesson_turn(второе)["data"]["turns"], 1)
			self.assertEqual(student.count_lesson_turn(первое)["data"]["turns"], 2)
