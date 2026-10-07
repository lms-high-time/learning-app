# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Проверка релиза по публичной схеме без базы (learning-services#500)."""

import unittest

from lms_frappe_app.agent_learning.releases import schema
from lms_frappe_app.tests.release_sample import пример_релиза


def _слова(узел, найдено: set) -> set:
	"""Все слова схемы, кроме имён свойств и определений."""
	if isinstance(узел, dict):
		for слово, значение in узел.items():
			найдено.add(слово)
			if слово in ("properties", "$defs"):
				for вложенное in значение.values():
					_слова(вложенное, найдено)
			elif слово != "enum" and слово != "const":
				_слова(значение, найдено)
	elif isinstance(узел, list):
		for вложенное in узел:
			_слова(вложенное, найдено)
	return найдено


class TestСхемаРелиза(unittest.TestCase):
	def test_схема_пользуется_только_поддержанными_словами(self):
		"""`Why:` своя проверка понимает подмножество; новое слово в схеме
		молча не проверялось бы вовсе."""
		неизвестные = _слова(schema.схема(), set()) - schema.ПОДДЕРЖАНО
		self.assertFalse(неизвестные, f"проверка не понимает: {sorted(неизвестные)}")

	def test_формат_схемы_совпадает_с_константой(self):
		self.assertEqual(schema.схема()["properties"]["format"]["const"], schema.ФОРМАТ)

	def test_ключ_с_переводом_строки_не_ключ(self):
		"""`$` в Python совпадает перед конечным переводом строки — в JSON Schema нет."""
		узел = schema.схема()["$defs"]["key"]
		self.assertEqual(schema.ошибки("e1-L1", узел), [])
		self.assertTrue(schema.ошибки("e1-L1\n", узел))

	def test_целое_не_принимает_логическое(self):
		self.assertTrue(schema.ошибки(True, {"type": "integer"}))
		self.assertEqual(schema.ошибки(3, {"type": "integer"}), [])

	def test_путь_ошибки_указывает_место(self):
		ошибки = schema.ошибки({"format": "lms-release/1"})
		self.assertIn({"path": "$", "message": "нет поля «course»"}, ошибки)


class TestСхемаНаПримере(unittest.TestCase):
	def test_пример_проходит_схему(self):
		self.assertEqual(schema.ошибки(пример_релиза()), [])

	def test_agent_и_map_непрозрачны(self):
		релиз = пример_релиза()
		релиз["agent"] = {"что": ["угодно", 1, None]}
		релиз["map"] = {}
		self.assertEqual(schema.ошибки(релиз), [])

	def test_лишнее_поле_урока(self):
		релиз = пример_релиза()
		релиз["lessons"][0]["body"] = "материал"
		self.assertIn({"path": "$.lessons[0].body", "message": "лишнее поле"}, schema.ошибки(релиз))

	def test_неверный_вид_пункта(self):
		релиз = пример_релиза()
		релиз["lessons"][0]["objectives"][0]["goals"][0]["kind"] = "icon"
		self.assertEqual(
			[о["path"] for о in schema.ошибки(релиз)],
			["$.lessons[0].objectives[0].goals[0].kind"],
		)

	def test_документ_может_отсутствовать(self):
		релиз = пример_релиза()
		релиз["document"] = None
		for урок in релиз["lessons"]:
			урок["sections"] = []
		self.assertEqual(schema.ошибки(релиз), [])
