# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Компетенции урока для старта без базы (learning-services#807)."""

from frappe.tests import UnitTestCase

from lms_frappe_app.agent_learning.competencies.lesson import компетенции_старта
from lms_frappe_app.tests.release_sample import карта_компетенций


class TestКомпетенцииСтарта(UnitTestCase):
	def setUp(self):
		self.карта = карта_компетенций()

	def test_цели_урока_затем_составляющие_без_цели(self):
		"""Урок с base-2 и whole-1: сначала цели урока по порядку, затем base-1 — составляющая
		whole-1 без цели."""
		итог = компетенции_старта(
			self.карта,
			[{"competency": "base-2", "target": 2}, {"competency": "whole-1", "target": 3}],
			{"whole-1": "Признаки уровней whole-1.", "base-1": ["не", "строка"]},
		)
		self.assertEqual(
			[(к["key"], к["target"]) for к in итог], [("base-2", 2), ("whole-1", 3), ("base-1", None)]
		)
		self.assertEqual(
			итог[1],
			{
				"key": "whole-1",
				"kind": "integrative",
				"title": "Обоснование устройства",
				"description": "Обосновываете устройство целиком.",
				"target": 3,
				"parts": [
					{"competency": "base-1", "from_level": 1},
					{"competency": "base-2", "from_level": 3},
				],
				"text": "Признаки уровней whole-1.",
				"levels": {
					уровень: f"Признак уровня {уровень} для whole-1" for уровень in ("1", "2", "3", "4")
				},
				"version": 1,
			},
		)
		# Текст не строкой и отсутствующий текст — null; у базовой составляющих нет.
		self.assertEqual((итог[0]["text"], итог[2]["text"], итог[2]["parts"]), (None, None, []))

	def test_без_целей_и_чужой_ключ(self):
		self.assertEqual(компетенции_старта(self.карта, [], {}), [])
		self.assertEqual(
			компетенции_старта(self.карта, [{"competency": "no-such-skill", "target": 1}], None), []
		)
