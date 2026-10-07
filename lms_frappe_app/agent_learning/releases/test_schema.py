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

	def test_целое_принимает_целый_float(self):
		"""`3.0` в JSON Schema — целое: тип по значению, а не по записи."""
		self.assertEqual(schema.ошибки(3.0, {"type": "integer"}), [])
		self.assertTrue(schema.ошибки(3.5, {"type": "integer"}))

	def test_не_число_и_бесконечность_не_числа(self):
		"""`json.loads` пропускает `NaN` и `Infinity`, в JSON их нет."""
		for значение in (float("nan"), float("inf"), float("-inf")):
			self.assertTrue(schema.ошибки(значение, {"type": "number"}), значение)
			self.assertTrue(
				schema.ошибки(значение, {"type": "number", "minimum": 0, "maximum": 100}), значение
			)

	def test_слова_рядом_со_ссылкой_проверяются(self):
		"""В 2020-12 `$ref` — одно из слов узла: соседние проверяются вместе с целью ссылки."""
		узел = {"$ref": "#/$defs/key", "maxLength": 3}
		self.assertEqual(schema.ошибки("abc", узел), [])
		self.assertEqual([о["message"] for о in schema.ошибки("abcd", узел)], ["строка длиннее 3"])
		self.assertEqual(len(schema.ошибки("a b c d", узел)), 2)

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

	def test_вариант_с_null_отдаёт_ошибки_самого_значения(self):
		"""`anyOf` с `null`: не-null значение разбирается по своему варианту — автор
		видит лишнее поле колонки, а не «не подходит ни под один вариант» у документа."""
		релиз = пример_релиза()
		релиз["document"]["sections"][0]["columns"][0]["hint"] = "подсказка"
		self.assertEqual(
			schema.ошибки(релиз),
			[{"path": "$.document.sections[0].columns[0].hint", "message": "лишнее поле"}],
		)

	def test_отрицательный_срок_домашки(self):
		релиз = пример_релиза()
		релиз["lessons"][2]["homework"]["due_days"] = -1
		self.assertEqual(
			schema.ошибки(релиз), [{"path": "$.lessons[2].homework.due_days", "message": "меньше 0"}]
		)

	def test_условие_обязательности_с_лишним_полем(self):
		релиз = пример_релиза()
		релиз["document"]["sections"][0]["columns"][2]["required"]["also"] = "x"
		self.assertEqual(
			[о["path"] for о in schema.ошибки(релиз)],
			["$.document.sections[0].columns[2].required.also"],
		)

	def test_ни_один_вариант_по_типу(self):
		релиз = пример_релиза()
		релиз["document"] = "тетрадь"
		self.assertEqual(
			schema.ошибки(релиз), [{"path": "$.document", "message": "не подходит ни под один вариант"}]
		)
