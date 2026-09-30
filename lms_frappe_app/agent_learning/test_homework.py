# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import json
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

	def test_complete_lesson_выдаёт_домашку(self):
		from lms_frappe_app.api import student
		from lms_frappe_app.tests.sample_data import сдать_отчёт

		задание(self.урок, due_mode="relative", due_days=2)
		frappe.set_user(self.ученик)
		старт = student.start_lesson(lesson=self.урок)["data"]
		сдать_отчёт(старт["session"])
		ответ = student.complete_lesson(старт["session"])
		self.assertTrue(ответ["ok"], ответ)
		frappe.set_user("Administrator")
		[сдача] = self.сдачи()
		self.assertEqual(сдача.status, "Assigned")

	def test_попытка_квиза_выдаёт_в_пространстве_своего_занятия(self):
		задание(self.урок)
		организация = создать_организацию(f"Орг {frappe.generate_hash(length=4)}")
		добавить_в_организацию(self.ученик, организация)
		имя = создать_занятие(self.ученик, self.урок)
		frappe.db.set_value("Agent Learning Session", имя, "organization", организация)
		попытка = frappe._dict(
			doctype="Agent Quiz Attempt", session=имя, lesson=self.урок, student=self.ученик, course=self.курс
		)
		домашка.выдать(попытка)
		self.assertEqual([с.organization for с in self.сдачи()], [организация])

	def test_задание_проверяет_правило_срока(self):
		from lms_frappe_app.agent_learning.errors import Отказ

		with self.assertRaises(Отказ) as отказ:
			задание(self.урок, due_mode="relative")
		self.assertEqual(отказ.exception.код, "invalid_due")
		with self.assertRaises(Отказ) as отказ:
			задание(self.урок, answer_mode="audio")
		self.assertEqual(отказ.exception.код, "invalid_answer_mode")


class IntegrationTestHomeworkSave(IntegrationTestCase):
	"""Сохранение ответа: версия на каждое сохранение, журнал, файлы (learning-services#439)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"hws-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(self.ученик, self.урок)

	def test_каждое_сохранение_новая_версия(self):
		задание(self.урок)
		домашка.сохранить(self.ученик, self.урок, None, answer="первая")
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="вторая")
		self.assertEqual(документ.status, "Submitted")
		self.assertEqual(документ.version, 2)
		self.assertEqual([в.answer for в in документ.versions], ["первая", "вторая"])
		self.assertEqual([с.event for с in документ.history], ["submitted", "submitted"])

	def test_без_задания_отказ(self):
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, answer="сделал")
		self.assertEqual(отказ.exception.код, "no_homework")

	def test_пустой_ответ_отклоняется(self):
		задание(self.урок)
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, answer="   ")
		self.assertEqual(отказ.exception.код, "empty_answer")

	def test_текст_в_задание_только_файлами(self):
		задание(self.урок, answer_mode="files")
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, answer="текст")
		self.assertEqual(отказ.exception.код, "answer_mode")

	def test_файл_в_задание_только_текстом(self):
		задание(self.урок, answer_mode="text")
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, новые=[("a.txt", b"one")])
		self.assertEqual(отказ.exception.код, "answer_mode")

	def test_неполный_ответ_принимается(self):
		"""Оценки нет: `text_and_files` без файлов видит и возвращает куратор."""
		задание(self.урок)
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="только текст")
		self.assertEqual(документ.status, "Submitted")

	def test_файлы_сохраняются_и_остаются_в_старой_версии(self):
		задание(self.урок)
		первая = домашка.сохранить(self.ученик, self.урок, None, новые=[("a.txt", b"one")])
		[файл] = [с.file for с in первая.files]
		вторая = домашка.сохранить(self.ученик, self.урок, None, answer="без файла", убрать=[файл])
		self.assertEqual(list(вторая.files), [])
		self.assertEqual(json.loads(вторая.versions[0].files), [файл])
		self.assertTrue(frappe.db.exists("File", файл))
		self.assertEqual(
			frappe.db.get_value("File", файл, ["attached_to_doctype", "is_private"]),
			("Agent Homework Submission", 1),
		)

	def test_предел_числа_файлов(self):
		задание(self.урок)
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, новые=[(f"{н}.txt", b"x") for н in range(11)])
		self.assertEqual(отказ.exception.код, "too_many_files")

	def test_предел_объёма_сохранения(self):
		задание(self.урок)
		# Размер файла пропускает оба, сумма — нет: отказ именно за объём сохранения.
		frappe.db.set_single_value("Agent Learning Settings", "artifact_file_max_mb", 20)
		frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")
		self.addCleanup(frappe.clear_document_cache, "Agent Learning Settings", "Agent Learning Settings")
		self.addCleanup(frappe.db.set_single_value, "Agent Learning Settings", "artifact_file_max_mb", 10)
		большой = b"x" * (16 * 1024 * 1024)
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, новые=[("a.bin", большой), ("b.bin", большой)])
		self.assertEqual(отказ.exception.код, "answer_too_large")

	def test_принятую_не_правят(self):
		задание(self.урок)
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="готово")
		frappe.db.set_value("Agent Homework Submission", документ.name, "status", "Accepted")
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, answer="ещё")
		self.assertEqual(отказ.exception.код, "accepted_locked")

	def test_сохранение_в_возвращённой_снова_сдана(self):
		задание(self.урок)
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="готово")
		frappe.db.set_value("Agent Homework Submission", документ.name, "status", "Returned")
		self.assertEqual(домашка.сохранить(self.ученик, self.урок, None, answer="доделал").status, "Submitted")

	def test_сдача_до_закрытия_получает_выдачу_и_срок(self):
		задание(self.урок, due_mode="relative", due_days=3)
		домашка.сохранить(self.ученик, self.урок, None, answer="Черновик мыслей")
		домашка.выдать(занятие(self.ученик, self.урок))
		[сдача] = frappe.get_all(
			"Agent Homework Submission", filters={"member": self.ученик}, fields=["status", "assigned_at", "due_at"]
		)
		self.assertEqual(сдача.status, "Submitted")
		self.assertIsNotNone(сдача.assigned_at)
		self.assertIsNotNone(сдача.due_at)

	def test_абсолютный_срок_ставится_при_создании(self):
		задание(self.урок, due_mode="absolute", due_date="2030-01-15")
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="рано")
		self.assertEqual(str(документ.due_at), "2030-01-15 23:59:59")
		self.assertIsNone(документ.assigned_at)

	def test_сдача_без_выдачи_живёт_без_относительного_срока(self):
		"""Задание появилось у уже закрытого урока: выдачи задним числом нет."""
		задание(self.урок, due_mode="relative", due_days=3)
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="сам нашёл")
		self.assertIsNone(документ.due_at)
		self.assertIsNone(документ.assigned_at)
