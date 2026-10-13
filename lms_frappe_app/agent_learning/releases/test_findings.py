# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Форма находок и изменений публикации (learning-services#560) — без базы."""

import json

from frappe.tests import UnitTestCase

from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases import findings

НАХОДКА = {"code": "quiz_echo", "severity": "attention", "target": "S2", "message": "Повтор практики"}


class TestФормаНаходок(UnitTestCase):
	def where(self, вызов, значение) -> str | None:
		try:
			вызов(значение)
		except Отказ as отказ:
			self.assertEqual(отказ.код, findings.НЕВЕРНЫ)
			self.assertIn(отказ.подробности["where"], отказ.сообщение)
			return отказ.подробности["where"]
		return None

	def test_находки_по_форме(self):
		без_цели = {**НАХОДКА, "target": ""}
		self.assertEqual(
			findings.находки(json.dumps([НАХОДКА, без_цели])),
			[НАХОДКА, {**НАХОДКА, "target": None}],
		)
		self.assertEqual(findings.находки([{k: v for k, v in НАХОДКА.items() if k != "target"}])[0]["target"], None)
		self.assertEqual(findings.находки([]), [])
		self.assertIsNone(findings.находки(None))

	def test_находки_не_по_форме(self):
		for что, значение, где in (
			("не JSON", "[{", "findings"),
			("не список", {"code": "x"}, "findings"),
			("больше предела", [НАХОДКА] * (findings.НАХОДОК_НЕ_БОЛЬШЕ + 1), "findings"),
			("не объект", ["строка"], "findings[0]"),
			("лишнее поле", [{**НАХОДКА, "level": 1}], "findings[0]"),
			("нет кода", [{**НАХОДКА, "code": None}], "findings[0].code"),
			("пустое сообщение", [{**НАХОДКА, "message": "  "}], "findings[0].message"),
			("длинное сообщение", [НАХОДКА, {**НАХОДКА, "message": "я" * 1001}], "findings[1].message"),
			("цель числом", [{**НАХОДКА, "target": 7}], "findings[0].target"),
		):
			with self.subTest(что):
				self.assertEqual(self.where(findings.находки, значение), где)

	def test_изменения_по_форме(self):
		пути = [{"op": "~", "path": "agent.lessons.l-1.directive"}, {"op": "+", "path": "map.situations.S2"}]
		self.assertEqual(
			findings.изменения(json.dumps({"against": 7, "paths": пути})),
			{"against": 7, "paths": пути, "truncated": False},
		)
		self.assertEqual(
			findings.изменения({"against": None}), {"against": None, "paths": [], "truncated": False}
		)
		self.assertIsNone(findings.изменения(None))

	def test_изменения_не_по_форме(self):
		путь = {"op": "~", "path": "course.title"}
		for что, значение, где in (
			("не объект", [путь], "changes"),
			("лишнее поле", {"against": 1, "paths": [], "base": 1}, "changes"),
			("нет against", {"paths": []}, "changes.against"),
			("against строкой", {"against": "7"}, "changes.against"),
			("against ноль", {"against": 0}, "changes.against"),
			("against логическое", {"against": True}, "changes.against"),
			("пути без against", {"against": None, "paths": [путь]}, "changes.paths"),
			("больше предела", {"against": 1, "paths": [путь] * (findings.ПУТЕЙ_НЕ_БОЛЬШЕ + 1)}, "changes.paths"),
			("truncated строкой", {"against": 1, "truncated": "да"}, "changes.truncated"),
			("лишнее поле пути", {"against": 1, "paths": [{**путь, "was": 1}]}, "changes.paths[0]"),
			("неизвестный знак", {"against": 1, "paths": [{**путь, "op": "*"}]}, "changes.paths[0].op"),
			("пустой путь", {"against": 1, "paths": [{**путь, "path": ""}]}, "changes.paths[0].path"),
		):
			with self.subTest(что):
				self.assertEqual(self.where(findings.изменения, значение), где)
