# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import public
from lms_frappe_app.api.authoring import КУРС_НЕ_НАЙДЕН
from lms_frappe_app.tests.sample_data import (
	привязать_урок,
	зачислить,
	создать_ученика,
	создать_урок,
)


class IntegrationTestCourseMap(IntegrationTestCase):
	"""Карта курса без релиза — то, что видят гость и зачисленный ученик.

	Цели уроков задаёт только релиз (`test_lesson_readers`): у курса без
	релиза уроки приходят с пустым списком целей.
	"""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)

		self.ученик = создать_ученика(f"map-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок карты {суффикс}")
		глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		self.курс = frappe.db.get_value("Course Chapter", глава, "course")
		frappe.db.set_value("LMS Course", self.курс, "published", 1)

	def карта(self) -> dict:
		return public.course_map(course=self.курс)["data"]

	def test_гость_видит_уроки_без_целей(self):
		frappe.set_user("Guest")

		[урок] = self.карта()["chapters"][0]["lessons"]

		self.assertEqual((урок["id"], урок["objectives"]), (self.урок, []))

	def test_зачисленный_видит_уроки_без_целей(self):
		зачислить(self.ученик, self.урок)
		frappe.set_user(self.ученик)

		[урок] = self.карта()["chapters"][0]["lessons"]

		self.assertEqual((урок["id"], урок["objectives"]), (self.урок, []))

	# --- программа курса: зачин, пройденность, следующий урок (learning-services#322) ---

	def test_гость_видит_зачин_но_не_свой_путь(self):
		frappe.db.set_value("Course Lesson", self.урок, "lesson_hook", "  Зачем это вам  ")
		frappe.set_user("Guest")

		карта = self.карта()
		урок = карта["chapters"][0]["lessons"][0]

		self.assertEqual(урок["hook"], "Зачем это вам")
		self.assertNotIn("completed", урок, "пройденность — только своя, у гостя ключа нет")
		self.assertNotIn("next_lesson", карта)

	def test_пустой_зачин_приходит_null(self):
		frappe.set_user("Guest")

		self.assertIsNone(self.карта()["chapters"][0]["lessons"][0]["hook"])

	def test_зачисленный_видит_пройденность_и_следующий_урок(self):
		глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		второй = frappe.get_doc(
			{"doctype": "Course Lesson", "title": f"Второй {frappe.generate_hash(length=6)}", "chapter": глава}
		).insert(ignore_permissions=True).name
		привязать_урок(глава, второй)
		зачислить(self.ученик, self.урок)
		frappe.get_doc(
			{"doctype": "LMS Course Progress", "member": self.ученик, "lesson": self.урок, "status": "Complete"}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)

		карта = self.карта()
		уроки = {у["id"]: у for у in карта["chapters"][0]["lessons"]}

		self.assertTrue(уроки[self.урок]["completed"])
		self.assertFalse(уроки[второй]["completed"])
		self.assertEqual(карта["next_lesson"], второй, "тот же урок, что у lesson_entry(course)")

	def test_у_пройденного_курса_следующего_нет(self):
		зачислить(self.ученик, self.урок)
		frappe.get_doc(
			{"doctype": "LMS Course Progress", "member": self.ученик, "lesson": self.урок, "status": "Complete"}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)

		self.assertIsNone(self.карта()["next_lesson"])

	def test_непубликованный_курс_гостю_отказ(self):
		frappe.db.set_value("LMS Course", self.курс, "published", 0)
		frappe.set_user("Guest")

		ответ = public.course_map(course=self.курс)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], КУРС_НЕ_НАЙДЕН)

	def test_порядок_уроков_совпадает_с_программой_learning(self):
		второй = создать_урок(f"Второй урок {frappe.generate_hash(length=4)}")
		глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		привязать_урок(глава, второй)
		frappe.set_user("Guest")

		from lms.lms.utils import get_course_outline

		наш = [
			урок["id"]
			for гл in self.карта()["chapters"]
			for урок in гл["lessons"]
		]
		их = [
			урок["name"]
			for гл in get_course_outline(self.курс)
			for урок in гл.get("lessons", [])
		]

		self.assertEqual(наш, их)


class IntegrationTestCourseMapDocuments(IntegrationTestCase):
	"""Документ курса на карте: что соберёт курс и на каком уроке (#340)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"map-doc-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок документа {суффикс}")
		глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		self.курс = frappe.db.get_value("Course Chapter", глава, "course")
		frappe.db.set_value("LMS Course", self.курс, "published", 1)
		frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"course": self.курс,
				"slug": "summary",
				"title": "Резюме проекта",
				"blocks": [
					{"block_key": "goal", "title": "Цель", "hint": "Секрет для агента", "lesson": self.урок},
					{"block_key": "sponsor", "title": "Спонсор"},
				],
			}
		).insert(ignore_permissions=True)

	def карта(self) -> dict:
		return public.course_map(course=self.курс)["data"]

	def test_гость_видит_документ_и_блоки_урока_без_заполненности(self):
		frappe.set_user("Guest")
		карта = self.карта()

		self.assertEqual(карта["documents"], [{"artifact": "summary", "title": "Резюме проекта"}])
		self.assertEqual(
			карта["chapters"][0]["lessons"][0]["blocks"],
			[{"artifact": "summary", "key": "goal", "title": "Цель"}],
		)
		self.assertNotIn("Секрет для агента", json.dumps(карта, ensure_ascii=False))

	def test_ученик_видит_заполненность(self):
		зачислить(self.ученик, self.урок)
		frappe.set_user(self.ученик)
		from lms_frappe_app.api import student

		student.update_artifact(self.курс, "summary", "goal", "Открыть седьмую кофейню")
		карта = self.карта()

		документ = карта["documents"][0]
		self.assertEqual((документ["blocks_filled"], документ["blocks_total"]), (1, 2))
		self.assertTrue(карта["chapters"][0]["lessons"][0]["blocks"][0]["filled"])

	def test_курс_без_документа(self):
		frappe.db.delete("Agent Course Artifact", {"course": self.курс})
		frappe.set_user("Guest")
		карта = self.карта()

		self.assertEqual(карта["documents"], [])
		self.assertEqual(карта["chapters"][0]["lessons"][0]["blocks"], [])
