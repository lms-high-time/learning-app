# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Проверки релиза сверх схемы без базы (learning-services#500)."""

import unittest

from lms_frappe_app.agent_learning.releases import checks
from lms_frappe_app.tests.release_sample import пример_релиза


def _коды(релиз) -> list[str]:
	return [п["code"] for п in checks.проблемы(релиз)[0]]


class TestПроверкиРелиза(unittest.TestCase):
	def test_пример_чист(self):
		self.assertEqual(checks.проблемы(пример_релиза()), ([], []))

	def test_повтор_ключа_урока(self):
		р = пример_релиза()
		р["lessons"][1]["key"] = "l-1"
		self.assertIn("duplicate_key", _коды(р))

	def test_пункт_с_тем_же_ключом_в_другом_уроке_допустим(self):
		# term:T1 и refute:M1 стоят в каждом уроке примера — пространство пункта — урок.
		self.assertNotIn("duplicate_key", _коды(пример_релиза()))

	def test_вопрос_на_чужую_цель(self):
		р = пример_релиза()
		р["lessons"][0]["quiz"]["questions"][0]["objective"] = "l-2-D1"
		self.assertIn("broken_ref", _коды(р))

	def test_раздел_урока_не_из_документа(self):
		р = пример_релиза()
		р["lessons"][0]["sections"] = ["nope"]
		self.assertIn("broken_ref", _коды(р))

	def test_уроки_глав_не_в_порядке_уроков(self):
		р = пример_релиза()
		р["chapters"][0]["lessons"] = ["l-2", "l-1"]
		self.assertIn("chapter_order", _коды(р))

	def test_верный_ответ_не_из_вариантов(self):
		р = пример_релиза()
		вопрос = р["lessons"][0]["quiz"]["questions"][0]["key"]
		р["lessons"][0]["quiz"]["answers"][вопрос]["correct"] = "V9"
		self.assertIn("quiz_correct", _коды(р))

	def test_вопрос_без_ответа(self):
		р = пример_релиза()
		р["lessons"][0]["quiz"]["answers"] = {}
		self.assertIn("quiz_correct", _коды(р))

	def test_ключ_раздела_не_ложится_на_документ(self):
		р = пример_релиза()
		р["document"]["sections"][0]["key"] = "Log-1"
		р["lessons"][0]["sections"] = р["lessons"][1]["sections"] = ["Log-1"]
		self.assertIn("document_key", _коды(р))

	def test_раздел_с_ключом_вида_страницы(self):
		р = пример_релиза()
		р["document"]["sections"][0]["key"] = "table"
		р["lessons"][0]["sections"] = р["lessons"][1]["sections"] = ["table"]
		self.assertIn("document_key", _коды(р))

	def test_поле_раздела_one_совпадает_с_колонкой(self):
		р = пример_релиза()
		р["document"]["sections"][1]["columns"][0]["key"] = "topic"
		self.assertIn("document_key", _коды(р))

	def test_длинное_название_урока(self):
		р = пример_релиза()
		р["lessons"][0]["title"] = "у" * 136
		self.assertIn("text_too_long", _коды(р))

	def test_длинное_название_главы(self):
		"""Имя главы Learning — тоже `{####} {название}`."""
		р = пример_релиза()
		р["chapters"][0]["title"] = "г" * 136
		self.assertIn("text_too_long", _коды(р))

	def test_пустые_тексты_ученику_предупреждения(self):
		р = пример_релиза()
		р["course"]["summary"] = ""
		р["chapters"][0]["description"] = ""
		критичные, предупреждения = checks.проблемы(р)
		self.assertEqual(критичные, [])
		self.assertEqual(
			[(п["code"], п["where"]) for п in предупреждения],
			[("public_text_empty", "course.summary"), ("public_text_empty", "chapters[ch-1].description")],
		)
