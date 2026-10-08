# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Читатели целей урока курса из релиза — по прохождению (learning-services#506).

`course_outline`, `public.course_map`, `lesson_session`, прогресс в `remember`
и `update_artifact`, `manager.student_detail`: уровень целей урока, статусы —
сохранённые в прохождении. Читающие методы прохождений не заводят и не сверяют.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.api import manager, public, student
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	курс_из_релиза,
	создать_занятие,
	создать_менеджера,
	создать_организацию,
	создать_урок,
	создать_ученика,
	урок_релиза,
)

ЦЕЛЬ_1 = {"key": "l-1-D1", "text": "Цель урока «Урок первый»"}
ЖУРНАЛ = {"key": "log", "title": "Журнал", "description": "Что сюда записывают."}


class IntegrationTestLessonReaders(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ключ_курса = f"readers-{суффикс}"
		self.курс, self.релиз = курс_из_релиза(релиз=пример_релиза(self.ключ_курса))
		self.ученик = создать_ученика(f"readers-{суффикс}@example.com")
		организация = создать_организацию(f"Читатели {суффикс}")
		добавить_в_организацию(self.ученик, организация)
		frappe.get_doc(
			{"doctype": "Course Allocation", "organization": организация, "course": self.курс}
		).insert(ignore_permissions=True)
		self.менеджер = создать_менеджера(f"readers-mg-{суффикс}@example.com", организация)
		self.урок = урок_релиза(self.курс, "l-1")
		frappe.set_user(self.ученик)

	def данные(self, ответ: dict) -> dict:
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def отказ(self, ответ: dict, код: str) -> dict:
		self.assertFalse(ответ["ok"], ответ)
		self.assertEqual(ответ["error"]["code"], код, ответ)
		return ответ["error"]

	def начать_урок(self) -> str:
		"""Занятие по `l-1` с отметкой первого пункта: цель урока `touched`."""
		run = прохождения.прохождение(self.ученик, self.курс, "l-1")
		занятие = создать_занятие(self.ученик, self.урок, run=run.name)
		прохождения.отметить(run.name, "term:T1", "done", "Назвал термин", занятие=занятие)
		return занятие

	def курс_старой_модели(self) -> str:
		frappe.set_user("Administrator")
		урок = создать_урок(f"Старый {frappe.generate_hash(length=6)}")
		зачислить(self.ученик, урок)
		frappe.get_doc(
			{"doctype": "Agent Lesson Directive", "lesson": урок, "objectives": "Цель директивы"}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)
		return урок

	# --- course_outline ---

	def test_дерево_курса_из_релиза(self):
		self.начать_урок()

		дерево = self.данные(student.course_outline(self.курс))

		self.assertEqual(set(дерево), {"course", "chapters", "programs"})
		первая, вторая = дерево["chapters"]
		self.assertEqual(
			{к: в for к, в in первая.items() if к != "lessons"},
			{
				"key": "ch-1",
				"title": "Глава первая",
				"description": "Что изменится после первой главы.",
				"status": "in_progress",
				"lessons_total": 2,
				"lessons_started": 1,
				"lessons_passed": 0,
			},
		)
		self.assertEqual(
			первая["lessons"][0],
			{
				"id": self.урок,
				"key": "l-1",
				"title": "Урок первый",
				"hook": "Зачин урока «Урок первый»",
				"completed": False,
				"current": True,
				"objectives": [{**ЦЕЛЬ_1, "status": "touched"}],
				"sections": [ЖУРНАЛ],
				"homework": None,
			},
		)
		self.assertEqual(
			[р["key"] for р in первая["lessons"][1]["sections"]], ["log", "rules"]
		)
		self.assertEqual((вторая["key"], вторая["status"]), ("ch-2", "not_started"))
		[третий] = вторая["lessons"]
		self.assertEqual(третий["objectives"][0]["status"], "not_started")
		self.assertEqual((третий["homework"], третий["sections"]), ({"title": "Задание"}, []))

	def test_пройденный_урок_и_текущий_следующий(self):
		прохождения.отметить_пройденным(self.ученик, self.курс, "l-1")

		уроки = self.данные(student.course_outline(self.курс))["chapters"][0]["lessons"]

		self.assertEqual([(у["completed"], у["current"]) for у in уроки], [(True, False), (False, True)])

	def test_дерево_курса_старой_модели_отказ(self):
		урок = self.курс_старой_модели()
		курс = frappe.db.get_value("Course Lesson", урок, "course")

		self.отказ(student.course_outline(курс), "course_not_released")

	# --- public.course_map ---

	def test_карта_курса_из_релиза_статусы_из_прохождения(self):
		self.начать_урок()
		frappe.set_user("Administrator")
		frappe.db.set_value("LMS Course", self.курс, "published", 1)
		frappe.set_user(self.ученик)

		карта = self.данные(public.course_map(course=self.курс))

		первый, второй = карта["chapters"][0]["lessons"]
		self.assertEqual(первый["objectives"], [{"text": ЦЕЛЬ_1["text"], "status": "touched"}])
		# Не начатая цель — без `status`, как «не дошли».
		self.assertEqual(второй["objectives"], [{"text": "Цель урока «Урок второй»"}])
		self.assertEqual(карта["next_lesson"], self.урок)
		frappe.set_user("Guest")
		гостю = self.данные(public.course_map(course=self.курс))["chapters"][0]["lessons"][0]
		self.assertEqual(гостю["objectives"], [{"text": ЦЕЛЬ_1["text"]}])

	def test_карта_курса_старой_модели_цели_без_статуса(self):
		урок = self.курс_старой_модели()
		курс = frappe.db.get_value("Course Lesson", урок, "course")

		карта = self.данные(public.course_map(course=курс))

		self.assertEqual(карта["chapters"][0]["lessons"][0]["objectives"], [{"text": "Цель директивы"}])

	# --- lesson_session ---

	def test_занятие_урока_с_местом_и_целями(self):
		занятие = self.начать_урок()

		данные = self.данные(student.lesson_session(self.урок))

		self.assertEqual(
			{к: данные[к] for к in ("lesson", "course", "number", "total", "session", "completed")},
			{
				"lesson": self.урок,
				"course": self.курс,
				"number": 1,
				"total": 3,
				"session": занятие,
				"completed": False,
			},
		)
		self.assertEqual(данные["course_title"], "Пример курса")
		self.assertEqual(данные["objectives_progress"], [{**ЦЕЛЬ_1, "status": "touched"}])

	def test_урок_без_занятий_цели_не_начаты(self):
		данные = self.данные(student.lesson_session(self.урок))

		self.assertEqual((данные["session"], данные["status"]), (None, None))
		self.assertFalse(данные["has_chat_state"])
		self.assertEqual(данные["objectives_progress"], [{**ЦЕЛЬ_1, "status": "not_started"}])
		self.assertFalse(frappe.db.exists(прохождения.ПРОХОЖДЕНИЕ, {"student": self.ученик}))

	def test_занятие_урока_старой_модели_отказ(self):
		self.отказ(student.lesson_session(self.курс_старой_модели()), "course_not_released")

	# --- remember, update_artifact ---

	def test_прогресс_открытого_занятия_по_целям(self):
		занятие = self.начать_урок()

		заметка = self.данные(student.remember("observation", "pace", "Торопится", session=занятие))
		документ = self.данные(
			student.update_artifact(self.курс, "notebook", "log", rows=[{"topic": "Встреча"}])
		)

		for ответ in (заметка, документ):
			self.assertEqual(ответ["objectives_progress"], [{**ЦЕЛЬ_1, "status": "touched"}])
		frappe.db.set_value("Agent Learning Session", занятие, "status", "Completed")
		self.assertNotIn(
			"objectives_progress",
			self.данные(student.update_artifact(self.курс, "notebook", "log", rows=[{"topic": "Ещё"}])),
		)

	def test_занятие_без_прохождения_цели_не_начаты(self):
		занятие = создать_занятие(self.ученик, self.урок)

		ответ = self.данные(student.remember("observation", "pace", "Торопится", session=занятие))

		self.assertEqual(ответ["objectives_progress"], [{**ЦЕЛЬ_1, "status": "not_started"}])
		self.assertFalse(frappe.db.exists(прохождения.ПРОХОЖДЕНИЕ, {"student": self.ученик}))

	# --- student_detail ---

	def test_руководитель_видит_цели_урока_из_прохождения(self):
		self.начать_урок()
		# Занятие того же урока без `run` берёт живое прохождение урока.
		создать_занятие(self.ученик, self.урок)
		frappe.set_user(self.менеджер)

		сессии = self.данные(manager.student_detail(self.ученик))["sessions"]

		self.assertEqual(len(сессии), 2)
		for сессия in сессии:
			self.assertEqual(сессия["objectives"], [{**ЦЕЛЬ_1, "status": "touched"}])

	# --- только чтение ---

	def test_читатели_не_заводят_и_не_сверяют_прохождения(self):
		занятие = self.начать_урок()
		run = frappe.db.get_value(
			прохождения.ПРОХОЖДЕНИЕ,
			{"student": self.ученик, "lesson_key": "l-1"},
			["name", "modified"],
			as_dict=True,
		)
		# Новый релиз без фоновой сверки: прохождение остаётся на прежнем.
		второй = пример_релиза(self.ключ_курса)
		второй["lessons"][0]["title"] = "Урок первый, переписанный"
		frappe.set_user("Administrator")
		with patch.object(frappe, "enqueue"):
			_, новый = курс_из_релиза(релиз=второй)
		self.assertNotEqual(новый, self.релиз)
		frappe.set_user(self.ученик)

		self.данные(student.course_outline(self.курс))
		self.данные(public.course_map(course=self.курс))
		self.данные(student.lesson_session(self.урок))
		self.данные(student.lesson_session(урок_релиза(self.курс, "l-2")))
		self.данные(student.remember("observation", "pace", "Торопится", session=занятие))
		self.данные(student.update_artifact(self.курс, "notebook", "log", rows=[{"topic": "Встреча"}]))
		frappe.set_user(self.менеджер)
		self.данные(manager.student_detail(self.ученик))

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count(прохождения.ПРОХОЖДЕНИЕ, {"student": self.ученик}), 1)
		self.assertEqual(
			frappe.db.get_value(прохождения.ПРОХОЖДЕНИЕ, run.name, ["release", "modified"], as_dict=True),
			{"release": self.релиз, "modified": run.modified},
		)
