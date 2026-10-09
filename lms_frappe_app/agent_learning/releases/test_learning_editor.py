# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Редактор Learning не правит главы и уроки курса из релиза (learning-services#512)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from lms.lms import api as learning

from lms_frappe_app.agent_learning.errors import КУРС_ИЗ_РЕЛИЗА, Отказ
from lms_frappe_app.agent_learning.releases import learning_editor
from lms_frappe_app.tests.sample_data import курс_из_релиза, создать_урок, урок_релиза


def вызвать(метод: str, **параметры):
	"""Тем путём, которым идёт запрос редактора: через подмену хуком."""
	return frappe.call(frappe.override_whitelisted_method(f"lms.lms.api.{метод}"), **параметры)


class IntegrationTestРедакторLearning(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		self.курс, _ = курс_из_релиза()
		self.урок = урок_релиза(self.курс, "l-1")
		self.глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		self.свободный_урок = создать_урок(f"Без релиза {frappe.generate_hash(length=6)}")
		self.свободная_глава, self.свободный_курс = frappe.db.get_value(
			"Course Lesson", self.свободный_урок, ["chapter", "course"]
		)

	def вызовы(self, урок: str, глава: str, курс: str) -> dict[str, dict]:
		return {
			"delete_chapter": dict(chapter=глава),
			"update_lesson_index": dict(lesson=урок, sourceChapter=глава, targetChapter=глава, idx=0),
			"update_chapter_index": dict(chapter=глава, course=курс, idx=0),
			"delete_lesson": dict(lesson=урок, chapter=глава),
			"create_lesson": dict(chapter=глава),
			"upsert_chapter": dict(
				title="Глава", course=курс, is_scorm_package=False, scorm_package=None, name=глава
			),
		}

	def test_запросы_редактора_уходят_в_подмену(self):
		"""`Why:` без хука редактор снова зовёт метод Learning, и порядок курса из
		релиза правится мимо `validate` — тест на сам модуль этого не заметит."""
		for метод in learning_editor.ПОДМЕНЯЕМЫЕ:
			self.assertEqual(
				frappe.override_whitelisted_method(f"lms.lms.api.{метод}"),
				f"lms_frappe_app.agent_learning.releases.learning_editor.{метод}",
			)

	def test_курс_из_релиза_отказ(self):
		вызовы = self.вызовы(self.урок, self.глава, self.курс)
		self.assertEqual(set(вызовы), set(learning_editor.ПОДМЕНЯЕМЫЕ))
		for метод, параметры in вызовы.items():
			with patch.object(learning, метод) as исходный, self.assertRaises(Отказ) as пойман:
				вызвать(метод, **параметры)
			self.assertEqual(пойман.exception.код, КУРС_ИЗ_РЕЛИЗА, метод)
			исходный.assert_not_called()

	def test_курс_без_релиза_идёт_в_learning(self):
		for метод, параметры in self.вызовы(self.свободный_урок, self.свободная_глава, self.свободный_курс).items():
			with patch.object(learning, метод, return_value="learning") as исходный:
				self.assertEqual(вызвать(метод, **параметры), "learning", метод)
			исходный.assert_called_once_with(*параметры.values())

	def test_новая_глава_в_курсе_из_релиза_отказ(self):
		with self.assertRaises(Отказ):
			вызвать("upsert_chapter", title="Лишняя", course=self.курс, is_scorm_package=False)

	def test_перенос_урока_в_главу_курса_из_релиза_отказ(self):
		"""Learning проверяет права только по главе-источнику: курс главы-цели — наша проверка."""
		with self.assertRaises(Отказ):
			вызвать(
				"update_lesson_index",
				lesson=self.свободный_урок,
				sourceChapter=self.свободная_глава,
				targetChapter=self.глава,
				idx=0,
			)
		self.assertEqual(
			frappe.get_all("Lesson Reference", {"parent": self.глава}, pluck="lesson", order_by="idx"),
			[урок_релиза(self.курс, "l-1"), урок_релиза(self.курс, "l-2")],
		)

	def test_курс_без_релиза_правится_редактором(self):
		"""Без подмены методов: подмена отдаёт вызов Learning целиком."""
		урок = вызвать("create_lesson", chapter=self.свободная_глава)
		вызвать(
			"update_lesson_index",
			lesson=урок,
			sourceChapter=self.свободная_глава,
			targetChapter=self.свободная_глава,
			idx=0,
		)
		self.assertEqual(
			frappe.get_all("Lesson Reference", {"parent": self.свободная_глава}, pluck="lesson", order_by="idx"),
			[урок, self.свободный_урок],
		)
		вызвать("delete_lesson", lesson=урок, chapter=self.свободная_глава)
		self.assertFalse(frappe.db.exists("Course Lesson", урок))

	def test_записи_редактора_без_ключа_пишутся_null(self):
		"""Глава и урок, заведённые редактором Learning в курсе без релиза, — без
		ключа, и пустой ключ в базе — NULL, а не `''`: уникальный индекс
		`(course, ключ)` различает только NULL-ы. Пустую строку от клиента
		`frappe.client` хук `validate` тоже приводит к NULL."""
		вызвать("create_lesson", chapter=self.свободная_глава)
		вызвать("create_lesson", chapter=self.свободная_глава)
		вызвать("upsert_chapter", title="Вторая", course=self.свободный_курс, is_scorm_package=False)
		frappe.get_doc(
			{
				"doctype": "Course Lesson",
				"title": "Урок с пустым ключом",
				"chapter": self.свободная_глава,
				"course": self.свободный_курс,
				"lesson_key": "",
			}
		).insert()

		for doctype, поле in (("Course Lesson", "lesson_key"), ("Course Chapter", "chapter_key")):
			ключи = frappe.db.sql(
				f"select `{поле}` from `tab{doctype}` where course = %s", self.свободный_курс, pluck=True
			)
			with self.subTest(doctype=doctype):
				self.assertGreaterEqual(len(ключи), 2)
				self.assertEqual(ключи, [None] * len(ключи))
