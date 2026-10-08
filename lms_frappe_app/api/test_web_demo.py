# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Лимит пробных уроков в веб-чате (lms-platform#137).

Платформа оплачивает модель только на пробные уроки, поэтому счёт идёт по
**урокам**, а не по занятиям: занятие закрывается по бездействию, и возврат в
тот же урок на следующий день не должен тратить второй пробный урок.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import (
	зачислить,
	политика_по_умолчанию,
	создать_ученика,
	урок_релиза,
	курс_из_релиза,
)
from lms_frappe_app.api import student


class IntegrationTestWebDemo(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(политика_по_умолчанию)
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"web-{суффикс}@example.com")
		# Уроки из разных курсов: лимит общий на учётную запись, а не на курс.
		# Курсы из релиза: `start_lesson` открывает только их (learning-services#506).
		self.уроки = [урок_релиза(курс_из_релиза()[0], "l-1") for _ in range(3)]
		for урок in self.уроки:
			зачислить(self.ученик, урок)
		frappe.set_user(self.ученик)

	def начать(self, урок: str, channel: str = "web") -> dict:
		return student.start_lesson(lesson=урок, channel=channel)

	def test_без_канала_занятие_не_веб(self):
		данные = student.start_lesson(lesson=self.уроки[0])["data"]
		self.assertFalse(frappe.db.get_value("Agent Learning Session", данные["session"], "web_chat"))

	def test_веб_канал_отмечает_занятие(self):
		данные = self.начать(self.уроки[0])["data"]
		self.assertTrue(frappe.db.get_value("Agent Learning Session", данные["session"], "web_chat"))

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

	def test_порог_берётся_из_настроек(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Agent Learning Settings", "web_demo_lessons", 1)
		frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")
		frappe.set_user(self.ученик)
		self.начать(self.уроки[0])

		self.assertEqual(self.начать(self.уроки[1])["error"]["code"], student.ДЕМО_ИСЧЕРПАНО)

	def test_неизвестный_канал_отклоняется(self):
		ответ = self.начать(self.уроки[0], channel="telegram")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.НЕИЗВЕСТНЫЙ_КАНАЛ)
