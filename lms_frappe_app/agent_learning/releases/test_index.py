# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Индекс релиза по ключам: запись и чтение (learning-services#500)."""

import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.releases import index, projection

# Модулем, а не именами: класс тестов проекции в этом модуле прогнался бы второй раз.
from lms_frappe_app.agent_learning.releases import test_projection as проекция
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

	def test_известные_помнят_снятый_ключ(self):
		релиз = пример_релиза()
		релиз["chapters"] = релиз["chapters"][:1]
		релиз["lessons"] = релиз["lessons"][:2]
		frappe.set_user(self.куратор)
		итог = projection.спроецировать(self.курс, релиз, index.известные(self.курс), index.ключи(self.релиз))
		frappe.set_user("Administrator")
		self.вставить(релиз, итог, 2)

		известные = index.известные(self.курс)
		self.assertEqual(известные["lessons"]["l-3"], self.итог.уроки["l-3"])
		self.assertEqual(известные["chapters"]["ch-2"], self.итог.главы["ch-2"])

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
		self.assertEqual(index.снимок(self.релиз)["agent"], {"opaque": True})
		self.assertEqual(index.снимок(self.релиз), пример_релиза())
