# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Блокировки, показанный уровень и очередь агента (learning-services#810)."""

import json

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.agent_learning.competencies import queue
from lms_frappe_app.tests.release_sample import пример_релиза, с_компетенциями


class _Запись(dict):
	__getattr__ = dict.get


def запись(имя, компетенция, исход, уровень=0, вид="Evidence", выведено=None):
	return _Запись(
		name=имя,
		kind=вид,
		competency=компетенция,
		outcome=исход,
		level=уровень,
		lesson_key="l-1",
		withdrawn=json.dumps(выведено) if выведено is not None else None,
	)


КАРТА = с_компетенциями(пример_релиза())["competencies"]
ЦЕЛИ = {
	"l-1": [{"competency": "B1", "target": 2}],
	"l-3": [{"competency": "B2", "target": 2}, {"competency": "I1", "target": 3}],
}


class TestУровни(UnitTestCase):
	def test_открытые_уровни_по_начатым(self):
		self.assertEqual(queue.открытые_уровни(ЦЕЛИ, {"l-1"}), {"B1": 2})
		self.assertEqual(queue.открытые_уровни(ЦЕЛИ, {"l-1", "l-3"}), {"B1": 2, "B2": 2, "I1": 3})
		self.assertEqual(queue.открытые_уровни(ЦЕЛИ, set()), {})

	def test_показанный_уровень(self):
		for уровень, открытый, показан in ((3, 2, 2), (1, 3, 1), (None, 2, None), (2, 0, None)):
			with self.subTest(уровень=уровень, открытый=открытый):
				self.assertEqual(queue.показанный_уровень(уровень, открытый), показан)


class TestЗатрудненияПосле(UnitTestCase):
	def test_открытие_и_выход(self):
		for случай, записи, итог in (
			("затруднение без подтверждения — не «после»", [запись("e1", "B1", "difficulty", 2)], {}),
			(
				"затруднение не выше подтверждённого",
				[запись("e1", "B1", "confirmed", 3), запись("e2", "B1", "difficulty", 2)],
				{"B1": 2},
			),
			(
				"затруднение выше подтверждённого — не противоречие",
				[запись("e1", "B1", "confirmed", 1), запись("e2", "B1", "difficulty", 2)],
				{},
			),
			(
				"подтверждение не ниже затруднения закрывает",
				[
					запись("e1", "B1", "confirmed", 3),
					запись("e2", "B1", "difficulty", 2),
					запись("e3", "B1", "confirmed", 2),
				],
				{},
			),
			(
				"подтверждение ниже затруднения не закрывает",
				[
					запись("e1", "B1", "confirmed", 3),
					запись("e2", "B1", "difficulty", 3),
					запись("e3", "B1", "confirmed", 2),
				],
				{"B1": 3},
			),
			(
				"пропуск не закрывает",
				[
					запись("e1", "B1", "confirmed", 3),
					запись("e2", "B1", "difficulty", 2),
					запись("e3", "B1", "skipped"),
				],
				{"B1": 2},
			),
			(
				"пересмотр закрывает",
				[
					запись("e1", "B1", "confirmed", 3),
					запись("e2", "B1", "difficulty", 2),
					запись("r1", "B1", None, 1, вид="Revision", выведено=["e1"]),
				],
				{},
			),
		):
			with self.subTest(случай):
				self.assertEqual(queue.затруднения_после(записи), итог)


class TestОчередь(UnitTestCase):
	def test_состав_и_порядок(self):
		записи = [
			запись("e1", "B2", "confirmed", 2),
			запись("e2", "B2", "difficulty", 1),
			запись("e3", "B1", "skipped"),
		]
		# l-1 пройден: B1 без подтверждения — в очереди; I1 урока l-3 не пройден — нет.
		статусы = {"l-1": "passed", "l-3": "in_progress"}
		очередь = queue.очередь_по_данным(КАРТА, ЦЕЛИ, статусы, записи, "l-2")
		self.assertEqual(
			[(п["competency"], п["reason"], п["level"], п["last_outcome"]) for п in очередь],
			[
				("B1", "unconfirmed", None, "skipped"),
				("B2", "difficulty_after_confirmation", 1, "difficulty"),
			],
		)
		self.assertEqual(очередь[0]["title"], "Выбор устройства")
		# Связанное с текущим уроком поднимается над прочим, даже если в карте оно позже.
		карта = [*КАРТА, {"key": "B3", "kind": "base", "title": "Третья"}]
		цели = {**ЦЕЛИ, "l-9": [{"competency": "B3", "target": 2}]}
		записи_b3 = [*записи, запись("e4", "B3", "confirmed", 2), запись("e5", "B3", "difficulty", 2)]
		self.assertEqual(
			[п["competency"] for п in queue.очередь_по_данным(карта, цели, статусы, записи_b3, "l-9")],
			["B3", "B1", "B2"],
		)

	def test_подтверждённое_ниже_цели_не_в_очереди(self):
		очередь = queue.очередь_по_данным(
			КАРТА, ЦЕЛИ, {"l-1": "passed"}, [запись("e1", "B1", "confirmed", 1)]
		)
		self.assertEqual(очередь, [])


class IntegrationTestПредложениеОчереди(IntegrationTestCase):
	def test_последний_урок_один_раз_предлагает_очередь(self):
		from lms_frappe_app.api import student
		from lms_frappe_app.tests.sample_data import (
			зачислить_на_курс,
			курс_из_релиза,
			настроить_квиз,
			отметить_все_пункты,
			политика_по_умолчанию,
			создать_ученика,
			урок_релиза,
		)

		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(политика_по_умолчанию)
		frappe.set_user("Administrator")
		настроить_квиз(quiz_required=0)
		ключ = f"queue-{frappe.generate_hash(length=6)}"
		курс, _ = курс_из_релиза(релиз=с_компетенциями(пример_релиза(ключ)))
		ученик = создать_ученика(f"{ключ}@example.com")
		зачислить_на_курс(ученик, курс)
		frappe.set_user(ученик)

		def пройти(урок: str, свидетельства: list[tuple[str, str]]) -> dict:
			старт = student.start_lesson(lesson=урок_релиза(курс, урок))["data"]
			занятие = старт["session"]
			отметить_все_пункты(frappe.db.get_value("Agent Learning Session", занятие, "run"), занятие)
			for компетенция, ключ_запроса in свидетельства:
				ответ = student.record_evidence(
					занятие, компетенция, "skipped", ключ_запроса, task="Предложил"
				)
				self.assertTrue(ответ["ok"], ответ)
			return {"старт": старт, "закрытие": student.complete_lesson(занятие), "занятие": занятие}

		# l-1: B1 пропущена — уйдёт в очередь после закрытия урока.
		self.assertTrue(пройти("l-1", [("B1", "q-1")])["закрытие"]["ok"])
		self.assertTrue(пройти("l-2", [])["закрытие"]["ok"])
		последний = пройти("l-3", [("B2", "q-2"), ("I1", "q-3")])
		self.assertEqual(
			[(п["competency"], п["reason"]) for п in последний["старт"]["competency_queue"]],
			[("B1", "unconfirmed")],
		)
		отказ = последний["закрытие"]
		self.assertEqual(отказ["error"]["code"], "course_queue_open", отказ)
		self.assertEqual([п["competency"] for п in отказ["error"]["competencies"]], ["B1"])
		# Предложено один раз: повторный вызов закрывает урок.
		self.assertTrue(student.complete_lesson(последний["занятие"])["ok"])
