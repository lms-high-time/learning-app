# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Публикация каталога компетенций: записи, версии, отказы (learning-services#825)."""

import copy
import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.catalog import checks, read, service
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.tests.catalog_sample import (
	ключи_примера,
	компетенция,
	поверх_опубликованного,
	пример_каталога,
)


def каталог_схема(каталог: dict, ключ: str) -> dict:
	return next(с for с in каталог["classifications"] if с["key"] == ключ)


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
		"""Первый выпуск заводит всё; тот же каталог, переставленный, — `unchanged`;
		редакторская правка — и правка классов компетенции — версию не трогает и называет
		поля; новая схема, новый и переименованный класс видны в правках и в записях схем;
		правка признака — следующая версия с этого выпуска."""
		каталог = поверх_опубликованного(пример_каталога(self.суффикс))
		первый = self.опубликовать(каталог, коммит="ab" * 20)

		with self.subTest("первый выпуск"):
			self.assertFalse(первый["unchanged"])
			self.assertEqual(первый["commit"], "ab" * 20)
			self.assertEqual(первый["catalog_release"], read.имя_выпуска(первый["version"]))
			self.assertEqual(первый["changes"]["added"], [self.к["a"], self.к["b"], self.к["whole"]])
			self.assertEqual(
				{
					с: первый["changes"]["classifications"]["classes"]["added"][с]
					for с in (self.к["scheme"], self.к["nos"])
				},
				{
					self.к["scheme"]: [self.к["x"], self.к["y"]],
					self.к["nos"]: [self.к["people"], self.к["self"]],
				},
			)
			целое = frappe.get_doc(read.КОМПЕТЕНЦИЯ, self.к["whole"])
			self.assertEqual(
				(целое.kind, целое.version, целое.version_release),
				("integrative", 1, первый["catalog_release"]),
			)
			классы = {
				к.classification: к.class_key for к in frappe.get_doc(read.КОМПЕТЕНЦИЯ, self.к["a"]).classes
			}
			self.assertEqual(
				(классы[self.к["scheme"]], классы[self.к["nos"]]), (self.к["x"], self.к["people"])
			)
			схема = frappe.get_doc(read.СХЕМА, self.к["nos"])
			self.assertEqual(
				(схема.title, схема.required, схема.source.splitlines()),
				(f"Функции руководителя {self.суффикс}", 0, каталог_схема(каталог, self.к["nos"])["source"]),
			)
			self.assertEqual(
				[(к.class_key, к.title) for к in схема.classes],
				[(self.к["people"], "Работать с людьми"), (self.к["self"], "Управлять собой")],
			)
			# Своя схема без первоисточника публикуется; пустой список не читается из базы
			# правкой — следующая публикация не называет её изменённой.
			направление = frappe.db.get_value(
				read.СХЕМА, self.к["scheme"], ["required", "source"], as_dict=True
			)
			self.assertEqual((направление.required, направление.source or ""), (1, ""))
			self.assertEqual(service._опубликованные_схемы()[self.к["scheme"]]["source"], [])
			self.assertEqual(
				[(ч.competency, ч.from_level) for ч in целое.parts], [(self.к["a"], 1), (self.к["b"], 3)]
			)
			self.assertEqual(целое.level_3, f"Признак уровня 3 для {self.к['whole']}")
			self.assertEqual(
				[п.competency for п in frappe.get_doc(read.КОМПЕТЕНЦИЯ, self.к["a"]).similar], [self.к["b"]]
			)
			self.assertEqual(
				json.loads(frappe.db.get_value(read.ВЫПУСК, первый["catalog_release"], "snapshot")),
				checks.канон(каталог),
			)

		with self.subTest("тот же каталог в другом порядке — без выпуска"):
			переставленный = copy.deepcopy(каталог)
			переставленный["competencies"].reverse()
			повтор = self.опубликовать(переставленный)
			self.assertTrue(повтор["unchanged"])
			self.assertEqual(повтор["catalog_release"], первый["catalog_release"])
			self.assertEqual(повтор["commit"], "ab" * 20)

		with self.subTest("редакторская правка"):
			правка = copy.deepcopy(каталог)
			себя = next(к for к in правка["competencies"] if к["key"] == self.к["a"])
			себя["title"] = "Новое название"
			себя.pop("similar")
			себя["classes"].pop(self.к["nos"])
			себя["classes"][self.к["scheme"]] = self.к["y"]
			целое = next(к for к in правка["competencies"] if к["key"] == self.к["whole"])
			целое["kind"] = "base"
			целое.pop("parts")
			nos = каталог_схема(правка, self.к["nos"])
			nos["classes"][1]["title"] = "Управлять собой и развитием"
			nos["classes"].append({"key": f"tasks-{self.суффикс}", "title": "Достигать результата"})
			второй = self.опубликовать(правка)
			self.assertEqual(второй["version"], первый["version"] + 1)
			self.assertEqual(
				второй["changes"],
				{
					"added": [],
					"substantive": [],
					"editorial": [
						{"key": self.к["a"], "fields": ["title", "classes", "similar"]},
						{"key": self.к["whole"], "fields": ["kind", "parts"]},
					],
					"classifications": {
						"added": [],
						"changed": [],
						"classes": {
							"added": {self.к["nos"]: [f"tasks-{self.суффикс}"]},
							"renamed": {self.к["nos"]: [self.к["self"]]},
						},
					},
				},
			)
			self.assertEqual(
				[(к.class_key, к.title) for к in frappe.get_doc(read.СХЕМА, self.к["nos"]).classes],
				[
					(self.к["people"], "Работать с людьми"),
					(self.к["self"], "Управлять собой и развитием"),
					(f"tasks-{self.суффикс}", "Достигать результата"),
				],
			)
			целое = frappe.get_doc(read.КОМПЕТЕНЦИЯ, self.к["whole"])
			self.assertEqual((целое.kind, целое.parts, целое.version), ("base", [], 1))
			запись = frappe.get_doc(read.КОМПЕТЕНЦИЯ, self.к["a"])
			self.assertEqual(
				(запись.title, запись.similar, запись.version, запись.version_release),
				("Новое название", [], 1, первый["catalog_release"]),
			)
			классы = {к.classification: к.class_key for к in запись.classes}
			self.assertEqual(классы[self.к["scheme"]], self.к["y"])
			self.assertNotIn(self.к["nos"], классы)

		with self.subTest("правка по существу"):
			по_существу = copy.deepcopy(правка)
			себя = next(к for к in по_существу["competencies"] if к["key"] == self.к["a"])
			себя["levels"]["4"] = "Новый признак"
			третий = self.опубликовать(по_существу)
			self.assertEqual(третий["changes"]["substantive"], [{"key": self.к["a"], "version": 2}])
			запись = frappe.get_doc(read.КОМПЕТЕНЦИЯ, self.к["a"])
			self.assertEqual(
				(запись.version, запись.version_release, запись.level_4),
				(2, третий["catalog_release"], "Новый признак"),
			)
			последний = read.последний()
			self.assertEqual(последний["catalog_release"], третий["catalog_release"])
			self.assertEqual(последний["versions"][self.к["a"]], 2)
			self.assertEqual(последний["catalog"], checks.канон(по_существу))

	def test_отказы(self):
		каталог = поверх_опубликованного(пример_каталога(self.суффикс))
		запасная = {
			"key": f"spare-{self.суффикс}",
			"title": "Запасная",
			"source": ["https://example.com/spare"],
			"required": False,
			"classes": [{"key": f"spare-class-{self.суффикс}", "title": "Запасной"}],
		}
		каталог["classifications"].append(запасная)
		self.опубликовать(каталог)

		with self.subTest("схема пропала"):
			без_схемы = copy.deepcopy(каталог)
			без_схемы["classifications"].remove(запасная)
			отказ = self.отказ(без_схемы)
			self.assertEqual(отказ.код, service.КЛЮЧИ_ПРОПАЛИ)
			self.assertEqual(
				отказ.подробности, {"competencies": [], "classifications": [запасная["key"]], "classes": {}}
			)

		with self.subTest("класс пропал"):
			без_класса = copy.deepcopy(каталог)
			nos = каталог_схема(без_класса, self.к["nos"])
			nos["classes"] = [к for к in nos["classes"] if к["key"] != self.к["self"]]
			отказ = self.отказ(без_класса)
			self.assertEqual(отказ.код, service.КЛЮЧИ_ПРОПАЛИ)
			self.assertEqual(
				отказ.подробности,
				{"competencies": [], "classifications": [], "classes": {self.к["nos"]: [self.к["self"]]}},
			)

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
			# Классы — как у соседней: с обязательными схемами прошлых тестов (`поверх_опубликованного`).
			классы = next(к for к in каталог["competencies"] if к["key"] == self.к["whole"])["classes"]
			без_компетенции["competencies"].append(компетенция("extra-" + self.суффикс, классы))
			отказ = self.отказ(без_компетенции)
			self.assertEqual(отказ.код, service.КЛЮЧИ_ПРОПАЛИ)
			self.assertEqual(
				отказ.подробности, {"competencies": [self.к["b"]], "classifications": [], "classes": {}}
			)
			self.assertFalse(frappe.db.exists(read.КОМПЕТЕНЦИЯ, "extra-" + self.суффикс))

		for случай, значение, код in (
			("не JSON", "{", service.КАТАЛОГ_НЕВЕРЕН),
			("формат", {**каталог, "format": "lms-competency-catalog/2"}, service.ФОРМАТ_НЕ_ТОТ),
			("схема", {**каталог, "classifications": None}, service.КАТАЛОГ_НЕВЕРЕН),
			(
				"класс не из схемы",
				{
					**каталог,
					"competencies": [
						*каталог["competencies"],
						компетенция("lost-" + self.суффикс, {self.к["scheme"]: "nowhere"}),
					],
				},
				service.КАТАЛОГ_НЕ_СХОДИТСЯ,
			),
			(
				"нет класса в обязательной схеме",
				{
					**каталог,
					"competencies": [*каталог["competencies"], компетенция("lost-" + self.суффикс)],
				},
				service.КАТАЛОГ_НЕ_СХОДИТСЯ,
			),
		):
			with self.subTest(случай):
				self.assertEqual(self.отказ(значение).код, код)

	def test_гонка_двух_публикаций(self):
		"""Вторая из двух одновременных публикаций, не увидевшая выпуск первой, упирается в
		уникальную версию — отказ `busy` кодом контракта, а не ошибка сервера, и ничего не
		записано."""
		каталог = поверх_опубликованного(пример_каталога(self.суффикс))
		self.опубликовать(каталог)
		правка = copy.deepcopy(каталог)
		next(к for к in правка["competencies"] if к["key"] == self.к["a"])["title"] = "Гонка"
		with patch.object(service, "_последний_с_блокировкой", return_value=None):
			отказ = self.отказ(правка)
		self.assertEqual(отказ.код, service.ЗАНЯТО)
		self.assertEqual(frappe.db.get_value(read.КОМПЕТЕНЦИЯ, self.к["a"], "title"), f"Умение {self.к['a']}")
