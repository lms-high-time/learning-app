# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Индекс релиза по ключам: запись и чтение (learning-services#500)."""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.releases import index, projection

# Модулем, а не именами: класс тестов проекции в этом модуле прогнался бы второй раз.
from lms_frappe_app.agent_learning.releases import test_projection as проекция
from lms_frappe_app.patches.v0_1 import release_agent_slices
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import создать_куратора


class IntegrationTestИндексРелиза(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.куратор = создать_куратора(f"rel-index-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(self.куратор)
		self.курс = проекция.курс_куратора(self.куратор)
		self.итог = projection.спроецировать(self.курс, пример_релиза(), проекция.ПУСТО, {})
		frappe.set_user("Administrator")
		self.релиз = self.вставить(пример_релиза(), self.итог, 1)

	def вставить(self, релиз: dict, итог, версия: int) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": index.РЕЛИЗ,
					"course": self.курс,
					"course_key": релиз["course"]["key"],
					"version": версия,
					"release_format": релиз["format"],
					"digest": frappe.generate_hash(length=64),
					"published_by": "Administrator",
					"published_at": now_datetime(),
					"snapshot": json.dumps(релиз, ensure_ascii=False),
					**index.строки(релиз, итог.главы, итог.уроки),
				}
			)
			.insert()
			.name
		)

	def test_ключи_по_порядку(self):
		self.assertEqual(
			index.ключи(self.релиз), {"chapters": ["ch-1", "ch-2"], "lessons": ["l-1", "l-2", "l-3"]}
		)
		self.assertEqual(index.ключи(None), {"chapters": [], "lessons": []})

	def test_уроки_глав(self):
		self.assertEqual(index.уроки_глав(self.релиз), {"ch-1": ["l-1", "l-2"], "ch-2": ["l-3"]})

	def test_урок_и_ключ_урока_в_обе_стороны(self):
		урок = index.урок(self.релиз, "l-2")
		self.assertEqual(урок["lesson"], self.итог.уроки["l-2"])
		self.assertEqual(урок["chapter_key"], "ch-1")
		self.assertEqual(json.loads(урок["section_keys"]), ["log", "rules"])
		self.assertEqual(index.ключ_урока(self.релиз, self.итог.уроки["l-2"]), "l-2")
		self.assertIsNone(index.урок(self.релиз, "нет-такого"))
		self.assertEqual(json.loads(index.урок(self.релиз, "l-3")["homework"])["due_days"], 3)

	def test_цели_урока_с_пунктами(self):
		цели = index.цели_урока(self.релиз, "l-1")
		self.assertEqual([ц["key"] for ц in цели], ["l-1-D1"])
		self.assertEqual([п["key"] for п in цели[0]["goals"]], ["term:T1", "l-1-D1/V1", "refute:M1"])
		self.assertEqual([п["required"] for п in цели[0]["goals"]], [True, True, False])
		self.assertEqual(цели[0]["goals"][2]["kind"], "misconception")

	def test_ответы_только_явным_флагом(self):
		вопросы = index.вопросы_урока(self.релиз, "l-1")
		self.assertEqual(len(вопросы), 1)
		self.assertNotIn("correct", вопросы[0])
		self.assertNotIn("explanation", вопросы[0])
		self.assertEqual([в["key"] for в in вопросы[0]["options"]], ["V1", "V2"])
		с_ответами = index.вопросы_урока(self.релиз, "l-1", с_ответами=True)
		self.assertEqual(с_ответами[0]["correct"], "V1")
		self.assertEqual(с_ответами[0]["explanation"], "Потому что так велит условие.")

	def test_эталон_вопроса(self):
		self.assertEqual(
			dict(index.эталон(self.релиз, "l-1", "S1/l-1-D1")),
			{"correct": "V1", "explanation": "Потому что так велит условие."},
		)
		self.assertIsNone(index.эталон(self.релиз, "l-2", "S1/l-1-D1"))
		self.assertIsNone(index.эталон(self.релиз, "l-1", "S9/l-1-D1"))

	def test_разделы_документа(self):
		строки = frappe.get_all(
			index.РАЗДЕЛ,
			filters={"parenttype": index.РЕЛИЗ, "parent": self.релиз},
			fields=["section_key", "rows", "columns"],
			order_by="idx asc",
		)
		self.assertEqual([(с.section_key, с.rows) for с in строки], [("log", "many"), ("rules", "one")])
		self.assertEqual(json.loads(строки[0].columns)[2]["required"], {"if_column": "decision"})

	def test_снимок_хранит_пакет_агента_как_есть(self):
		self.assertEqual(index.снимок(self.релиз)["agent"], пример_релиза()["agent"])
		self.assertEqual(index.снимок(self.релиз), пример_релиза())

	# --- пакет агента ---

	def test_срез_урока_и_рамка(self):
		пакет = пример_релиза()["agent"]
		for ключ in ("l-1", "l-2", "l-3"):
			self.assertEqual(index.пакет_урока(self.релиз, ключ), пакет["lessons"][ключ], ключ)
		self.assertEqual(
			index.рамка(self.релиз),
			{"frame": пакет["frame"], "learn_about_student": пакет["learn_about_student"]},
		)
		self.assertEqual(index.ключи_выяснять(self.релиз), ["where_applies", "c-team"])

	def test_урок_без_среза_и_релиз_без_пакета(self):
		релиз = пример_релиза()
		del релиз["agent"]["lessons"]["l-3"]
		del релиз["agent"]["frame"]
		без_среза = self.вставить(релиз, self.итог, 2)
		self.assertEqual(index.пакет_урока(без_среза, "l-3"), {})
		self.assertEqual(
			index.рамка(без_среза), {"learn_about_student": релиз["agent"]["learn_about_student"]}
		)
		self.assertEqual(index.пакет_урока(без_среза, "нет-такого"), {})

		релиз = пример_релиза()
		del релиз["agent"]
		без_пакета = self.вставить(релиз, self.итог, 3)
		self.assertEqual(index.пакет_урока(без_пакета, "l-1"), {})
		self.assertEqual(index.рамка(без_пакета), {})
		self.assertEqual(index.ключи_выяснять(без_пакета), [])

	def test_ключи_выяснять_терпят_не_ту_форму(self):
		for версия, что_выяснять, ключи in (
			(2, "не список", []),
			(3, [{"key": "a"}, "мусор", {"text": "без ключа"}, {"key": 7}, {"key": "b"}], ["a", "b"]),
		):
			релиз = пример_релиза()
			релиз["agent"]["learn_about_student"] = что_выяснять
			with self.subTest(что_выяснять=что_выяснять):
				self.assertEqual(index.ключи_выяснять(self.вставить(релиз, self.итог, версия)), ключи)

	def test_урок_не_отдаёт_пакет_агента(self):
		self.assertNotIn("agent", index.урок(self.релиз, "l-1"))

	def test_патч_раскладывает_пакет_старых_релизов(self):
		frappe.db.set_value(index.РЕЛИЗ, self.релиз, "agent_frame", None, update_modified=False)
		for строка in frappe.get_all(index.УРОК, filters={"parent": self.релиз}, pluck="name"):
			frappe.db.set_value(index.УРОК, строка, "agent", None, update_modified=False)
		self.assertEqual(index.рамка(self.релиз), {})

		release_agent_slices.execute()

		пакет = пример_релиза()["agent"]
		self.assertEqual(index.пакет_урока(self.релиз, "l-2"), пакет["lessons"]["l-2"])
		self.assertEqual(index.рамка(self.релиз)["frame"], пакет["frame"])
		with patch.object(frappe.db, "set_value") as запись:
			release_agent_slices.execute()
		запись.assert_not_called()

	def test_патч_не_раскладывает_пакет_с_ответами(self):
		"""Пакет старого релиза с ответами квиза — пустые срезы и запись в журнале ошибок."""
		снимок = пример_релиза()
		снимок["agent"]["lessons"]["l-1"]["answers"] = {"S1/l-1-D1": "V1"}
		frappe.db.set_value(index.РЕЛИЗ, self.релиз, "snapshot", json.dumps(снимок), update_modified=False)
		for строка in frappe.get_all(index.УРОК, filters={"parent": self.релиз}, pluck="name"):
			frappe.db.set_value(index.УРОК, строка, "agent", None, update_modified=False)
		# Журнал ошибок переживает откат теста, а имя релиза (`REL-#####`)
		# откат возвращает: записи прошлых прогонов с тем же релизом — не наши.
		начало = now_datetime()

		release_agent_slices.execute()

		self.assertEqual(index.пакет_урока(self.релиз, "l-1"), {})
		self.assertEqual(index.пакет_урока(self.релиз, "l-2"), {})
		self.assertEqual(index.рамка(self.релиз), {})
		журнал = frappe.get_all(
			"Error Log",
			filters={
				"reference_doctype": index.РЕЛИЗ,
				"reference_name": self.релиз,
				"creation": (">=", начало),
			},
			pluck="error",
		)
		self.assertEqual(len(журнал), 1)
		self.assertIn("agent.lessons.l-1.answers", журнал[0])
