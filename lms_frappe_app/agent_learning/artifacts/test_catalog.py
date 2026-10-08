# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Проверка каталога документов: действующие схемы курсов — через движок (learning-services#377)."""

import json
from unittest import mock

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.artifacts import catalog
from lms_frappe_app.commands import commands
from lms_frappe_app.tests.sample_data import зачислить, создать_урок, создать_ученика, схема_документа

БЛОКИ = [
	{"key": "intro", "title": "Вступление", "hint": "Одной фразой"},
	{
		"key": "items",
		"title": "Записи",
		"spec": {
			"table": "items",
			"prefix": "I",
			"fields": [{"key": "limit", "title": "Предел", "type": "number"}],
			"columns": [
				{"key": "event", "title": "Событие", "type": "text", "required": True},
				{"key": "kind", "title": "Вид", "type": "select", "options": ["a", "b"]},
			],
		},
	},
	{"key": "outro", "title": "Итог", "kind": "file", "accept": "xlsx,csv"},
]
ХОЛСТ = {"grid": ["intro items", "outro outro"], "labels": {"intro": "Начало"}}
#: Таблица шаблонов документов: на сайте её может не быть.
ТАБЛИЦА_ШАБЛОНОВ = "Agent Artifact Template"


class IntegrationTestArtifactCatalog(IntegrationTestCase):
	"""Каталог общий для сайта, и на стенде в нём чужие документы: тест смотрит
	только на беды своих записей."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(создать_ученика(f"cat-{суффикс}@example.com"), урок)
		self.холст = self.записать("journal", "Журнал", БЛОКИ, layout="canvas", canvas=ХОЛСТ)
		self.столбец = self.записать("plain", "Схема столбцом", БЛОКИ[:2])

	def записать(self, документ: str, название: str, блоки: list, **схема) -> str:
		ответ = схема_документа(self.курс, документ, название, блоки, **схема)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]["id"]

	def свои(self) -> list[dict]:
		return [беда for беда in catalog.проверить_каталог() if беда["course"] == self.курс]

	def test_каталог_в_порядке(self):
		self.assertEqual(self.свои(), [])

	def test_подделанная_запись_видна(self):
		# Колонка `id` занята движком, ячейка холста — блок, которого нет.
		frappe.db.set_value(
			"Agent Artifact Block",
			{"parent": self.столбец, "block_key": "items"},
			"spec",
			json.dumps({"columns": [{"key": "id"}]}),
		)
		frappe.db.set_value(
			"Agent Course Artifact", self.холст, "canvas", json.dumps({"grid": ["intro ghost"]})
		)

		беды = {б["name"]: б for б in self.свои()}

		self.assertEqual(
			{имя: беда["code"] for имя, беда in беды.items()},
			{self.столбец: "artifact_invalid_spec", self.холст: "artifact_invalid_spec"},
		)
		self.assertEqual(
			{ключ: беды[self.холст][ключ] for ключ in ("doctype", "course", "artifact", "version")},
			{"doctype": "Agent Course Artifact", "course": self.курс, "artifact": "journal", "version": 1},
		)
		строки: list[str] = []
		self.assertEqual(catalog.отчёт(строки.append), 1)
		self.assertTrue(any(self.столбец in с and "plain" in с for с in строки), строки)
		with self.assertRaises(frappe.ValidationError):
			catalog.после_миграции()

	def test_сбой_проверки_бедой_а_не_исключением(self):
		"""Сбой на одном документе не прячет беды остальных."""
		with mock.patch.object(catalog, "проверить_холст", side_effect=RuntimeError("сломалось")):
			беды = self.свои()

		self.assertEqual({б["name"] for б in беды}, {self.холст, self.столбец})
		self.assertEqual({б["code"] for б in беды}, {catalog.СБОЙ})
		self.assertIn("RuntimeError: сломалось", беды[0]["message"])

	def test_миграция_без_таблицы_шаблонов(self):
		"""`after_migrate` каталога проходит на сайте, где таблицы шаблонов
		документов нет: ни один её запрос до базы не доходит."""
		хук = "lms_frappe_app.agent_learning.artifacts.catalog.после_миграции"
		self.assertIn(хук, frappe.get_hooks("after_migrate"))

		исходный_sql = frappe.db.sql
		исходная_таблица = frappe.db.table_exists
		исходный_get_all = frappe.get_all
		курс = self.курс

		def sql(запрос, *args, **kwargs):
			if ТАБЛИЦА_ШАБЛОНОВ in str(запрос):
				raise frappe.db.TableMissingError(1146, f"Table 'tab{ТАБЛИЦА_ШАБЛОНОВ}' doesn't exist")
			return исходный_sql(запрос, *args, **kwargs)

		def table_exists(doctype, *args, **kwargs):
			return doctype != ТАБЛИЦА_ШАБЛОНОВ and исходная_таблица(doctype, *args, **kwargs)

		def get_all(doctype, *args, filters=None, **kwargs):
			# Только свои схемы: чужие документы стенда тесту не принадлежат.
			if doctype == catalog.ДОКУМЕНТ:
				filters = {**(filters or {}), "course": курс}
			return исходный_get_all(doctype, *args, filters=filters, **kwargs)

		with (
			mock.patch.object(frappe.db, "sql", side_effect=sql),
			mock.patch.object(frappe.db, "table_exists", side_effect=table_exists),
			mock.patch("frappe.get_all", side_effect=get_all),
		):
			self.assertFalse(frappe.db.table_exists(ТАБЛИЦА_ШАБЛОНОВ))
			with self.assertRaises(frappe.db.TableMissingError):
				frappe.get_all(ТАБЛИЦА_ШАБЛОНОВ)
			frappe.get_attr(хук)()

	def test_команда_bench_объявлена(self):
		self.assertIn("check-artifact-catalog", [команда.name for команда in commands])
