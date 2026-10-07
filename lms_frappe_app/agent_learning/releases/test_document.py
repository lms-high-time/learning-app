# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Схема документа курса из релиза (learning-services#500)."""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.releases import document, projection

# Модулем, а не именами: класс тестов проекции в этом модуле прогнался бы второй раз.
from lms_frappe_app.agent_learning.releases import test_projection as проекция
from lms_frappe_app.api import student
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import зачислить, создать_куратора, создать_ученика

СХЕМА = "Agent Course Artifact"


class IntegrationTestДокументИзРелиза(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.куратор = создать_куратора(f"rel-doc-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(self.куратор)
		self.курс = проекция.курс_куратора(self.куратор)
		self.итог = projection.спроецировать(self.курс, пример_релиза(), проекция.ПУСТО, {})

	def спроецировать(self, релиз: dict, прежний: str | None = None):
		return document.спроецировать(
			self.курс, релиз["document"], релиз["lessons"], self.итог.уроки, прежний
		)

	def блоки(self) -> dict:
		имя = frappe.db.get_value(СХЕМА, {"course": self.курс, "slug": "notebook", "is_active": 1})
		return {б.block_key: б for б in frappe.get_doc(СХЕМА, имя).blocks}

	def test_разделы_становятся_блоками(self):
		ответ = self.спроецировать(пример_релиза())

		self.assertEqual(ответ, {"artifact": "notebook", "version": 1})
		схема = frappe.get_doc(СХЕМА, {"course": self.курс, "slug": "notebook", "is_active": 1})
		self.assertEqual(
			(схема.title, схема.purpose, схема.layout), ("Тетрадь", "Зачем ученику тетрадь.", "sections")
		)
		блоки = self.блоки()
		self.assertEqual(list(блоки), ["log", "rules"])
		журнал = блоки["log"]
		self.assertEqual(журнал.lesson, self.итог.уроки["l-1"])
		self.assertEqual(журнал.description, "Что сюда записывают.")
		спек = json.loads(журнал.spec)
		self.assertEqual(спек["table"], "log")
		self.assertEqual([к["key"] for к in спек["columns"]], ["topic", "decision", "owner"])
		self.assertEqual(
			{к["key"]: к.get("required") for к in спек["columns"]},
			{"topic": True, "decision": None, "owner": "decision"},
		)
		self.assertEqual({к["type"] for к in спек["columns"]}, {"longtext"})
		правила = блоки["rules"]
		self.assertEqual(правила.lesson, self.итог.уроки["l-2"])
		self.assertEqual(
			json.loads(правила.spec)["fields"],
			[{"key": "scope", "title": "Что решаем", "type": "longtext", "required": True}],
		)

	def test_тот_же_документ_та_же_версия(self):
		self.спроецировать(пример_релиза())

		self.assertEqual(
			self.спроецировать(пример_релиза(), "notebook"), {"artifact": "notebook", "version": 1}
		)
		self.assertEqual(frappe.db.count(СХЕМА, {"course": self.курс}), 1)

	def test_правка_колонки_новая_версия(self):
		self.спроецировать(пример_релиза())
		релиз = пример_релиза()
		релиз["document"]["sections"][0]["columns"][0]["title"] = "Тема встречи"

		self.assertEqual(self.спроецировать(релиз, "notebook"), {"artifact": "notebook", "version": 2})
		self.assertEqual(frappe.db.count(СХЕМА, {"course": self.курс, "is_active": 1}), 1)

	def test_правка_описания_раздела_новая_версия(self):
		self.спроецировать(пример_релиза())
		релиз = пример_релиза()
		релиз["document"]["sections"][1]["description"] = "Как решаем теперь."

		self.assertEqual(self.спроецировать(релиз, "notebook")["version"], 2)
		self.assertEqual(self.блоки()["rules"].description, "Как решаем теперь.")

	def test_документ_ушёл_из_релиза(self):
		self.спроецировать(пример_релиза())
		релиз = пример_релиза()
		релиз["document"] = None

		self.assertIsNone(self.спроецировать(релиз, "notebook"))
		self.assertFalse(frappe.db.exists(СХЕМА, {"course": self.курс, "is_active": 1}))

	def test_ученик_пишет_в_раздел_по_ключу(self):
		self.спроецировать(пример_релиза())
		frappe.set_user("Administrator")
		ученик = создать_ученика(f"rel-doc-pupil-{frappe.generate_hash(length=6)}@example.com")
		зачислить(ученик, self.итог.уроки["l-1"])
		frappe.set_user(ученик)

		ответ = student.update_artifact(self.курс, "notebook", "log", rows=[{"topic": "Первая встреча"}])
		self.assertTrue(ответ["ok"], ответ)
		ответ = student.update_artifact(self.курс, "notebook", "rules", fields={"scope": "Бюджет"})
		self.assertTrue(ответ["ok"], ответ)
