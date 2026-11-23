# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Патчи `competency_event_catalog_keys` и `drop_competency_map`: журнал компетенций — на ключах
каталога, колонки `Agent Course Release.competency_map` нет (learning-services#839)."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.catalog import service as каталог
from lms_frappe_app.patches.v0_1 import competency_event_catalog_keys, drop_competency_map
from lms_frappe_app.tests.catalog_sample import поверх_опубликованного
from lms_frappe_app.tests.release_sample import каталог_компетенций
from lms_frappe_app.tests.test_old_model_patches import Схема, выполнить


class IntegrationTestКлючиЖурнала(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		каталог.опубликовать(поверх_опубликованного(каталог_компетенций()), "Administrator")

	def вставить(self, компетенция: str) -> None:
		"""Запись журнала мимо проверки ссылки — как записи, сделанные до неё; без ученика, как
		архив сброса. Удаляется после теста: откат — только после класса."""
		имя = frappe.generate_hash(length=10)
		frappe.get_doc(
			{
				"doctype": competency_event_catalog_keys.ЖУРНАЛ,
				"name": имя,
				"kind": "Evidence",
				"competency": компетенция,
				"outcome": "skipped",
				"request_key": frappe.generate_hash(length=10),
			}
		).db_insert()
		self.addCleanup(frappe.db.delete, competency_event_catalog_keys.ЖУРНАЛ, {"name": имя})

	def test_проходит_на_ключах_каталога(self):
		self.вставить("base-1")
		self.assertEqual(competency_event_catalog_keys.неизвестные(), {})
		self.assertIn(
			"все ключи журнала компетенций — ключи каталога", выполнить(competency_event_catalog_keys)
		)

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


class IntegrationTestDropCompetencyMap(IntegrationTestCase):
	def test_поля_нет_в_схеме(self):
		self.assertIsNone(frappe.get_meta(drop_competency_map.ДОКТАЙП).get_field(drop_competency_map.КОЛОНКА))

	def test_удаляет_колонку_один_раз(self):
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
