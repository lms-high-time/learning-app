# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Язык формул документа без базы (learning-services#330)."""

import unittest

from lms_frappe_app.agent_learning.artifacts import formulas
from lms_frappe_app.agent_learning.errors import Отказ


class TestФормулы(unittest.TestCase):
	def test_арифметика_и_сравнения(self):
		self.assertEqual(formulas.вычислить(formulas.разобрать("a * b + 1"), {"a": 3, "b": 4}), 13)
		self.assertTrue(formulas.вычислить(formulas.разобрать("(a - 1) >= 2 and not b"), {"a": 3, "b": False}))

	def test_пусто_вместо_значения(self):
		for случай, формула, значения in (
			("пустой операнд", "a * b", {"a": 3}),
			("деление на ноль", "a / b", {"a": 3, "b": 0}),
		):
			with self.subTest(случай):
				self.assertIsNone(formulas.вычислить(formulas.разобрать(формула), значения))

	def test_мусор_не_разбирается(self):
		for формула in ("a *", "__import__('os')", "a ** b", "(a"):
			with self.subTest(формула), self.assertRaises(Отказ):
				formulas.разобрать(формула)


if __name__ == "__main__":
	unittest.main()
