# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Методы автора курса: публикация, анонс, ревизия и граница прав.

Публикацию релиза и его проекцию в Learning проверяют `test_publish_release`
и `agent_learning/releases/test_service.py`; здесь — что вокруг неё.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	привязать_главу,
	привязать_урок,
	создать_куратора,
	создать_ученика,
)
from lms_frappe_app.api import authoring


class IntegrationTestAuthoring(IntegrationTestCase):
	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"curator-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Курс {суффикс}", summary="Анонс")["data"]["id"]

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_курс_без_релиза_не_открывается(self):
		"""Открывается только курс с действующим релизом."""
		ответ = authoring.publish_course(course=self.курс)

		self.assertEqual(ответ["error"]["code"], "course_not_released")
		self.assertFalse(frappe.db.get_value("LMS Course", self.курс, "published"))

	def test_снятие_несуществующего_курса_даёт_код(self):
		ответ = authoring.unpublish_course(course="нет-такого-курса")

		self.assertEqual(ответ["error"]["code"], "course_not_found")

	def test_ученик_не_правит_курсы_и_не_видит_ответов(self):
		"""Отдельный эндпоинт ничего не защищает — защищает эта проверка."""
		курс = authoring.publish_release(release=пример_релиза(f"pupil-{frappe.generate_hash(length=6)}"))[
			"data"
		]["course"]
		ученик = создать_ученика(f"pupil-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(ученик)

		for вызов in (
			lambda: authoring.list_courses(),
			lambda: authoring.course_release(course=курс),
			lambda: authoring.course_release(course=курс, lesson="l-1"),
			lambda: authoring.update_course(course=self.курс, title="Свой"),
			lambda: authoring.publish_course(course=курс),
			lambda: authoring.announce_course(course=self.курс, objectives="Цель"),
		):
			with self.assertRaises(frappe.PermissionError):
				вызов()


class IntegrationTestAuthoringEdits(IntegrationTestCase):
	"""Список курсов и правка анонса."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"editor-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.ключ = f"edits-{суффикс}"

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_курс_находится_по_списку(self):
		"""Без списка идентификатор курса взять негде — все методы требуют его."""
		курс = authoring.publish_release(release=пример_релиза(self.ключ))["data"]["course"]

		курсы = authoring.list_courses()["data"]["courses"]

		наш = [строка for строка in курсы if строка["id"] == курс]
		self.assertTrue(наш, "опубликованный релизом курс не виден в списке")
		self.assertFalse(наш[0]["published"])
		self.assertEqual(наш[0]["lessons_total"], 3)

	def test_правится_только_анонс_без_уроков(self):
		"""Курс без релиза, но с уроками — заведёнными мимо релиза, в Learning, —
		не анонс: программу курсу даёт релиз."""
		курс = authoring.create_course(title="С уроком", summary="было")["data"]["id"]
		frappe.set_user("Administrator")
		глава = frappe.get_doc({"doctype": "Course Chapter", "title": "Глава", "course": курс}).insert(
			ignore_permissions=True
		)
		урок = frappe.get_doc({"doctype": "Course Lesson", "title": "Урок", "chapter": глава.name}).insert(
			ignore_permissions=True
		)
		привязать_главу(курс, глава.name)
		привязать_урок(глава.name, урок.name)
		frappe.set_user(self.куратор)

		ответ = authoring.update_course(course=курс, title="Стало")

		self.assertEqual(ответ["error"]["code"], "course_has_content")
		self.assertEqual(ответ["error"]["lessons"], 1)
		self.assertNotEqual(frappe.db.get_value("LMS Course", курс, "title"), "Стало")

		анонс = authoring.create_course(title="Анонс", summary="было")["data"]["id"]
		ответ = authoring.update_course(course=анонс, title="Стало", summary="и описание")
		self.assertEqual(ответ["data"]["title"], "Стало")
		self.assertEqual(frappe.db.get_value("LMS Course", анонс, "short_introduction"), "и описание")


class IntegrationTestCourseRevision(IntegrationTestCase):
	"""Отметка изменения курса: по ней кабинет автора узнаёт, что курс поменялся,
	не перечитывая его целиком (#261). Курс правится только новым релизом (#512)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"revision-{суффикс}@example.com")
		self.ключ = f"revision-{суффикс}"
		frappe.set_user(self.куратор)
		self.курс = self.опубликовать()["course"]

	def опубликовать(self, релиз: dict | None = None) -> dict:
		ответ = authoring.publish_release(release=релиз or пример_релиза(self.ключ))
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def отметки(self) -> dict:
		return authoring.course_revision(course=self.курс)["data"]

	def ревизия(self) -> str:
		return self.отметки()["revision"]

	def test_новый_релиз_двигает_ревизию_а_тот_же_нет(self):
		до = self.ревизия()
		self.опубликовать()
		self.assertEqual(self.ревизия(), до)

		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, переписанный"
		self.опубликовать(релиз)

		self.assertGreater(self.ревизия(), до)

	def test_открытие_курса_двигает_ревизию(self):
		до = self.ревизия()
		authoring.publish_course(course=self.курс)
		self.assertGreater(self.ревизия(), до)

	def test_заметка_двигает_свою_метку_а_не_ревизию(self):
		до = self.отметки()
		self.assertIsNone(до["notes_revision"])

		ид = authoring.add_note(course=self.курс, target="course", text="Проверь карточку")["data"]["id"]
		после_записи = self.отметки()
		authoring.reply_note(note=ид, text="Уточню: обещание")
		после_ответа = self.отметки()

		self.assertEqual(после_записи["revision"], до["revision"])
		self.assertIsNotNone(после_записи["notes_revision"])
		self.assertGreater(после_ответа["notes_revision"], после_записи["notes_revision"])

	def test_чтение_ревизию_не_меняет(self):
		до = self.ревизия()
		authoring.course_release(course=self.курс)
		authoring.course_release(course=self.курс, lesson="l-1")
		authoring.course_releases(course=self.курс)

		self.assertEqual(self.ревизия(), до)

	def test_снятие_с_публикации_и_анонс_двигают_ревизию(self):
		authoring.publish_course(course=self.курс)
		до = self.ревизия()
		authoring.unpublish_course(course=self.курс)
		после_снятия = self.ревизия()
		self.assertGreater(после_снятия, до)

		ответ = authoring.announce_course(course=self.курс)

		self.assertTrue(ответ["ok"], ответ)
		self.assertGreater(self.ревизия(), после_снятия)

	def test_анонс_без_релиза_отмечается_карточкой(self):
		анонс = authoring.create_course(title="Анонс", summary="было")["data"]["id"]
		до = authoring.course_revision(course=анонс)["data"]["revision"]
		authoring.update_course(course=анонс, summary="стало")

		self.assertGreater(authoring.course_revision(course=анонс)["data"]["revision"], до)

	def test_ученику_ревизия_недоступна(self):
		ученик = создать_ученика(f"revision-s-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(ученик)

		with self.assertRaises(frappe.PermissionError):
			authoring.course_revision(course=self.курс)

	def test_неизвестный_курс_даёт_код(self):
		ответ = authoring.course_revision(course="такого-курса-нет")

		self.assertEqual(ответ["error"]["code"], "course_not_found")


class IntegrationTestLessonHookAndPromise(IntegrationTestCase):
	"""Зачин урока и обещание курса (#238).

	Адресат обоих — ученик, а директива адресована агенту; поэтому это поля
	`Course Lesson` и `LMS Course`, а не поля директив (решение владельца 1Б).
	"""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"curator-{суффикс}@example.com")
		frappe.set_user(self.куратор)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_у_урока_и_курса_есть_поля_зачина_и_обещания(self):
		self.assertTrue(frappe.get_meta("Course Lesson").has_field("lesson_hook"))
		self.assertTrue(frappe.get_meta("LMS Course").has_field("course_promise"))

	def test_обещание_курса_задаётся_и_очищается(self):
		анонс = authoring.create_course(title="Анонс", summary="Без уроков")["data"]["id"]
		ответ = authoring.update_course(course=анонс, promise="Уйдёте с готовым канвасом")
		self.assertEqual(ответ["data"]["promise"], "Уйдёте с готовым канвасом")
		self.assertEqual(
			frappe.db.get_value("LMS Course", анонс, "course_promise"), "Уйдёте с готовым канвасом"
		)

		authoring.update_course(course=анонс, promise="")
		self.assertFalse(frappe.db.get_value("LMS Course", анонс, "course_promise"))
