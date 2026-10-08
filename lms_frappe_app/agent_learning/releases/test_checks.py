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

	def test_длинное_название_домашки(self):
		"""Название шаблона `Agent Lesson Homework` — Data, без номера."""
		р = пример_релиза()
		р["lessons"][2]["homework"]["title"] = "д" * 141
		self.assertIn(
			("text_too_long", "lessons[l-3].homework.title"),
			[(п["code"], п["where"]) for п in checks.проблемы(р)[0]],
		)

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

	def test_ответы_квиза_в_пакете_агента(self):
		"""`answers` и `correct` на любой глубине пакета — `agent_leak`, путь — до ключа."""
		р = пример_релиза()
		р["agent"]["lessons"]["l-1"]["answers"] = {"S1/l-1-D1": "V1"}
		р["agent"]["learn_about_student"].append({"key": "x", "text": "т", "changes": [{"correct": "V1"}]})
		self.assertEqual(
			[(п["code"], п["where"]) for п in checks.проблемы(р)[0]],
			[
				("agent_leak", "agent.learn_about_student[2].changes[0].correct"),
				("agent_leak", "agent.lessons.l-1.answers"),
			],
		)

	def test_ответы_вне_пакета_агента_не_утечка(self):
		"""Ключи с тем же смыслом в тексте или в карте курса — не утечка."""
		р = пример_релиза()
		р["agent"]["frame"] = "answers и correct — слова, а не ключи"
		р["map"] = {"answers": {"correct": "V1"}}
		self.assertEqual(checks.проблемы(р), ([], []))

	def test_релиз_без_пакета_агента_чист(self):
		р = пример_релиза()
		del р["agent"]
		self.assertEqual(checks.проблемы(р), ([], []))
