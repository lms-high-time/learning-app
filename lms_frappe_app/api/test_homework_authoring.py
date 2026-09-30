# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import homework as домашка
from lms_frappe_app.api import authoring
from lms_frappe_app.tests.sample_data import создать_куратора, создать_ученика


class IntegrationTestHomeworkAuthoring(IntegrationTestCase):
	"""Задание урока в авторинге (learning-services#439)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		# Удаление урока Frappe Learning разрешает только модератору.
		self.куратор = создать_куратора(f"hwau-{суффикс}@example.com", роль="Moderator")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Домашки {суффикс}", summary="к")["data"]["id"]
		self.глава = authoring.add_chapter(course=self.курс, title="Глава")["data"]["id"]
		self.урок = authoring.add_lesson(chapter=self.глава, title="Урок", body="# Текст")["data"]["id"]

	def добавить(self, **поля) -> dict:
		return authoring.add_homework(
			lesson=self.урок,
			title="Встреча со спонсором",
			description="Проведите встречу и опишите итог.",
			**поля,
		)

	def ревизия(self) -> str:
		return authoring.course_revision(course=self.курс)["data"]["revision"]

	def test_задание_видно_в_уроке_и_черновике(self):
		ответ = self.добавить(due_mode="relative", due_days=5)
		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(ответ["data"]["answer_mode"], "text_and_files")
		self.assertEqual(ответ["data"]["due_days"], 5)
		self.assertIsNone(ответ["data"]["due_date"])
		урок = authoring.get_lesson(lesson=self.урок)["data"]
		self.assertEqual(урок["homework"]["title"], "Встреча со спонсором")
		self.assertEqual(урок["homework"]["id"], ответ["data"]["id"])
		[строка] = authoring.course_draft(course=self.курс)["data"]["chapters"][0]["lessons"]
		self.assertEqual(
			строка["homework"],
			{"title": "Встреча со спонсором", "answer_mode": "text_and_files", "due_mode": "relative"},
		)

	def test_урок_без_задания(self):
		self.assertIsNone(authoring.get_lesson(lesson=self.урок)["data"]["homework"])
		[строка] = authoring.course_draft(course=self.курс)["data"]["chapters"][0]["lessons"]
		self.assertIsNone(строка["homework"])

	def test_второе_задание_на_урок_отказ(self):
		self.добавить()
		self.assertEqual(self.добавить()["error"]["code"], "homework_exists")

	def test_неизвестный_урок_отказ(self):
		ответ = authoring.add_homework(lesson="нет-такого", title="т", description="о")
		self.assertEqual(ответ["error"]["code"], "lesson_not_found")

	def test_правка_не_затирает_незаданное(self):
		self.добавить(answer_mode="text")
		ответ = authoring.update_homework(lesson=self.урок, title="Встреча с заказчиком")["data"]
		self.assertEqual(ответ["title"], "Встреча с заказчиком")
		self.assertEqual(ответ["answer_mode"], "text")
		self.assertEqual(ответ["description"], "Проведите встречу и опишите итог.")

	def test_правило_срока_проверяется(self):
		self.добавить()
		ответ = authoring.update_homework(lesson=self.урок, due_mode="relative")
		self.assertEqual(ответ["error"]["code"], "invalid_due")
		ответ = authoring.update_homework(lesson=self.урок, answer_mode="audio")
		self.assertEqual(ответ["error"]["code"], "invalid_answer_mode")
		ответ = authoring.update_homework(lesson=self.урок, due_mode="absolute", due_date="2030-01-15")
		self.assertEqual((ответ["data"]["due_mode"], str(ответ["data"]["due_date"])), ("absolute", "2030-01-15"))

	def test_правка_и_удаление_без_задания_отказ(self):
		self.assertEqual(authoring.update_homework(lesson=self.урок, title="т")["error"]["code"], "homework_missing")
		self.assertEqual(authoring.remove_homework(lesson=self.урок)["error"]["code"], "homework_missing")

	def test_удаление_без_сдач(self):
		self.добавить()
		ответ = authoring.remove_homework(lesson=self.урок)
		self.assertEqual(ответ["data"], {"lesson": self.урок, "removed": True})
		self.assertFalse(frappe.db.exists("Agent Lesson Homework", {"lesson": self.урок}))

	def test_задание_со_сдачей_не_удаляется_даже_архивной(self):
		задание = self.добавить()["data"]["id"]
		ученик = создать_ученика(f"hwau-s-{frappe.generate_hash(length=6)}@example.com")
		сдача = домашка.сохранить(ученик, self.урок, None, answer="сделал")
		ответ = authoring.remove_homework(lesson=self.урок)
		self.assertEqual(ответ["error"]["code"], "homework_in_use")
		self.assertEqual(ответ["error"]["submissions"], 1)
		frappe.db.set_value(
			"Agent Homework Submission", сдача.name, {"member": None, "archived_student": ученик}
		)
		self.assertEqual(authoring.remove_homework(lesson=self.урок)["error"]["code"], "homework_in_use")
		self.assertTrue(frappe.db.exists("Agent Lesson Homework", задание))

	def test_удаление_урока_убирает_задание(self):
		задание = self.добавить()["data"]["id"]
		ответ = authoring.remove_lesson(lesson=self.урок)
		self.assertTrue(ответ["ok"], ответ)
		self.assertFalse(frappe.db.exists("Agent Lesson Homework", задание))

	def test_урок_со_сдачей_не_удаляется(self):
		self.добавить()
		ученик = создать_ученика(f"hwau-l-{frappe.generate_hash(length=6)}@example.com")
		домашка.сохранить(ученик, self.урок, None, answer="сделал")
		ответ = authoring.remove_lesson(lesson=self.урок)
		self.assertEqual(ответ["error"]["code"], "lesson_in_use")
		self.assertEqual(ответ["error"]["homework_submissions"], 1)

	def test_ревизия_растёт_от_правок_задания(self):
		правки = {
			"add_homework": lambda: self.добавить(),
			"update_homework": lambda: authoring.update_homework(lesson=self.урок, title="Другое"),
			"remove_homework": lambda: authoring.remove_homework(lesson=self.урок),
		}
		for имя, правка in правки.items():
			with self.subTest(правка=имя):
				до = self.ревизия()
				правка()
				self.assertGreater(self.ревизия(), до)

	def test_методист_добавляет_задание(self):
		frappe.set_user(создать_куратора(f"hwau-c-{frappe.generate_hash(length=6)}@example.com"))
		self.assertTrue(self.добавить()["ok"])

	def test_ученику_авторинг_закрыт(self):
		frappe.set_user(создать_ученика(f"hwau-p-{frappe.generate_hash(length=6)}@example.com"))
		with self.assertRaises(frappe.PermissionError):
			self.добавить()
