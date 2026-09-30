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
	создать_домашку,
	создать_занятие,
	создать_организацию,
	создать_ученика,
	создать_урок,
)


def занятие(ученик, урок):
	"""`создать_занятие` отдаёт имя, а `выдать` ждёт документ."""
	return frappe.get_doc("Agent Learning Session", создать_занятие(ученик, урок))


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
		создать_домашку(self.урок, due_mode="relative", due_days=5)
		домашка.выдать(занятие(self.ученик, self.урок))
		[сдача] = self.сдачи()
		self.assertEqual(сдача.status, "Assigned")
		разница = get_datetime(сдача.due_at) - get_datetime(сдача.assigned_at)
		self.assertEqual(разница, timedelta(days=5))

	def test_абсолютный_срок_конец_дня(self):
		создать_домашку(self.урок, due_mode="absolute", due_date="2030-01-15")
		домашка.выдать(занятие(self.ученик, self.урок))
		[сдача] = self.сдачи()
		self.assertEqual(str(сдача.due_at), "2030-01-15 23:59:59")

	def test_повторное_закрытие_ничего_не_меняет(self):
		создать_домашку(self.урок, due_mode="relative", due_days=5)
		запись = занятие(self.ученик, self.урок)
		домашка.выдать(запись)
		[было] = self.сдачи()
		домашка.выдать(запись)
		[стало] = self.сдачи()
		self.assertEqual(было, стало)
		self.assertEqual(len(frappe.get_doc("Agent Homework Submission", стало.name).history), 1)

	def test_правка_правила_не_пересчитывает_выставленный_срок(self):
		"""Ученик не получает просрочку задним числом за правку правила."""
		правило = создать_домашку(self.урок, due_mode="relative", due_days=5)
		запись = занятие(self.ученик, self.урок)
		домашка.выдать(запись)
		[было] = self.сдачи()
		правило.update({"due_mode": "absolute", "due_date": "2020-01-01"})
		правило.save(ignore_permissions=True)
		домашка.выдать(запись)
		домашка.сохранить(self.ученик, self.урок, None, answer="сделал")
		[стало] = self.сдачи()
		self.assertEqual(стало.due_at, было.due_at)

	def test_закрытие_в_другом_пространстве_даёт_свою_сдачу(self):
		создать_домашку(self.урок)
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

		создать_домашку(self.урок, due_mode="relative", due_days=2)
		frappe.set_user(self.ученик)
		старт = student.start_lesson(lesson=self.урок)["data"]
		сдать_отчёт(старт["session"])
		ответ = student.complete_lesson(старт["session"])
		self.assertTrue(ответ["ok"], ответ)
		frappe.set_user("Administrator")
		[сдача] = self.сдачи()
		self.assertEqual(сдача.status, "Assigned")

	def test_попытка_квиза_выдаёт_в_пространстве_своего_занятия(self):
		создать_домашку(self.урок)
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
			создать_домашку(self.урок, due_mode="relative")
		self.assertEqual(отказ.exception.код, "invalid_due")
		with self.assertRaises(Отказ) as отказ:
			создать_домашку(self.урок, answer_mode="audio")
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
		создать_домашку(self.урок)
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
		создать_домашку(self.урок)
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, answer="   ")
		self.assertEqual(отказ.exception.код, "empty_answer")

	def test_текст_в_задание_только_файлами(self):
		создать_домашку(self.урок, answer_mode="files")
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, answer="текст")
		self.assertEqual(отказ.exception.код, "answer_mode")

	def test_файл_в_задание_только_текстом(self):
		создать_домашку(self.урок, answer_mode="text")
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, новые=[("a.txt", b"one")])
		self.assertEqual(отказ.exception.код, "answer_mode")

	def test_неполный_ответ_принимается(self):
		"""Оценки нет: `text_and_files` без файлов видит и возвращает куратор."""
		создать_домашку(self.урок)
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="только текст")
		self.assertEqual(документ.status, "Submitted")

	def test_файлы_сохраняются_и_остаются_в_старой_версии(self):
		создать_домашку(self.урок)
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

	def _отклонить_второй_файл(self):
		"""Вставка файла, которая принимает первый файл и отклоняет второй — как
		Frappe отклоняет запрещённый тип."""
		from unittest.mock import patch

		настоящая = домашка._вложить_файл
		вызовы = []

		def вложить(документ, имя, данные):
			вызовы.append(имя)
			if len(вызовы) > 1:
				raise frappe.ValidationError("Тип файла не разрешён")
			return настоящая(документ, имя, данные)

		return patch.object(домашка, "_вложить_файл", side_effect=вложить)

	def test_отклонённый_файл_не_оставляет_новой_сдачи(self):
		создать_домашку(self.урок)
		файлов = frappe.db.count("File", {"attached_to_doctype": "Agent Homework Submission"})
		with self._отклонить_второй_файл(), self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, новые=[("a.txt", b"one"), ("b.exe", b"two")])
		self.assertEqual(отказ.exception.код, "file_rejected")
		self.assertEqual(отказ.exception.подробности["file"], "b.exe")
		self.assertFalse(frappe.db.exists("Agent Homework Submission", {"member": self.ученик}))
		self.assertEqual(frappe.db.count("File", {"attached_to_doctype": "Agent Homework Submission"}), файлов)

	def test_отклонённый_файл_не_меняет_существующую_сдачу(self):
		создать_домашку(self.урок)
		было = домашка.сохранить(self.ученик, self.урок, None, answer="первая", новые=[("a.txt", b"one")])
		файлы_было = set(frappe.get_all("File", filters={"attached_to_name": было.name}, pluck="name"))
		with self._отклонить_второй_файл(), self.assertRaises(домашка.Отказ):
			домашка.сохранить(self.ученик, self.урок, None, answer="вторая", новые=[("b.txt", b"two"), ("c.exe", b"x")])
		стало = frappe.get_doc("Agent Homework Submission", было.name)
		self.assertEqual((стало.version, стало.answer, len(стало.versions)), (1, "первая", 1))
		self.assertEqual([с.file for с in стало.files], [с.file for с in было.files])
		self.assertEqual(set(frappe.get_all("File", filters={"attached_to_name": было.name}, pluck="name")), файлы_было)

	def test_предел_числа_файлов(self):
		создать_домашку(self.урок)
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, новые=[(f"{н}.txt", b"x") for н in range(11)])
		self.assertEqual(отказ.exception.код, "too_many_files")

	def test_предел_объёма_сохранения(self):
		создать_домашку(self.урок)
		# Размер файла пропускает оба, сумма — нет: отказ именно за объём сохранения.
		было = frappe.db.get_single_value("Agent Learning Settings", "artifact_file_max_mb")
		frappe.db.set_single_value("Agent Learning Settings", "artifact_file_max_mb", 20)
		frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")
		self.addCleanup(frappe.clear_document_cache, "Agent Learning Settings", "Agent Learning Settings")
		self.addCleanup(frappe.db.set_single_value, "Agent Learning Settings", "artifact_file_max_mb", было)
		большой = b"x" * (16 * 1024 * 1024)
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, новые=[("a.bin", большой), ("b.bin", большой)])
		self.assertEqual(отказ.exception.код, "answer_too_large")

	def test_принятую_не_правят(self):
		создать_домашку(self.урок)
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="готово")
		frappe.db.set_value("Agent Homework Submission", документ.name, "status", "Accepted")
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, answer="ещё")
		self.assertEqual(отказ.exception.код, "accepted_locked")

	def test_сохранение_в_возвращённой_снова_сдана(self):
		создать_домашку(self.урок)
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="готово")
		frappe.db.set_value("Agent Homework Submission", документ.name, "status", "Returned")
		self.assertEqual(домашка.сохранить(self.ученик, self.урок, None, answer="доделал").status, "Submitted")

	def test_сдача_до_закрытия_получает_выдачу_и_срок(self):
		создать_домашку(self.урок, due_mode="relative", due_days=3)
		домашка.сохранить(self.ученик, self.урок, None, answer="Черновик мыслей")
		домашка.выдать(занятие(self.ученик, self.урок))
		[сдача] = frappe.get_all(
			"Agent Homework Submission", filters={"member": self.ученик}, fields=["status", "assigned_at", "due_at"]
		)
		self.assertEqual(сдача.status, "Submitted")
		self.assertIsNotNone(сдача.assigned_at)
		self.assertIsNotNone(сдача.due_at)

	def test_абсолютный_срок_ставится_при_создании(self):
		создать_домашку(self.урок, due_mode="absolute", due_date="2030-01-15")
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="рано")
		self.assertEqual(str(документ.due_at), "2030-01-15 23:59:59")
		self.assertIsNone(документ.assigned_at)

	def test_сдача_без_выдачи_живёт_без_относительного_срока(self):
		"""Задание появилось у уже закрытого урока: выдачи задним числом нет."""
		создать_домашку(self.урок, due_mode="relative", due_days=3)
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="сам нашёл")
		self.assertIsNone(документ.due_at)
		self.assertIsNone(документ.assigned_at)
