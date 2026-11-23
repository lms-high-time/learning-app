# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Патчи `competency_event_catalog_keys` и `drop_competency_map`: журнал компетенций и релизы — на
каталоге, колонки `Agent Course Release.competency_map` нет (learning-services#839)."""

import json
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.catalog import service as каталог
from lms_frappe_app.patches.v0_1 import competency_event_catalog_keys, drop_competency_map
from lms_frappe_app.tests.catalog_sample import поверх_опубликованного
from lms_frappe_app.tests.release_sample import каталог_компетенций, пример_релиза
from lms_frappe_app.tests.sample_data import курс_из_релиза
from lms_frappe_app.tests.test_old_model_patches import Схема, выполнить, отметить_патч

ПРИЛОЖЕНИЕ = Path(drop_competency_map.__file__).parents[2]
ПРОВЕРКА = competency_event_catalog_keys


class IntegrationTestКлючиЖурнала(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		каталог.опубликовать(поверх_опубликованного(каталог_компетенций()), "Administrator")

	def вставить(self, компетенция: str, версия: int | None = 1) -> None:
		"""Запись журнала мимо проверки ссылки — как записи, сделанные до неё; без ученика, как
		архив сброса. Удаляется после теста: откат — только после класса."""
		имя = frappe.generate_hash(length=10)
		frappe.get_doc(
			{
				"doctype": competency_event_catalog_keys.ЖУРНАЛ,
				"name": имя,
				"kind": "Evidence",
				"competency": компетенция,
				"competency_version": версия,
				"outcome": "skipped",
				"request_key": frappe.generate_hash(length=10),
			}
		).db_insert()
		self.addCleanup(frappe.db.delete, competency_event_catalog_keys.ЖУРНАЛ, {"name": имя})

	def test_проходит_на_ключах_каталога(self):
		self.вставить("base-1")
		self.assertEqual(ПРОВЕРКА.находки(), [])
		self.assertIn("журнал и релизы компетенций — на каталоге", выполнить(ПРОВЕРКА))

	def test_падает_на_ключе_не_из_каталога_со_списком(self):
		"""Ключ не из каталога и ключ каталога в другом регистре — в списке с числом записей;
		журнал не тронут."""
		for компетенция in ("base-1", "K1", "K1", "BASE-1"):
			self.вставить(компетенция)
		with self.assertRaises(frappe.ValidationError) as отказ:
			competency_event_catalog_keys.execute()
		текст = str(отказ.exception)
		self.assertIn("K1: записей 2", текст)
		self.assertIn("BASE-1: записей 1", текст)
		self.assertNotIn("base-1:", текст)
		self.assertEqual(frappe.db.count(competency_event_catalog_keys.ЖУРНАЛ, {"competency": "K1"}), 2)

	def test_падает_на_записи_без_версии_со_списком(self):
		"""Ключ каталога без версии компетенции — пустой или 0 — в списке: такая запись стала бы
		общей для всех курсов ученика."""
		for версия in (None, 0, 1):
			self.вставить("base-2", версия)
		with self.assertRaises(frappe.ValidationError) as отказ:
			ПРОВЕРКА.execute()
		текст = str(отказ.exception)
		self.assertIn("записи журнала без версии компетенции — base-2: записей 2", текст)
		self.assertNotIn("ключи журнала не из каталога", текст)
		self.assertEqual(frappe.db.count(ПРОВЕРКА.ЖУРНАЛ, {"competency": "base-2"}), 3)

	def test_падает_на_релизе_без_каталога_с_целями(self):
		"""Действующий релиз без выпуска каталога, у урока которого есть цели, — в списке; у
		освобождённого релиза (без снимка) и у пустых целей — нет."""
		_, релиз = курс_из_релиза(релиз=пример_релиза(f"keys-{frappe.generate_hash(length=6)}"))
		self.assertEqual(ПРОВЕРКА.релизы_без_каталога(), [])
		for цели in ("[]", None):
			frappe.db.set_value(ПРОВЕРКА.УРОК, {"parent": релиз, "lesson_key": "l-1"}, "competencies", цели)
			self.assertEqual(ПРОВЕРКА.релизы_без_каталога(), [], цели)
		frappe.db.set_value(
			ПРОВЕРКА.УРОК,
			{"parent": релиз, "lesson_key": "l-1"},
			"competencies",
			json.dumps([{"competency": "base-1", "target": 2}]),
		)
		with self.assertRaises(frappe.ValidationError) as отказ:
			ПРОВЕРКА.execute()
		self.assertIn(f"релиз без каталога с целями уроков по компетенциям — {релиз}", str(отказ.exception))
		frappe.db.set_value(ПРОВЕРКА.РЕЛИЗ, релиз, "snapshot", None)
		self.assertEqual(ПРОВЕРКА.релизы_без_каталога(), [])

	def test_падает_на_релизе_с_картой_внутри(self):
		"""Непустая карта в колонке `competency_map` — в списке; колонки нет — проверять нечего."""
		схема = Схема({ПРОВЕРКА.РЕЛИЗ: {"name", "snapshot", "competency_map"}, ПРОВЕРКА.УРОК: {"name"}})
		with схема.подменить({"competency_map is not null": ["REL-00042"]}):
			with self.assertRaises(frappe.ValidationError) as отказ:
				ПРОВЕРКА.execute()
		self.assertIn("релиз с картой компетенций внутри — REL-00042", str(отказ.exception))
		self.assertEqual(схема.ddl, [])
		self.assertEqual(ПРОВЕРКА.релизы_с_картой(), [])


class IntegrationTestDropCompetencyMap(IntegrationTestCase):
	def test_поля_нет_в_схеме(self):
		self.assertIsNone(frappe.get_meta(drop_competency_map.ДОКТАЙП).get_field(drop_competency_map.КОЛОНКА))

	def test_проверка_раньше_удаления(self):
		строки = (ПРИЛОЖЕНИЕ / "patches.txt").read_text(encoding="utf-8").splitlines()
		проверка, удаление = (
			строки.index(f"lms_frappe_app.patches.v0_1.{патч}")
			for патч in (drop_competency_map.ЖДЁТ, "drop_competency_map")
		)
		self.assertLess(строки.index("[pre_model_sync]"), проверка)
		self.assertLess(проверка, строки.index("[post_model_sync]"))
		self.assertGreater(удаление, строки.index("[post_model_sync]"))

	def test_ждёт_проверки(self):
		"""Проверка упала при `--skip-failing` или не запускалась — колонка остаётся."""
		for состояние in ("пропущен", "нет"):
			with self.subTest(состояние):
				отметить_патч(drop_competency_map.ЖДЁТ, состояние)
				схема = Схема({drop_competency_map.ДОКТАЙП: {"name", drop_competency_map.КОЛОНКА}})
				with схема.подменить():
					вывод = выполнить(drop_competency_map)
				self.assertEqual(схема.ddl, [])
				self.assertIn("оставлена — патч competency_event_catalog_keys ещё не выполнен", вывод)

	def test_удаляет_колонку_один_раз(self):
		отметить_патч(drop_competency_map.ЖДЁТ, "выполнен")
		схема = Схема({drop_competency_map.ДОКТАЙП: {"name", "snapshot", drop_competency_map.КОЛОНКА}})
		with схема.подменить():
			первый = выполнить(drop_competency_map)
			второй = выполнить(drop_competency_map)
		self.assertEqual(
			схема.ddl, ["ALTER TABLE `tabAgent Course Release` DROP COLUMN IF EXISTS `competency_map`"]
		)
		self.assertEqual(схема.таблицы[drop_competency_map.ДОКТАЙП], {"name", "snapshot"})
		self.assertTrue(схема.схема_по_базе)
		self.assertIn("удалена колонка competency_map", первый)
		self.assertIn("колонки competency_map нет", второй)
