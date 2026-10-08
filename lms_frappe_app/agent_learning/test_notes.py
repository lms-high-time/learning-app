# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Заметки автора: адрес места, переходы статуса и чей ход.

`Why:` петля «увидел → агент поправил → принял» держится на трёх правилах:
заметка указывает точное место — по ключам релиза курса, агент не принимает
заметки за автора, а автор не отмечает их сделанными за агента; очередь
строится по тому, чьё слово последнее (lms-high-time/learning-services#266,
#512).
"""

from frappe.tests import UnitTestCase

from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.notes import (
	НЕВЕРНЫЙ_АДРЕС,
	НЕДОПУСТИМЫЙ_ПЕРЕХОД,
	адрес,
	группа,
	ждёт,
	проверить_переход,
	разобрать_адрес,
)


class TestNoteTargets(UnitTestCase):
	def test_адреса_разбираются_по_видам(self):
		случаи = {
			"course": {"kind": "course", "key": None},
			" course ": {"kind": "course", "key": None},
			"chapter.ch-1": {"kind": "chapter", "key": "ch-1"},
			"lesson.l-1": {"kind": "lesson", "key": "l-1"},
			"lesson.e1.L1": {"kind": "lesson", "key": "e1.L1"},
			"objective.l-1-D1": {"kind": "objective", "key": "l-1-D1"},
			"goal.l-1/term:T1": {"kind": "goal", "key": "l-1/term:T1"},
			"goal.l-1/l-1-D1/V1": {"kind": "goal", "key": "l-1/l-1-D1/V1"},
			"question.S1/l-1-D1": {"kind": "question", "key": "S1/l-1-D1"},
			"section.log": {"kind": "section", "key": "log"},
			"agent.frame": {"kind": "agent.frame", "key": None},
			"agent.lesson.l-1": {"kind": "agent.lesson", "key": "l-1"},
			"agent.item.l-1/term:T1": {"kind": "agent.item", "key": "l-1/term:T1"},
			"map.e3-D3": {"kind": "map", "key": "e3-D3"},
		}
		for target, ожидаемое in случаи.items():
			with self.subTest(target=target):
				разобранный = разобрать_адрес(target)
				self.assertEqual(разобранный, ожидаемое)
				self.assertEqual(адрес(разобранный), target.strip())

	def test_неверный_адрес_отклоняется(self):
		for target in (
			"",
			None,
			"lesson",
			"lesson.",
			"chapter. ch-1",
			"material",
			"directive.teaching_directive",
			"course_directive.glossary",
			"block.register/risks",
			"course.x",
			"agent",
			"agent.frame.x",
			"agent.lesson.",
			"goal.l-1",
			"goal./term:T1",
			"goal.l-1/",
			"agent.item.l-1",
			"map.",
		):
			with self.subTest(target=target):
				with self.assertRaises(Отказ) as пойманный:
					разобрать_адрес(target)
				self.assertEqual(пойманный.exception.код, НЕВЕРНЫЙ_АДРЕС)
				self.assertEqual(пойманный.exception.подробности["where"], "target")


class TestNoteTransitions(UnitTestCase):
	def test_допустимые_переходы(self):
		for было, стало, via, текст in (
			("open", "done", "agent", "Переписал шаг 3"),
			("done", "accepted", "author", None),
			("open", "accepted", "author", None),
			("done", "open", "author", "Пример всё ещё про кафе"),
			("accepted", "open", "author", "Всплыло снова"),
		):
			with self.subTest(было=было, стало=стало, via=via):
				проверить_переход(было, стало, via, текст)

	def test_недопустимые_переходы(self):
		for было, стало, via, текст in (
			("done", "accepted", "agent", None),
			("open", "accepted", "agent", None),
			("open", "done", "author", "Сам поправил"),
			("open", "done", "agent", None),
			("open", "done", "agent", "   "),
			("done", "open", "author", None),
			("accepted", "open", "author", ""),
			("open", "open", "author", "Ещё раз"),
			("done", "done", "agent", "Ещё раз"),
			("accepted", "done", "agent", "Сделал"),
			("open", "closed", "author", None),
			("open", "done", "robot", "Сделал"),
		):
			with self.subTest(было=было, стало=стало, via=via, текст=текст):
				with self.assertRaises(Отказ) as пойманный:
					проверить_переход(было, стало, via, текст)
				self.assertEqual(пойманный.exception.код, НЕДОПУСТИМЫЙ_ПЕРЕХОД)


class TestWhoseTurn(UnitTestCase):
	def ответ(self, via: str) -> dict:
		return {"via": via, "text": "…"}

	def test_чей_ход(self):
		случаи = (
			("open", "author", [], "agent"),
			("open", "agent", [], "author"),
			("open", "author", [self.ответ("agent")], "author"),
			("open", "agent", [self.ответ("author")], "agent"),
			("open", "author", [self.ответ("agent"), self.ответ("author")], "agent"),
			("done", "author", [self.ответ("agent")], "author"),
			("accepted", "author", [self.ответ("author")], None),
		)
		for статус, via, ответы, ожидаемое in случаи:
			with self.subTest(статус=статус, via=via, ответов=len(ответы)):
				self.assertEqual(ждёт(статус, via, ответы), ожидаемое)

	def test_группа_очереди(self):
		self.assertEqual(группа("done", "author"), "check")
		self.assertEqual(группа("open", "author"), "question")
		self.assertEqual(группа("open", "agent"), "agent")
		self.assertEqual(группа("accepted", None), "accepted")
