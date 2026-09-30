# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

from datetime import timedelta

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_datetime

from lms_frappe_app.agent_learning import homework as домашка
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	создать_занятие,
	создать_организацию,
	создать_ученика,
	создать_урок,
)


def занятие(ученик, урок):
	"""`создать_занятие` отдаёт имя, а `выдать` ждёт документ."""
	return frappe.get_doc("Agent Learning Session", создать_занятие(ученик, урок))


def задание(урок, **поля):
	return frappe.get_doc(
		{
			"doctype": "Agent Lesson Homework",
			"lesson": урок,
			"title": "Встреча со спонсором",
			"description": "Проведите встречу и опишите итог.",
			**поля,
		}
	).insert(ignore_permissions=True)


class IntegrationTestHomeworkIssue(IntegrationTestCase):
	"""Выдача домашки при закрытии урока (learning-services#439)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"hw-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(self.ученик, self.урок)

	def сдачи(self):
		return frappe.get_all(
			"Agent Homework Submission",
			filters={"member": self.ученик},
			fields=["name", "status", "organization", "due_at", "assigned_at"],
		)

	def test_урок_без_задания_ничего_не_выдаёт(self):
		домашка.выдать(занятие(self.ученик, self.урок))
		self.assertEqual(self.сдачи(), [])

	def test_выдача_ставит_относительный_срок_от_закрытия(self):
		задание(self.урок, due_mode="relative", due_days=5)
		домашка.выдать(занятие(self.ученик, self.урок))
		[сдача] = self.сдачи()
		self.assertEqual(сдача.status, "Assigned")
		разница = get_datetime(сдача.due_at) - get_datetime(сдача.assigned_at)
		self.assertEqual(разница, timedelta(days=5))

	def test_абсолютный_срок_конец_дня(self):
		задание(self.урок, due_mode="absolute", due_date="2030-01-15")
		домашка.выдать(занятие(self.ученик, self.урок))
		[сдача] = self.сдачи()
		self.assertEqual(str(сдача.due_at), "2030-01-15 23:59:59")

	def test_повторное_закрытие_ничего_не_меняет(self):
		задание(self.урок, due_mode="relative", due_days=5)
		запись = занятие(self.ученик, self.урок)
		домашка.выдать(запись)
		[было] = self.сдачи()
		домашка.выдать(запись)
		[стало] = self.сдачи()
		self.assertEqual(было, стало)
		self.assertEqual(len(frappe.get_doc("Agent Homework Submission", стало.name).history), 1)

	def test_закрытие_в_другом_пространстве_даёт_свою_сдачу(self):
		задание(self.урок)
		домашка.выдать(занятие(self.ученик, self.урок))
		организация = создать_организацию(f"Орг {frappe.generate_hash(length=4)}")
		добавить_в_организацию(self.ученик, организация)
		имя = создать_занятие(self.ученик, self.урок)
		frappe.db.set_value("Agent Learning Session", имя, "organization", организация)
		домашка.выдать(frappe.get_doc("Agent Learning Session", имя))
		self.assertEqual({с.organization for с in self.сдачи()}, {None, организация})

	def test_задание_проверяет_правило_срока(self):
		from lms_frappe_app.agent_learning.errors import Отказ

		with self.assertRaises(Отказ) as отказ:
			задание(self.урок, due_mode="relative")
		self.assertEqual(отказ.exception.код, "invalid_due")
		with self.assertRaises(Отказ) as отказ:
			задание(self.урок, answer_mode="audio")
		self.assertEqual(отказ.exception.код, "invalid_answer_mode")
