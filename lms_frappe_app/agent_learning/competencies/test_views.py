# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Представления карты компетенций: ученик, организация, автор (learning-services#810)."""

import json

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.agent_learning.competencies import views
from lms_frappe_app.tests.release_sample import пример_релиза, с_компетенциями


class _Запись(dict):
	__getattr__ = dict.get


def запись(имя, компетенция, исход, уровень=0, вид="Evidence", выведено=None, ученик="u1", **ещё):
	return _Запись(
		name=имя,
		kind=вид,
		competency=компетенция,
		outcome=исход,
		level=уровень,
		student=ученик,
		lesson_key="l-1",
		task=f"задание {имя}",
		student_action=f"действие {имя}",
		assessment=f"оценка {имя}",
		uncertainties=None,
		withdrawn=json.dumps(выведено) if выведено is not None else None,
		**ещё,
	)


КАРТА = с_компетенциями(пример_релиза())["competencies"]
ЦЕЛИ = {
	"l-1": [{"competency": "B1", "target": 2}],
	"l-3": [{"competency": "B2", "target": 2}, {"competency": "I1", "target": 3}],
}


class TestУровни(UnitTestCase):
	def test_три_состояния_и_потолок_открытого(self):
		записи = [запись("e1", "B1", "confirmed", 3), запись("e2", "B2", "difficulty", 2)]
		уровни = {у["key"]: у for у in views.уровни_по_данным(КАРТА, ЦЕЛИ, {"l-1": "passed"}, записи)}
		# B1 подтверждена на 3, открыто до 2 — показано 2; B2 и I1 урока l-3 не начатого — закрыты.
		self.assertEqual(
			(уровни["B1"]["status"], уровни["B1"]["level"], уровни["B1"]["open_level"]), ("level", 2, 2)
		)
		self.assertEqual((уровни["B2"]["status"], уровни["I1"]["status"]), ("locked", "locked"))
		уровни = {
			у["key"]: у
			for у in views.уровни_по_данным(КАРТА, ЦЕЛИ, {"l-1": "passed", "l-3": "in_progress"}, записи)
		}
		self.assertEqual((уровни["B2"]["status"], уровни["B2"]["level"]), ("no_info", None))


class TestОснование(UnitTestCase):
	def уровень(self, статус, показан=None):
		return {"key": "B1", "status": статус, "level": показан}

	def test_основание_уровня_не_выше_показанного(self):
		записи = [запись("e1", "B1", "confirmed", 1), запись("e2", "B1", "confirmed", 3)]
		основание = views.основание(self.уровень("level", 2), записи)
		self.assertEqual((основание["level"], основание["task"]), (1, "задание e1"))

	def test_только_скрытое_подтверждение_без_уровня_и_оценки(self):
		основание = views.основание(self.уровень("level", 2), [запись("e1", "B1", "confirmed", 4)])
		self.assertEqual(
			основание, {"outcome": "confirmed", "task": "задание e1", "student_action": "действие e1"}
		)

	def test_нет_информации_последнее_и_заблокировано(self):
		записи = [запись("e1", "B1", "skipped"), запись("e2", "B1", "difficulty", 2)]
		основание = views.основание(self.уровень("no_info"), записи)
		self.assertEqual((основание["outcome"], основание["assessment"]), ("difficulty", "оценка e2"))
		self.assertIsNone(views.основание(self.уровень("no_info"), []))
		self.assertIsNone(views.основание(self.уровень("locked"), записи))


class TestСводка(UnitTestCase):
	def test_по_компетенциям_и_целям(self):
		записи = [
			запись("e1", "B1", "confirmed", 2, task_form="choice", hints=0, corrected=0, indirect=0),
			запись(
				"e2", "B1", "confirmed", 2, ученик="u2", task_form="free", hints=1, corrected=0, indirect=0
			),
			запись(
				"e3", "B1", "difficulty", 1, ученик="u2", task_form="free", hints=0, corrected=0, indirect=0
			),
			запись("r1", "B1", None, 1, вид="Revision", ученик="u2", выведено=["e2"]),
		]
		прохождения = [
			_Запись(student="u1", lesson_key="l-1", status="passed"),
			_Запись(student="u2", lesson_key="l-1", status="passed"),
		]
		сводка = views.сводка_по_данным(КАРТА, ЦЕЛИ, прохождения, записи)
		b1 = next(к for к in сводка["competencies"] if к["key"] == "B1")
		self.assertEqual(b1["evidence"], {"confirmed": 2, "difficulty": 1, "insufficient": 0, "skipped": 0})
		self.assertEqual(b1["confirmed_forms"], {"choice": 1, "free": 1, "work": 0})
		self.assertEqual((b1["confirmed_with_hints"], b1["withdrawn"], b1["students"]), (1, 1, 2))
		# У u2 подтверждение выведено пересмотром — цель урока у него не достигнута.
		self.assertEqual(
			сводка["targets"][0],
			{"lesson": "l-1", "competency": "B1", "target": 2, "passed": 2, "reached": 1},
		)


class IntegrationTestКартаУченика(IntegrationTestCase):
	def test_карта_ученика_через_метод(self):
		from lms_frappe_app.api import student
		from lms_frappe_app.tests.sample_data import (
			зачислить_на_курс,
			курс_из_релиза,
			создать_ученика,
			урок_релиза,
		)

		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		ключ = f"views-{frappe.generate_hash(length=6)}"
		курс, _ = курс_из_релиза(релиз=с_компетенциями(пример_релиза(ключ)))
		ученик = создать_ученика(f"{ключ}@example.com")
		зачислить_на_курс(ученик, курс)
		frappe.set_user(ученик)
		занятие = student.start_lesson(lesson=урок_релиза(курс, "l-1"))["data"]["session"]
		student.record_evidence(занятие, "B1", "skipped", "v-1", task="Предложил")

		карта = student.my_competencies(course=курс)
		self.assertTrue(карта["ok"], карта)
		по_ключу = {к["key"]: к for к in карта["data"]["competencies"]}
		self.assertEqual(
			(по_ключу["B1"]["status"], по_ключу["B1"]["basis"]["outcome"]), ("no_info", "skipped")
		)
		self.assertEqual(по_ключу["I1"]["status"], "locked")
