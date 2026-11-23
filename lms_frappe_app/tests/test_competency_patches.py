# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Патч `drop_competency_map`: колонки `Agent Course Release.competency_map` нет
(learning-services#839)."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.patches.v0_1 import drop_competency_map
from lms_frappe_app.tests.test_old_model_patches import Схема, выполнить


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
