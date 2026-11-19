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

	def test_после_пересмотра_основание_причина_а_не_выведенное(self):
		записи = [
			запись("e1", "B1", "confirmed", 2),
			запись("r1", "B1", None, 0, вид="Revision", выведено=["e1"]),
		]
		self.assertEqual(
			views.основание(self.уровень("no_info"), записи),
			{"outcome": "revised", "assessment": "оценка r1"},
		)

	def test_скрытое_наименьшее_а_при_равных_последнее(self):
		записи = [
			запись("e1", "B1", "confirmed", 4),
			запись("e2", "B1", "confirmed", 3),
			запись("e3", "B1", "confirmed", 3),
		]
		self.assertEqual(views.основание(self.уровень("level", 2), записи)["task"], "задание e3")

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


class IntegrationTestПредставленияРуководителяИАвтора(IntegrationTestCase):
	def test_руководителю_только_уровни_автору_сводка(self):
		from lms_frappe_app.api import authoring, manager, student
		from lms_frappe_app.tests.sample_data import (
			добавить_в_организацию,
			зачислить_на_курс,
			курс_из_релиза,
			создать_куратора,
			создать_менеджера,
			создать_организацию,
			создать_ученика,
			урок_релиза,
		)

		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		ключ = f"views-mg-{frappe.generate_hash(length=6)}"
		курс, _ = курс_из_релиза(релиз=с_компетенциями(пример_релиза(ключ)))
		организация = создать_организацию(f"Карта {ключ}")
		ученик = создать_ученика(f"{ключ}@example.com")
		добавить_в_организацию(ученик, организация)
		# Как в воротах утечек: зачисление, затем назначение курса организацией.
		зачислить_на_курс(ученик, курс)
		frappe.get_doc({"doctype": "Course Allocation", "organization": организация, "course": курс}).insert(
			ignore_permissions=True
		)
		менеджер = создать_менеджера(f"{ключ}-mg@example.com", организация)
		чужой_автор = создать_куратора(f"{ключ}-author@example.com")

		frappe.set_user(ученик)
		занятие = student.start_lesson(lesson=урок_релиза(курс, "l-1"))["data"]["session"]
		ответ = student.record_evidence(
			занятие,
			"B1",
			"confirmed",
			"mg-1",
			level=2,
			task="Объяснить выбор",
			student_action="Назвал свойства задачи",
			assessment="Своими словами",
			task_form="free",
			hints=False,
			corrected=False,
			indirect=False,
		)
		self.assertTrue(ответ["ok"], ответ)

		frappe.set_user(менеджер)
		ожидаемое = [
			{"key": "B1", "title": "Выбор устройства", "status": "level", "level": 2},
			{"key": "B2", "title": "Цена ошибочного действия", "status": "locked", "level": None},
			{"key": "I1", "title": "Обоснование устройства", "status": "locked", "level": None},
		]
		# Руководитель — тоже участник организации: строка ученика — по пользователю.
		строка = next(
			с for с in manager.org_report()["data"]["rows"] if с["course"] == курс and с["user"] == ученик
		)
		self.assertEqual(строка["competencies"], ожидаемое)
		курс_ученика = next(к for к in manager.student_detail(ученик)["data"]["courses"] if к["id"] == курс)
		self.assertEqual(курс_ученика["competencies"], ожидаемое)

		frappe.set_user("Administrator")
		сводка = authoring.competency_summary(course=курс)["data"]
		b1 = next(к for к in сводка["competencies"] if к["key"] == "B1")
		self.assertEqual((b1["evidence"]["confirmed"], b1["students"], b1["students_closed"]), (1, 1, 1))
		# Автор не своего курса — 403, как у прохождений.
		frappe.set_user(чужой_автор)
		with self.assertRaises(frappe.PermissionError):
			authoring.competency_summary(course=курс)
