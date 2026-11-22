# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Компетенции урока для старта без базы (learning-services#807)."""

from frappe.tests import UnitTestCase

from lms_frappe_app.agent_learning.competencies.lesson import компетенции_старта
from lms_frappe_app.tests.release_sample import пример_релиза, с_компетенциями


class TestКомпетенцииСтарта(UnitTestCase):
	def setUp(self):
		self.карта = с_компетенциями(пример_релиза())["competencies"]

	def test_цели_урока_затем_составляющие_без_цели(self):
		"""Урок с B2 и I1: сначала цели урока по порядку, затем B1 — составляющая I1 без цели."""
		итог = компетенции_старта(
			self.карта,
			[{"competency": "B2", "target": 2}, {"competency": "I1", "target": 3}],
			{"I1": "Признаки уровней I1.", "B1": ["не", "строка"]},
		)
		self.assertEqual([(к["key"], к["target"]) for к in итог], [("B2", 2), ("I1", 3), ("B1", None)])
		i1 = итог[1]
		self.assertEqual(
			i1,
			{
				"key": "I1",
				"kind": "integrative",
				"title": "Обоснование устройства",
				"description": "Обосновываете устройство целиком.",
				"target": 3,
				"parts": [{"competency": "B1", "from_level": 1}, {"competency": "B2", "from_level": 3}],
				"text": "Признаки уровней I1.",
				# Признаки уровней и версия — у компетенции каталога; у карты внутри релиза их нет.
				"levels": None,
				"version": None,
			},
		)
		# Текст не строкой и отсутствующий текст — null; у базовой составляющих нет.
		self.assertEqual((итог[0]["text"], итог[2]["text"], итог[2]["parts"]), (None, None, []))

	def test_без_целей_и_чужой_ключ(self):
		self.assertEqual(компетенции_старта(self.карта, [], {}), [])
		self.assertEqual(компетенции_старта(self.карта, [{"competency": "B9", "target": 1}], None), [])
