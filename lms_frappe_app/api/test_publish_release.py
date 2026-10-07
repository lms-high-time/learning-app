# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Метод `publish_release`: граница прав и форма ответа (learning-services#500).

Поведение публикации проверяет `agent_learning/releases/test_service.py`;
здесь — что оно дошло до метода: кто может звать, что релиз принимается и
объектом, и строкой JSON, и что отказ едет кодом контракта.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import authoring
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
)


class IntegrationTestPublishRelease(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"rel-api-{суффикс}@example.com")
		self.ученик = создать_ученика(f"rel-api-pupil-{суффикс}@example.com")
		self.ключ = f"api-{суффикс}"

	def test_ученику_нельзя(self):
		frappe.set_user(self.ученик)
		with self.assertRaises(frappe.PermissionError):
			authoring.publish_release(release=пример_релиза(self.ключ))
		self.assertFalse(frappe.db.exists("LMS Course", {"course_key": self.ключ}))

	def test_куратор_публикует_объектом(self):
		frappe.set_user(self.куратор)
		ответ = authoring.publish_release(release=пример_релиза(self.ключ))

		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(ответ["data"]["version"], 1)
		self.assertTrue(ответ["data"]["course_created"])

	def test_строка_json_то_же_что_объект(self):
		frappe.set_user(self.куратор)
		authoring.publish_release(release=пример_релиза(self.ключ))

		ответ = authoring.publish_release(release=json.dumps(пример_релиза(self.ключ), ensure_ascii=False))

		self.assertTrue(ответ["data"]["unchanged"], ответ)

	def test_отказ_кодом_контракта(self):
		frappe.set_user(self.куратор)
		ответ = authoring.publish_release(release={"format": "lms-release/2"})

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], "release_format_unsupported")

	def test_пустой_course_ищет_по_ключу(self):
		frappe.set_user(self.куратор)
		первый = authoring.publish_release(release=пример_релиза(self.ключ))["data"]

		ответ = authoring.publish_release(release=пример_релиза(self.ключ), course="")

		self.assertEqual(ответ["data"]["course"], первый["course"])

	def test_только_post(self):
		self.assertEqual(
			set(frappe.allowed_http_methods_for_whitelisted_func[authoring.publish_release]), {"POST"}
		)

	def test_курс_из_релиза_не_правится_по_кусочку(self):
		"""Курс из релиза правится новым релизом: методы сборки по кусочку отказывают."""
		frappe.set_user(self.куратор)
		данные = authoring.publish_release(release=пример_релиза(self.ключ))["data"]
		курс = данные["course"]
		урок = frappe.db.get_value("Course Lesson", {"course": курс, "title": "Урок первый"})
		глава = frappe.db.get_value("Course Lesson", урок, "chapter")
		вызовы = {
			"update_course": dict(course=курс, title="Другое"),
			"add_chapter": dict(course=курс, title="Лишняя"),
			"update_chapter": dict(chapter=глава, title="Другая"),
			"remove_chapter": dict(chapter=глава),
			"reorder_chapters": dict(course=курс, chapters=[глава]),
			"add_lesson": dict(chapter=глава, title="Лишний", body="# Лишний"),
			"update_lesson": dict(lesson=урок, title="Другой"),
			"move_lesson": dict(lesson=урок, position=2),
			"remove_lesson": dict(lesson=урок),
			"reorder_lessons": dict(chapter=глава, lessons=[урок]),
			"set_directive": dict(lesson=урок, teaching_directive="Директива"),
			"set_course_directive": dict(course=курс, teaching_directive="Директива"),
			"set_course_artifact": dict(course=курс, artifact="notebook", title="Тетрадь", blocks=[]),
			"set_course_artifact_template": dict(course=курс, artifact="notebook", template="any"),
			"upgrade_course_artifact": dict(course=курс, artifact="notebook"),
			"add_quiz": dict(lesson=урок, questions=[]),
			"add_question": dict(lesson=урок, question={"question": "?"}),
			"remove_question": dict(lesson=урок, question="any"),
			"add_homework": dict(lesson=урок, title="Задание", description="Сделайте"),
			"update_homework": dict(lesson=урок, title="Задание"),
			"remove_homework": dict(lesson=урок),
			"set_course_map": dict(course=курс, levels=[], nodes=[]),
		}
		for метод, параметры in вызовы.items():
			ответ = getattr(authoring, метод)(**параметры)
			self.assertEqual(ответ.get("error", {}).get("code"), "course_from_release", (метод, ответ))
		self.assertEqual(frappe.db.get_value("Course Lesson", урок, "title"), "Урок первый")

	def test_вопрос_квиза_на_уроке_из_релиза_не_правится(self):
		"""Вопрос Learning, оказавшийся в квизе урока из релиза, — тоже правка курса."""
		from lms_frappe_app.tests.sample_data import создать_вопрос, создать_квиз

		frappe.set_user(self.куратор)
		курс = authoring.publish_release(release=пример_релиза(self.ключ))["data"]["course"]
		урок = frappe.db.get_value("Course Lesson", {"course": курс, "title": "Урок первый"})
		frappe.set_user("Administrator")
		вопрос = создать_вопрос("Столица?", варианты=[("Москва", True), ("Тула", False)])
		создать_квиз(урок, [вопрос])
		frappe.set_user(self.куратор)

		ответ = authoring.update_question(question=вопрос, text="Другой вопрос")

		self.assertEqual(ответ["error"]["code"], "course_from_release", ответ)
		self.assertEqual(frappe.db.get_value("LMS Question", вопрос, "question"), "Столица?")

	def test_руководителю_нельзя(self):
		организация = создать_организацию(f"Релиз {frappe.generate_hash(length=6)}")
		руководитель = создать_менеджера(
			f"rel-api-mgr-{frappe.generate_hash(length=6)}@example.com", организация
		)
		frappe.set_user(руководитель)
		with self.assertRaises(frappe.PermissionError):
			authoring.publish_release(release=пример_релиза(self.ключ))
