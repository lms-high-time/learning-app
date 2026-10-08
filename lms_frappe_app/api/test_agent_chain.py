# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Цепочка агента по уроку курса из релиза: `lesson_item`, `mark_goal`, квиз и закрытие (learning-services#506)."""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from lms_frappe_app.agent_learning import quiz
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.api import student
from lms_frappe_app.tests.release_sample import релиз_двух_целей
from lms_frappe_app.tests.sample_data import (
	зачислить,
	зачислить_на_курс,
	курс_из_релиза,
	настроить_квиз,
	политика_по_умолчанию,
	создать_вопрос,
	создать_занятие,
	создать_квиз,
	создать_урок,
	создать_ученика,
	урок_релиза,
)

СВИДЕТЕЛЬСТВО = "Ученик назвал термин своими словами"
ПОДРОБНОСТИ = "Термин «пример» и его определение."


class IntegrationTestAgentChain(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(политика_по_умолчанию)
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"chain-{суффикс}@example.com")
		self.курс, self.релиз = курс_из_релиза()
		зачислить_на_курс(self.ученик, self.курс)
		self.урок = урок_релиза(self.курс, "l-1")
		frappe.set_user(self.ученик)

	# --- помощники ---

	def данные(self, ответ: dict) -> dict:
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def отказ(self, ответ: dict, код: str) -> dict:
		self.assertFalse(ответ["ok"], ответ)
		self.assertEqual(ответ["error"]["code"], код, ответ)
		return ответ["error"]

	def старт(self, урок: str | None = None) -> dict:
		return self.данные(student.start_lesson(lesson=урок or self.урок, frame=False))

	def отметить(self, занятие: str, пункт: str, статус: str = "done", **поля) -> dict:
		return student.mark_goal(занятие, пункт, статус, поля.pop("evidence", СВИДЕТЕЛЬСТВО), **поля)

	def прохождение(self, курс: str | None = None):
		return frappe.get_doc(
			прохождения.ПРОХОЖДЕНИЕ, {"student": self.ученик, "course": курс or self.курс, "lesson_key": "l-1"}
		)

	def курс_двух_целей(self) -> tuple[str, str]:
		frappe.set_user("Administrator")
		курс, _ = курс_из_релиза(релиз=релиз_двух_целей(f"chain-two-{frappe.generate_hash(length=6)}"))
		зачислить_на_курс(self.ученик, курс)
		frappe.set_user(self.ученик)
		return курс, урок_релиза(курс, "l-1")

	def отметить_обязательные(self, занятие: str, карта: list[dict]) -> dict:
		ответ = None
		for цель in карта:
			for пункт in цель["goals"]:
				if пункт["required"]:
					ответ = self.данные(self.отметить(занятие, пункт["key"]))
		return ответ

	def курс_старой_модели(self) -> str:
		frappe.set_user("Administrator")
		урок = создать_урок(f"Старый {frappe.generate_hash(length=6)}")
		зачислить(self.ученик, урок)
		frappe.set_user(self.ученик)
		return урок

	# --- lesson_item ---

	def test_подробности_пункта_из_пакета_со_статусом(self):
		занятие = self.старт()["session"]

		до = self.данные(student.lesson_item(занятие, "term:T1"))
		self.данные(self.отметить(занятие, "term:T1"))
		после = self.данные(student.lesson_item(занятие, "term:T1"))

		self.assertEqual(
			до,
			{
				"goal": {"key": "term:T1", "kind": "term", "required": True, "title": "Термин «пример»", "status": "open"},
				"details": ПОДРОБНОСТИ,
			},
		)
		self.assertEqual(после["goal"]["status"], "done")
		self.assertNotIn(СВИДЕТЕЛЬСТВО, json.dumps(после, ensure_ascii=False))

	def test_подробности_без_текста_в_пакете_пустая_строка(self):
		frappe.db.set_value("Agent Release Lesson", {"parent": self.релиз, "lesson_key": "l-1"}, "agent", "{}")
		занятие = self.старт()["session"]

		self.assertEqual(self.данные(student.lesson_item(занятие, "refute:M1"))["details"], "")

	def test_подробности_без_прохождения_только_читают(self):
		занятие = создать_занятие(self.ученик, self.урок)

		данные = self.данные(student.lesson_item(занятие, "l-1-D1/V1"))

		self.assertEqual(данные["goal"]["status"], "open")
		self.assertFalse(frappe.db.exists(прохождения.ПРОХОЖДЕНИЕ, {"student": self.ученик}))
		self.assertFalse(frappe.db.get_value("Agent Learning Session", занятие, "run"))

	def test_подробности_отказы(self):
		занятие = self.старт()["session"]
		frappe.set_user("Administrator")
		чужой = создать_ученика(f"chain-other-{frappe.generate_hash(length=6)}@example.com")
		чужое = создать_занятие(чужой, self.урок)
		frappe.set_user(self.ученик)

		self.отказ(student.lesson_item(чужое, "term:T1"), "not_your_session")
		неизвестный = self.отказ(student.lesson_item(занятие, "term:T9"), "goal_unknown")
		self.assertEqual(неизвестный["goals"], ["term:T1", "l-1-D1/V1", "refute:M1"])
		старое = создать_занятие(self.ученик, self.курс_старой_модели())
		self.отказ(student.lesson_item(старое, "term:T1"), "course_not_released")

	# --- mark_goal ---

	def test_отметка_двигает_активность_занятия(self):
		занятие = self.старт()["session"]
		давно = add_to_date(now_datetime(), hours=-5)
		frappe.db.set_value("Agent Learning Session", занятие, "last_activity_at", давно, update_modified=False)

		self.данные(self.отметить(занятие, "term:T1"))

		self.assertGreater(
			get_datetime(frappe.db.get_value("Agent Learning Session", занятие, "last_activity_at")), давно
		)

	def test_с_чего_продолжить_пишется_одной_версией_с_отметкой(self):
		занятие = self.старт()["session"]
		run = self.прохождение().name
		версии = {"ref_doctype": прохождения.ПРОХОЖДЕНИЕ, "docname": run}

		# Версии `track_changes` в тестах Frappe не пишет (`ignore_version = in_test`).
		with patch.object(frappe, "in_test", False):
			self.данные(self.отметить(занятие, "term:T1", resume_from="  С варианта «первый»  "))

		[версия] = frappe.get_all("Version", filters=версии, pluck="data")
		изменено = {поле for поле, *_ in json.loads(версия)["changed"]}
		self.assertTrue({"resume_from", "started_at", "status"} <= изменено, изменено)
		self.assertEqual(frappe.db.get_value(прохождения.ПРОХОЖДЕНИЕ, run, "resume_from"), "С варианта «первый»")
		# Пустая заметка прежнюю не стирает.
		self.данные(self.отметить(занятие, "l-1-D1/V1"))
		self.assertEqual(frappe.db.get_value(прохождения.ПРОХОЖДЕНИЕ, run, "resume_from"), "С варианта «первый»")
		self.assertEqual(self.старт()["resume_from"], "С варианта «первый»")

	def test_длинная_заметка_отказ_без_записи(self):
		занятие = создать_занятие(self.ученик, self.урок)

		ошибка = self.отказ(self.отметить(занятие, "term:T1", resume_from="я" * 501), "resume_from_too_long")

		self.assertEqual((ошибка["limit"], ошибка["length"]), (500, 501))
		self.assertFalse(frappe.db.exists(прохождения.ПРОХОЖДЕНИЕ, {"student": self.ученик}))

	def test_ответ_отметки_и_следующий_шаг(self):
		курс, урок = self.курс_двух_целей()
		занятие = self.старт(урок)["session"]

		первая = self.данные(self.отметить(занятие, "term:T1"))
		self.assertEqual(
			первая,
			{
				"goal": "term:T1",
				"status": "done",
				"objective": {"key": "l-1-D1", "status": "touched", "open": ["exec:E1"]},
				"lesson": {"status": "in_progress"},
				"next_step": {"kind": "goal", "objective": "l-1-D1", "goal": "exec:E1", "title": "Сделать пример"},
			},
		)
		self.assertNotIn(СВИДЕТЕЛЬСТВО, json.dumps(первая, ensure_ascii=False))

		self.assertEqual(self.данные(self.отметить(занятие, "exec:E1"))["next_step"], {"kind": "quiz"})

		frappe.set_user("Administrator")
		настроить_квиз(quiz_required=0)
		frappe.set_user(self.ученик)
		self.assertEqual(self.данные(self.отметить(занятие, "exec:E1"))["next_step"], {"kind": "complete"})

		прохождения.отметить_пройденным(self.ученик, курс, "l-1")
		self.assertIsNone(self.данные(self.отметить(занятие, "exec:E1"))["next_step"])

	def test_занятие_без_прохождения_получает_его(self):
		занятие = создать_занятие(self.ученик, self.урок)

		self.данные(self.отметить(занятие, "term:T1"))

		self.assertEqual(frappe.db.get_value("Agent Learning Session", занятие, "run"), self.прохождение().name)

	def test_архивное_прохождение_не_отмечается(self):
		занятие = self.старт()["session"]
		frappe.db.set_value(прохождения.ПРОХОЖДЕНИЕ, self.прохождение().name, "student", None)

		self.отказ(self.отметить(занятие, "term:T1"), "run_archived")

	def test_отметка_отказы_до_записи(self):
		занятие = создать_занятие(self.ученик, self.урок)
		старое = создать_занятие(self.ученик, self.курс_старой_модели())

		self.отказ(self.отметить(старое, "term:T1"), "course_not_released")
		self.отказ(self.отметить(занятие, "term:T1", evidence=""), "evidence_required")
		self.отказ(self.отметить(занятие, "term:T1", "skipped"), "goal_status_unknown")
		self.assertFalse(frappe.db.exists(прохождения.ПРОХОЖДЕНИЕ, {"student": self.ученик}))

	def test_гонка_за_прохождение_busy(self):
		занятие = self.старт()["session"]

		with (
			patch.object(прохождения, "отметить", side_effect=frappe.QueryDeadlockError("1213")),
			patch.object(frappe.db, "rollback") as откат,
		):
			ошибка = self.отказ(self.отметить(занятие, "term:T1"), "busy")

		self.assertEqual(ошибка["session"], занятие)
		откат.assert_called_once_with()

	# --- квиз и закрытие ---

	def test_урок_целиком_методами(self):
		курс, урок = self.курс_двух_целей()
		старт = self.старт(урок)
		занятие = старт["session"]

		последняя = self.отметить_обязательные(занятие, старт["lesson_map"])
		self.assertEqual(последняя["next_step"], {"kind": "quiz"})
		квиз = self.данные(student.request_quiz(занятие))
		self.assertEqual(set(квиз), {"attempt", "question"})
		вопрос = квиз["question"]
		while вопрос:
			ответ = self.данные(student.submit_answer(квиз["attempt"], вопрос["id"], "V1", "Первый"))
			вопрос = ответ["next_question"]

		self.assertTrue(ответ["attempt_finished"])
		self.assertTrue(ответ["result"]["passed"])
		self.assertEqual(ответ["result"]["session_status"], "Completed")
		self.assertEqual(self.прохождение(курс).status, прохождения.ПРОЙДЕН)
		self.assertEqual(прохождения.главы(self.ученик, курс)[0]["status"], прохождения.ПРОЙДЕН)
		self.assertIsNone(self.старт(урок)["next_step"])

	def test_квиз_закрыт_пока_открыты_пункты(self):
		курс, урок = self.курс_двух_целей()
		занятие = self.старт(урок)["session"]
		self.данные(self.отметить(занятие, "term:T1"))

		ошибка = self.отказ(student.request_quiz(занятие), "goals_open")

		self.assertEqual(ошибка["goals"], [{"objective": "l-1-D1", "goal": "exec:E1", "title": "Сделать пример"}])
		self.assertFalse(frappe.db.exists("Agent Quiz Attempt", {"student": self.ученик}))

	def test_закрытие_без_обязательного_квиза(self):
		frappe.set_user("Administrator")
		настроить_квиз(quiz_required=0)
		frappe.set_user(self.ученик)
		курс, урок = self.курс_двух_целей()
		старт = self.старт(урок)
		занятие = старт["session"]

		рано = self.отказ(student.complete_lesson(занятие), "goals_open")
		self.assertEqual([п["goal"] for п in рано["goals"]], ["term:T1", "exec:E1"])
		self.отметить_обязательные(занятие, старт["lesson_map"])
		закрыто = self.данные(student.complete_lesson(занятие))

		self.assertEqual(set(закрыто), {"lesson", "session_status", "next_lesson", "empty_blocks"})
		self.assertEqual((закрыто["lesson"], закрыто["session_status"]), (урок, "Completed"))
		self.assertEqual(self.прохождение(курс).status, прохождения.ПРОЙДЕН)

	def test_урок_без_вопросов_закрывается(self):
		frappe.db.delete("Agent Release Question", {"parent": self.релиз, "lesson_key": "l-1"})
		старт = self.старт()
		self.отметить_обязательные(старт["session"], старт["lesson_map"])

		закрыто = self.данные(student.complete_lesson(старт["session"]))

		self.assertEqual(закрыто["session_status"], "Completed")
		self.assertEqual(закрыто["next_lesson"], {"id": урок_релиза(self.курс, "l-2"), "title": "Урок второй"})
		# Предупреждение, а не отказ: урок закрывается и с пустым разделом документа.
		self.assertEqual([(б["artifact"], б["key"]) for б in закрыто["empty_blocks"]], [("notebook", "log")])

	def test_закрытое_занятие_урок_не_закрывает(self):
		frappe.set_user("Administrator")
		настроить_квиз(quiz_required=0)
		frappe.set_user(self.ученик)
		старт = self.старт()
		self.отметить_обязательные(старт["session"], старт["lesson_map"])
		frappe.db.set_value("Agent Learning Session", старт["session"], "status", "Abandoned")

		ошибка = self.отказ(student.complete_lesson(старт["session"]), "session_closed")

		self.assertEqual(ошибка["status"], "Abandoned")
		self.assertNotEqual(self.прохождение().status, прохождения.ПРОЙДЕН)
		self.assertFalse(frappe.db.exists("LMS Course Progress", {"member": self.ученик, "lesson": self.урок}))

	def test_ответ_не_принят_после_отзыва_доступа(self):
		"""Доступ проверяется на каждом ответе: иначе попытка доходила бы до зачёта по курсу, которого у ученика уже нет."""
		старт = self.старт()
		self.отметить_обязательные(старт["session"], старт["lesson_map"])
		квиз = self.данные(student.request_quiz(старт["session"]))
		frappe.db.delete("LMS Enrollment", {"member": self.ученик, "course": self.курс})

		self.отказ(student.submit_answer(квиз["attempt"], квиз["question"]["id"], "V1", "Первый"), "not_enrolled")

		self.assertFalse(frappe.db.exists("Agent Quiz Answer", {"attempt": квиз["attempt"]}))

	def test_с_обязательным_квизом_так_не_закрыть(self):
		старт = self.старт()
		self.отметить_обязательные(старт["session"], старт["lesson_map"])

		self.отказ(student.complete_lesson(старт["session"]), "quiz_required")

		self.assertNotEqual(self.прохождение().status, прохождения.ПРОЙДЕН)

	def test_курс_старой_модели_квиз_и_закрытие_отказывают(self):
		урок = self.курс_старой_модели()
		frappe.set_user("Administrator")
		вопрос = создать_вопрос("Два плюс два?", варианты=[("4", True), ("5", False)])
		создать_квиз(урок, [вопрос])
		frappe.set_user(self.ученик)
		занятие = создать_занятие(self.ученик, урок)
		попытка = quiz.начать_попытку(занятие)["attempt"]

		self.отказ(student.request_quiz(занятие), "course_not_released")
		self.отказ(student.complete_lesson(занятие), "course_not_released")
		self.отказ(student.submit_answer(попытка, вопрос, "1", "Четыре"), "course_not_released")
		self.assertFalse(frappe.db.exists("Agent Quiz Answer", {"attempt": попытка}))

	def test_квиз_при_гонке_за_прохождение_busy(self):
		занятие = создать_занятие(self.ученик, self.урок)

		with (
			patch.object(прохождения, "прохождение", side_effect=frappe.QueryDeadlockError("1213")),
			patch.object(frappe.db, "rollback") as откат,
		):
			ошибка = self.отказ(student.request_quiz(занятие), "busy")

		self.assertEqual(ошибка["session"], занятие)
		откат.assert_called_once_with()
