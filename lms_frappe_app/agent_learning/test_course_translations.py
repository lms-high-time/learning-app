# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Переводы карточки курса без базы (learning-services#738)."""

import json

import frappe
from frappe.tests import UnitTestCase

from lms_frappe_app.agent_learning import course_translations as переводы
from lms_frappe_app.tests.test_unsanitized_fields import НАГРУЗКИ, исполнимое


class UnitTestПереводыКарточки(UnitTestCase):
	def test_в_json_хранит_текст_без_исполнимого(self):
		"""Поле JSON хук очистки не трогает: тег из текста и из целей не исполним и
		после записи — каждый вид `НАГРУЗКИ`, который Frappe пропускает."""
		for вид, нагрузка in НАГРУЗКИ.items():
			with self.subTest(вид):
				значение = json.loads(
					переводы.в_json(
						{"en": {"title": нагрузка("title"), "objectives": [нагрузка("goal"), "Plain goal"]}}
					)
				)["en"]
				self.assertEqual(исполнимое(значение["title"]), [])
				self.assertEqual([исполнимое(цель) for цель in значение["objectives"]], [[], []])
				self.assertEqual(значение["objectives"][1], "Plain goal")

	def test_в_json_без_пустых_языков(self):
		self.assertEqual(
			json.loads(переводы.в_json({"en": {}, "de": {"title": "T"}})), {"de": {"title": "T"}}
		)

	def test_разобрать_неверное_как_пусто(self):
		for значение, ожидается in (
			(None, {}),
			("", {}),
			("{не json", {}),
			('["en"]', {}),
			(["en"], {}),
			('{"en": "строка", "de": {"title": "T"}}', {"de": {"title": "T"}}),
			({"en": {"title": "T"}}, {"en": {"title": "T"}}),
		):
			with self.subTest(значение):
				self.assertEqual(переводы.разобрать(значение), ожидается)

	def test_перевод_на_язык(self):
		"""Русский — основные поля; пробелы срезаются, пустое — как отсутствующее."""
		значение = json.dumps(
			{
				"en": {"title": "  ", "short_introduction": " Short ", "objectives": [" A ", " "]},
				"ru": {"title": "Нет"},
			}
		)
		for язык, ожидается in (
			("ru", {}),
			(None, {}),
			("de", {}),
			("en", {"short_introduction": "Short", "objectives": ["A"]}),
		):
			with self.subTest(язык):
				self.assertEqual(переводы.перевод(значение, язык), ожидается)

	def test_язык_запроса_без_варианта(self):
		прежний = getattr(frappe.local, "lang", None)
		self.addCleanup(setattr, frappe.local, "lang", прежний)
		for язык, ожидается in (("en-US", "en"), ("ru", "ru"), (None, "ru")):
			with self.subTest(язык):
				frappe.local.lang = язык
				self.assertEqual(переводы.язык_запроса(), ожидается)
