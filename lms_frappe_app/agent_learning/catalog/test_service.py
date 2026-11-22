# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Публикация каталога компетенций: записи, версии, отказы (learning-services#825)."""

import copy
import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.catalog import read, service
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.tests.catalog_sample import (
	ключи_примера,
	компетенция,
	поверх_опубликованного,
	пример_каталога,
)


class IntegrationTestПубликацияКаталога(IntegrationTestCase):
	def setUp(self):
		self.суффикс = frappe.generate_hash(length=6)
		self.к = ключи_примера(self.суффикс)

	def опубликовать(self, каталог: dict, коммит: str | None = None) -> dict:
		return service.опубликовать(каталог, "Administrator", коммит)

	def отказ(self, каталог) -> Отказ:
		выпусков = frappe.db.count(read.ВЫПУСК)
		with self.assertRaises(Отказ) as отказ:
			self.опубликовать(каталог)
		self.assertEqual(frappe.db.count(read.ВЫПУСК), выпусков, "отказ ничего не записывает")
		return отказ.exception

	def test_выпуски_и_версии(self):
		"""Первый выпуск заводит всё; тот же каталог — `unchanged`; редакторская правка
		версию не трогает; правка признака — следующая версия с этого выпуска."""
		каталог = поверх_опубликованного(пример_каталога(self.суффикс))
		первый = self.опубликовать(каталог, коммит="ab" * 20)

		with self.subTest("первый выпуск"):
			self.assertFalse(первый["unchanged"])
			self.assertEqual(первый["commit"], "ab" * 20)
			self.assertEqual(первый["catalog_release"], read.имя_выпуска(первый["version"]))
			self.assertEqual(первый["changes"]["added"], [self.к["a"], self.к["b"], self.к["whole"]])
			целое = frappe.get_doc(read.КОМПЕТЕНЦИЯ, self.к["whole"])
			self.assertEqual(
				(целое.kind, целое.area, целое.version, целое.version_release),
				("integrative", self.к["area"], 1, первый["catalog_release"]),
			)
			self.assertEqual(
				[(ч.competency, ч.from_level) for ч in целое.parts], [(self.к["a"], 1), (self.к["b"], 3)]
			)
			self.assertEqual(целое.level_3, f"Признак уровня 3 для {self.к['whole']}")
			self.assertEqual(
				[п.competency for п in frappe.get_doc(read.КОМПЕТЕНЦИЯ, self.к["a"]).similar], [self.к["b"]]
			)
			self.assertEqual(
				json.loads(frappe.db.get_value(read.ВЫПУСК, первый["catalog_release"], "snapshot")), каталог
			)

		with self.subTest("тот же каталог — без выпуска"):
			повтор = self.опубликовать(copy.deepcopy(каталог))
			self.assertTrue(повтор["unchanged"])
			self.assertEqual(повтор["catalog_release"], первый["catalog_release"])
			self.assertEqual(повтор["commit"], "ab" * 20)

		with self.subTest("редакторская правка"):
			правка = copy.deepcopy(каталог)
			себя = next(к for к in правка["competencies"] if к["key"] == self.к["a"])
			себя["title"] = "Новое название"
			себя.pop("similar")
			второй = self.опубликовать(правка)
			self.assertEqual(второй["version"], первый["version"] + 1)
			self.assertEqual(второй["changes"], {"added": [], "substantive": [], "editorial": [self.к["a"]]})
			запись = frappe.get_doc(read.КОМПЕТЕНЦИЯ, self.к["a"])
			self.assertEqual(
				(запись.title, запись.similar, запись.version, запись.version_release),
				("Новое название", [], 1, первый["catalog_release"]),
			)

		with self.subTest("правка по существу"):
			по_существу = copy.deepcopy(правка)
			себя = next(к for к in по_существу["competencies"] if к["key"] == self.к["a"])
			себя["levels"]["4"] = "Новый признак"
			третий = self.опубликовать(по_существу)
			self.assertEqual(
				третий["changes"],
				{"added": [], "substantive": [{"key": self.к["a"], "version": 2}], "editorial": []},
			)
			запись = frappe.get_doc(read.КОМПЕТЕНЦИЯ, self.к["a"])
			self.assertEqual(
				(запись.version, запись.version_release, запись.level_4),
				(2, третий["catalog_release"], "Новый признак"),
			)
			последний = read.последний()
			self.assertEqual(последний["catalog_release"], третий["catalog_release"])
			self.assertEqual(последний["versions"][self.к["a"]], 2)
			self.assertEqual(последний["catalog"], по_существу)

	def test_отказы(self):
		каталог = поверх_опубликованного(пример_каталога(self.суффикс))
		self.опубликовать(каталог)

		with self.subTest("ключ пропал"):
			без_компетенции = copy.deepcopy(каталог)
			без_компетенции["competencies"] = [
				к for к in без_компетенции["competencies"] if к["key"] != self.к["b"]
			]
			for к in без_компетенции["competencies"]:
				к.pop("similar", None)
				if к["key"] == self.к["whole"]:
					к["parts"] = [
						{"competency": self.к["a"], "from_level": 1},
						{"competency": "extra-" + self.суффикс, "from_level": 2},
					]
			без_компетенции["competencies"].append(компетенция("extra-" + self.суффикс, self.к["area"]))
			отказ = self.отказ(без_компетенции)
			self.assertEqual(отказ.код, service.КЛЮЧИ_ПРОПАЛИ)
			self.assertEqual(отказ.подробности, {"competencies": [self.к["b"]], "areas": []})
			self.assertFalse(frappe.db.exists(read.КОМПЕТЕНЦИЯ, "extra-" + self.суффикс))

		for случай, значение, код in (
			("не JSON", "{", service.КАТАЛОГ_НЕВЕРЕН),
			("формат", {**каталог, "format": "lms-competency-catalog/2"}, service.ФОРМАТ_НЕ_ТОТ),
			("схема", {**каталог, "areas": []}, service.КАТАЛОГ_НЕВЕРЕН),
			(
				"ссылки",
				{
					**каталог,
					"competencies": [
						*каталог["competencies"],
						компетенция("lost-" + self.суффикс, "nowhere"),
					],
				},
				service.КАТАЛОГ_НЕ_СХОДИТСЯ,
			),
		):
			with self.subTest(случай):
				self.assertEqual(self.отказ(значение).код, код)
