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

	def test_значение_по_узлу(self):
		ключ = schema.схема()["$defs"]["key"]
		число = {"type": "number"}
		for случай, значение, узел, ошибка in (
			("ключ", "e1-L1", ключ, False),
			# `$` в Python совпадает перед конечным переводом строки — в JSON Schema нет.
			("ключ с переводом строки", "e1-L1\n", ключ, True),
			("целое: логическое", True, {"type": "integer"}, True),
			("целое: 3", 3, {"type": "integer"}, False),
			# `3.0` в JSON Schema — целое: тип по значению, а не по записи.
			("целое: 3.0", 3.0, {"type": "integer"}, False),
			("целое: 3.5", 3.5, {"type": "integer"}, True),
			# `json.loads` пропускает `NaN` и `Infinity`, в JSON их нет.
			*(
				(f"число{с_границами}: {значение}", значение, узел, True)
				for значение in (float("nan"), float("inf"), float("-inf"))
				for с_границами, узел in (
					("", число),
					(" с границами", {**число, "minimum": 0, "maximum": 100}),
				)
			),
		):
			with self.subTest(случай):
				if ошибка:
					self.assertTrue(schema.ошибки(значение, узел))
				else:
					self.assertEqual(schema.ошибки(значение, узел), [])

	def test_слова_рядом_со_ссылкой_проверяются(self):
		"""В 2020-12 `$ref` — одно из слов узла: соседние проверяются вместе с целью ссылки."""
		узел = {"$ref": "#/$defs/key", "maxLength": 3}
		self.assertEqual(schema.ошибки("abc", узел), [])
		self.assertEqual([о["message"] for о in schema.ошибки("abcd", узел)], ["строка длиннее 3"])
		self.assertEqual(len(schema.ошибки("a b c d", узел)), 2)

	def test_путь_ошибки_указывает_место(self):
		ошибки = schema.ошибки({"format": "lms-release/1"})
		self.assertIn({"path": "$", "message": "нет поля «course»"}, ошибки)


def _без_документа(релиз: dict) -> None:
	релиз["document"] = None
	for урок in релиз["lessons"]:
		урок["sections"] = []


class TestСхемаНаПримере(unittest.TestCase):
	def test_проходит_схему(self):
		for случай, правка in {
			"пример как есть": lambda релиз: None,
			"agent и map непрозрачны": lambda релиз: релиз.update(agent={"что": ["угодно", 1, None]}, map={}),
			"документ может отсутствовать": _без_документа,
		}.items():
			with self.subTest(случай):
				релиз = пример_релиза()
				правка(релиз)
				self.assertEqual(schema.ошибки(релиз), [])

	def test_лишнее_поле_урока(self):
		релиз = пример_релиза()
		релиз["lessons"][0]["body"] = "материал"
		self.assertIn({"path": "$.lessons[0].body", "message": "лишнее поле"}, schema.ошибки(релиз))

	def test_путь_ошибки_по_правке(self):
		for случай, правка, путь in (
			(
				"неверный вид пункта",
				lambda релиз: релиз["lessons"][0]["objectives"][0]["goals"][0].update(kind="icon"),
				"$.lessons[0].objectives[0].goals[0].kind",
			),
			(
				"условие обязательности с лишним полем",
				lambda релиз: релиз["document"]["sections"][0]["columns"][2]["required"].update(also="x"),
				"$.document.sections[0].columns[2].required.also",
			),
		):
			with self.subTest(случай):
				релиз = пример_релиза()
				правка(релиз)
				self.assertEqual([о["path"] for о in schema.ошибки(релиз)], [путь])

	def test_ошибка_по_правке(self):
		for случай, правка, ошибка in (
			(
				# `anyOf` с `null`: не-null значение разбирается по своему варианту — автор
				# видит лишнее поле колонки, а не «не подходит ни под один вариант» у документа.
				"вариант с null отдаёт ошибки самого значения",
				lambda релиз: релиз["document"]["sections"][0]["columns"][0].update(hint="подсказка"),
				{"path": "$.document.sections[0].columns[0].hint", "message": "лишнее поле"},
			),
			(
				"отрицательный срок домашки",
				lambda релиз: релиз["lessons"][2]["homework"].update(due_days=-1),
				{"path": "$.lessons[2].homework.due_days", "message": "меньше 0"},
			),
			(
				"ни один вариант по типу",
				lambda релиз: релиз.update(document="тетрадь"),
				{"path": "$.document", "message": "не подходит ни под один вариант"},
			),
		):
			with self.subTest(случай):
				релиз = пример_релиза()
				правка(релиз)
				self.assertEqual(schema.ошибки(релиз), [ошибка])
