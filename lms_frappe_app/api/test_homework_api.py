# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import base64

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.test_homework import задание
from lms_frappe_app.api import student
from lms_frappe_app.tests.sample_data import (
	зачислить,
	привязать_урок,
	создать_организацию,
	создать_ученика,
	создать_урок,
)


def файл(имя: str, данные: bytes) -> dict:
	return {"name": имя, "data": base64.b64encode(данные).decode()}


class IntegrationTestHomeworkApi(IntegrationTestCase):
	"""Методы ученика для домашки (learning-services#439)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"hwa-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(self.ученик, self.урок)
		задание(self.урок)
		frappe.set_user(self.ученик)

	def test_сдача_текстом_и_файлом(self):
		ответ = student.submit_homework(lesson=self.урок, answer="сделал")
		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(ответ["data"]["space"], "personal")
		self.assertEqual(ответ["data"]["submission"]["version"], 1)
		ответ = student.submit_homework(lesson=self.урок, files=[файл("a.txt", b"hi")])
		сдача = ответ["data"]["submission"]
		self.assertEqual(сдача["answer"], "сделал", "текст без `answer` остаётся прежним")
		self.assertEqual(сдача["files"][0]["name"], "a.txt")
		self.assertEqual(len(сдача["versions"]), 2)
		ответ = student.submit_homework(lesson=self.урок, remove_files=[сдача["files"][0]["id"]])
		сдача = ответ["data"]["submission"]
		self.assertEqual(сдача["files"], [])
		self.assertEqual(сдача["versions"][1]["files"][0]["name"], "a.txt", "старая версия помнит файл")

	def test_параметры_списком_приходят_и_строкой_json(self):
		"""Frappe отдаёт тело запроса формой: списки приезжают строкой."""
		import json

		ответ = student.submit_homework(lesson=self.урок, files=json.dumps([файл("b.txt", b"x")]))
		self.assertEqual(ответ["data"]["submission"]["files"][0]["name"], "b.txt")

	def test_задание_и_сдача_урока(self):
		ответ = student.homework(lesson=self.урок)["data"]
		self.assertEqual(ответ["homework"]["title"], "Встреча со спонсором")
		self.assertEqual(ответ["homework"]["due"], {"mode": "none", "days": None, "date": None})
		self.assertIsNone(ответ["submission"])
		self.assertTrue(ответ["lesson_url"].startswith(f"/lms/courses/{self.курс}/learn/1-1"))
		student.submit_homework(lesson=self.урок, answer="сделал")
		сдача = student.homework(lesson=self.урок)["data"]["submission"]
		self.assertEqual(сдача["status"], "Submitted")
		self.assertEqual([с["event"] for с in сдача["history"]], ["submitted"])
		self.assertEqual(len(сдача["versions"]), 1)

	def test_журнал_называет_автора_события_по_имени(self):
		"""Ученику на странице урока журнал показывает имя, а не почту."""
		frappe.db.set_value(
			"User", self.ученик, {"first_name": "Анна", "last_name": "Петрова", "full_name": "Анна Петрова"}
		)
		frappe.clear_document_cache("User", self.ученик)
		сдача = student.submit_homework(lesson=self.урок, answer="сделал")["data"]["submission"]
		[событие] = сдача["history"]
		self.assertEqual(событие["by"], self.ученик)
		self.assertEqual(событие["by_name"], "Анна Петрова")

	def test_без_имени_журнал_называет_почту(self):
		frappe.db.set_value("User", self.ученик, {"first_name": "", "last_name": "", "full_name": ""})
		frappe.clear_document_cache("User", self.ученик)
		[событие] = student.submit_homework(lesson=self.урок, answer="сделал")["data"]["submission"]["history"]
		self.assertEqual(событие["by_name"], self.ученик)

	def test_урок_без_задания_не_отказ(self):
		frappe.set_user("Administrator")
		другой = создать_урок(f"Без задания {frappe.generate_hash(length=4)}")
		зачислить(self.ученик, другой)
		frappe.set_user(self.ученик)
		ответ = student.homework(lesson=другой)
		self.assertTrue(ответ["ok"], ответ)
		self.assertIsNone(ответ["data"]["homework"])
		self.assertEqual(student.submit_homework(lesson=другой, answer="x")["error"]["code"], "no_homework")

	def test_без_записи_на_курс_задания_не_видно(self):
		frappe.set_user("Administrator")
		чужой = создать_урок(f"Чужой {frappe.generate_hash(length=4)}")
		задание(чужой)
		frappe.set_user(self.ученик)
		for ответ in (
			student.homework(lesson=чужой),
			student.my_homework(lesson=чужой),
			student.submit_homework(lesson=чужой, answer="x"),
		):
			self.assertFalse(ответ["ok"])
			self.assertEqual(ответ["error"]["code"], "not_enrolled")

	def test_мои_домашки(self):
		student.submit_homework(lesson=self.урок, answer="сделал")
		[строка] = student.my_homework()["data"]["items"]
		self.assertEqual(строка["lesson"], self.урок)
		self.assertEqual(строка["course"], self.курс)
		self.assertTrue(строка["lesson_url"].startswith("/lms/courses/"))
		self.assertEqual(строка["status"], "Submitted")
		self.assertIsNone(строка["last_comment"])
		self.assertNotIn("submission", строка)
		[строка] = student.my_homework(course=self.курс)["data"]["items"]
		self.assertEqual(строка["lesson"], self.урок)

	def test_моя_домашка_по_уроку_целиком(self):
		student.submit_homework(lesson=self.урок, answer="сделал")
		[строка] = student.my_homework(lesson=self.урок)["data"]["items"]
		self.assertEqual(строка["submission"]["answer"], "сделал")
		self.assertEqual(строка["homework"]["answer_mode"], "text_and_files")
		self.assertNotIn("versions", строка["submission"])

	def test_чужое_пространство_отказ(self):
		frappe.set_user("Administrator")
		организация = создать_организацию(f"Чужая {frappe.generate_hash(length=4)}")
		frappe.set_user(self.ученик)
		ответ = student.submit_homework(lesson=self.урок, answer="x", space=организация)
		self.assertEqual(ответ["error"]["code"], "space_not_available")
		ответ = student.my_homework(space=организация)
		self.assertEqual(ответ["error"]["code"], "space_not_available")


class IntegrationTestHomeworkStart(IntegrationTestCase):
	"""Домашка в `start_lesson`: задание текущего урока и сдача прошлого (learning-services#439)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"hwst-{суффикс}@example.com")
		self.первый = создать_урок(f"Урок 1 {суффикс}")
		self.курс = зачислить(self.ученик, self.первый)
		глава = frappe.db.get_value("Course Lesson", self.первый, "chapter")
		self.второй = frappe.get_doc(
			{"doctype": "Course Lesson", "title": f"Урок 2 {суффикс}", "chapter": глава}
		).insert(ignore_permissions=True).name
		привязать_урок(глава, self.второй)
		задание(self.первый, due_mode="relative", due_days=3)
		frappe.set_user(self.ученик)

	def test_первый_урок_отдаёт_своё_задание(self):
		данные = student.start_lesson(lesson=self.первый)["data"]
		self.assertEqual(данные["homework"]["title"], "Встреча со спонсором")
		self.assertEqual(данные["homework"]["due"]["days"], 3)
		self.assertIsNone(данные["previous_homework"])

	def test_следующий_урок_отдаёт_сдачу_прошлого(self):
		student.submit_homework(lesson=self.первый, answer="Встретились, бюджет согласован")
		данные = student.start_lesson(lesson=self.второй)["data"]
		self.assertIsNone(данные["homework"])
		прошлое = данные["previous_homework"]
		self.assertEqual(прошлое["lesson"], self.первый)
		self.assertEqual(прошлое["homework"]["title"], "Встреча со спонсором")
		self.assertEqual(прошлое["submission"]["status"], "Submitted")
		self.assertEqual(прошлое["submission"]["answer"], "Встретились, бюджет согласован")
		self.assertEqual([с["event"] for с in прошлое["submission"]["history"]], ["submitted"])
		self.assertIsNone(прошлое["last_comment"])

	def test_лёгкий_старт_без_ответа_ученика(self):
		student.submit_homework(lesson=self.первый, answer="Встретились")
		прошлое = student.start_lesson(lesson=self.второй, brief=True)["data"]["previous_homework"]
		self.assertEqual(прошлое["submission"]["status"], "Submitted")
		self.assertNotIn("answer", прошлое["submission"])
		self.assertNotIn("homework", прошлое)
		self.assertIn("last_comment", прошлое)

	def test_прошлое_задание_без_сдачи(self):
		прошлое = student.start_lesson(lesson=self.второй)["data"]["previous_homework"]
		self.assertEqual(прошлое["lesson"], self.первый)
		self.assertIsNone(прошлое["submission"])
