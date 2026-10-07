# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Квиз урока из релиза: старт и ответ (learning-services#504)."""

import json
from datetime import datetime

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from lms_frappe_app.agent_learning import release_quiz
from lms_frappe_app.agent_learning.access import НЕ_ЗАЧИСЛЕН
from lms_frappe_app.agent_learning.constants import (
	ЗАНЯТИЕ_ЖДЁТ_КВИЗ,
	ПОПЫТКА_НЕ_ЗАЧТЕНА,
	ПРОВЕРКА_ВОПРОС_ВЫДАН,
	ПРОВЕРКА_ОТВЕТ_ПРИНЯТ,
)
from lms_frappe_app.agent_learning.errors import ЧУЖОЕ_ЗАНЯТИЕ, Отказ
from lms_frappe_app.agent_learning.quiz import (
	НУЖНЫ_СЛОВА,
	ПОПЫТКА_ЗАВЕРШЕНА,
	ПОПЫТКИ_ИСЧЕРПАНЫ,
	СЛИШКОМ_РАНО,
	ЧУЖОЙ_ВОПРОС,
)
from lms_frappe_app.agent_learning.releases import index
from lms_frappe_app.agent_learning.releases import service as релизы
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.tests.release_sample import пример_релиза, релиз_двух_целей
from lms_frappe_app.tests.sample_data import (
	зачислить,
	настроить_квиз,
	политика_по_умолчанию,
	создать_занятие,
	создать_куратора,
	создать_ученика,
)

ПОПЫТКА = "Agent Quiz Attempt"
ПОЯСНЕНИЕ = "Потому что так велит условие."
С1, С2 = "S1/l-1-D1", "S2/l-1-D1"


class IntegrationTestКвизИзРелиза(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(политика_по_умолчанию)
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"rq-{суффикс}@example.com")
		self.ученик = создать_ученика(f"rq-pupil-{суффикс}@example.com")
		self.ключ = f"rq-{суффикс}"
		frappe.set_user(self.куратор)

	# --- подготовка ---

	def опубликовать(self, релиз: dict) -> str:
		return релизы.опубликовать(релиз, None, self.куратор)["course"]

	def урок(self, релиз: dict | None = None, ключ_урока: str = "l-1"):
		"""Курс из релиза, ученик зачислен; отдаёт прохождение урока и занятие по нему."""
		курс = self.опубликовать(релиз or релиз_двух_целей(self.ключ))
		run = прохождения.прохождение(self.ученик, курс, ключ_урока)
		зачислить(self.ученик, run.lesson)
		return run, создать_занятие(self.ученик, run.lesson)

	def отказ(self, код: str, функция, *аргументы):
		with self.assertRaises(Отказ) as пойман:
			функция(*аргументы)
		self.assertEqual(пойман.exception.код, код)
		return пойман.exception

	def ответить(self, попытка: str, вопрос: str, ответ: str = "V1") -> dict:
		return release_quiz.ответить(попытка, вопрос, ответ, "слова ученика")

	def снимок(self, попытка: str) -> list[dict]:
		return json.loads(frappe.db.get_value(ПОПЫТКА, попытка, "questions"))

	# --- старт ---

	def test_снимок_вопросов_без_верного_и_пояснения(self):
		run, занятие = self.урок()

		начало = release_quiz.начать(run, занятие)

		снимок = self.снимок(начало["attempt"])
		self.assertEqual(снимок, index.вопросы_урока(run.release, "l-1"))
		self.assertEqual([в["key"] for в in снимок], [С1, С2])
		выдано = json.dumps([снимок, начало], ensure_ascii=False)
		for поле in ("correct", "explanation", ПОЯСНЕНИЕ):
			self.assertNotIn(поле, выдано)
		попытка = frappe.get_doc(ПОПЫТКА, начало["attempt"])
		self.assertEqual(
			(попытка.release, попытка.lesson_key, попытка.lesson, попытка.course, попытка.quiz),
			(run.release, "l-1", run.lesson, run.course, None),
		)
		self.assertEqual(
			(попытка.session, попытка.student, попытка.attempt_number), (занятие, self.ученик, 1)
		)

	def test_первый_вопрос_в_форме_старого_квиза_с_ключами_вариантов(self):
		run, занятие = self.урок()

		вопрос = release_quiz.начать(run, занятие)["question"]

		self.assertEqual(
			вопрос,
			{
				"id": С1,
				"text": "Ситуация и вопрос 1",
				"kind": "choice",
				"index": 1,
				"total": 2,
				"multiple": False,
				"options": [{"id": "V1", "text": "Первый"}, {"id": "V2", "text": "Второй"}],
			},
		)

	def test_старт_переводит_занятие_в_ожидание_квиза(self):
		run, занятие = self.урок()

		release_quiz.начать(run, занятие)

		self.assertEqual(frappe.db.get_value("Agent Learning Session", занятие, "status"), ЗАНЯТИЕ_ЖДЁТ_КВИЗ)

	def test_открытая_попытка_занятия_переиспользуется(self):
		run, занятие = self.урок()
		первая = release_quiz.начать(run, занятие)["attempt"]
		self.ответить(первая, С1)

		повтор = release_quiz.начать(run, занятие)

		self.assertEqual(повтор["attempt"], первая)
		self.assertEqual(повтор["question"]["id"], С2)
		self.assertEqual(frappe.db.count(ПОПЫТКА, {"student": self.ученик}), 1)

	def test_урок_снят_из_релиза(self):
		релиз = пример_релиза(self.ключ)
		run, занятие = self.урок(релиз, "l-2")
		без_урока = пример_релиза(self.ключ)
		без_урока["chapters"][0]["lessons"] = ["l-1"]
		без_урока["lessons"] = [у for у in без_урока["lessons"] if у["key"] != "l-2"]
		self.опубликовать(без_урока)
		run = прохождения.прохождение(self.ученик, run.course, "l-2")

		self.отказ("lesson_not_in_release", release_quiz.начать, run, занятие)
		self.assertFalse(frappe.db.exists(ПОПЫТКА, {"student": self.ученик}))

	def test_без_доступа_к_курсу_не_начать(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))
		run = прохождения.прохождение(self.ученик, курс, "l-1")
		занятие = создать_занятие(self.ученик, run.lesson)

		self.отказ(НЕ_ЗАЧИСЛЕН, release_quiz.начать, run, занятие)

	def test_занятие_другого_ученика_не_подходит(self):
		run, _ = self.урок()
		другой = создать_ученика(f"rq-other-{frappe.generate_hash(length=6)}@example.com")

		self.отказ(ЧУЖОЕ_ЗАНЯТИЕ, release_quiz.начать, run, создать_занятие(другой, run.lesson))

	# --- ответ ---

	def test_верный_ответ_с_пояснением(self):
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]

		ответ = self.ответить(попытка, С1, "V1")

		self.assertEqual(ответ["verdict"], {"correct": True, "explanation": ПОЯСНЕНИЕ})
		self.assertEqual(ответ["next_question"]["id"], С2)
		self.assertFalse(ответ["all_answered"])
		запись = frappe.get_all(
			"Agent Quiz Answer",
			filters={"attempt": попытка},
			fields=["question", "question_key", "objective_key", "answer", "is_correct"],
		)
		self.assertEqual(
			[dict(з) for з in запись],
			[
				{
					"question": None,
					"question_key": С1,
					"objective_key": "l-1-D1",
					"answer": "V1",
					"is_correct": 1,
				}
			],
		)

	def test_неверный_ответ_без_пояснения_и_без_верного_варианта(self):
		run, занятие = self.урок(релиз_двух_целей(self.ключ, вопросов=1))
		попытка = release_quiz.начать(run, занятие)["attempt"]

		ответ = self.ответить(попытка, С1, "V2")

		self.assertEqual(ответ["verdict"], {"correct": False})
		self.assertIsNone(ответ["next_question"])
		self.assertTrue(ответ["all_answered"])
		выдано = json.dumps(ответ, ensure_ascii=False)
		for утечка in ("V1", "Первый", ПОЯСНЕНИЕ, "explanation", "why_wrong"):
			self.assertNotIn(утечка, выдано)

	def test_ответ_текстом_варианта_засчитан(self):
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]

		self.assertTrue(self.ответить(попытка, С1, "  пеРвый ")["verdict"]["correct"])
		self.assertFalse(self.ответить(попытка, С2, "Второй")["verdict"]["correct"])
		self.assertEqual(
			frappe.get_all(
				"Agent Quiz Answer", filters={"attempt": попытка}, pluck="is_correct", order_by="creation"
			),
			[1, 0],
		)

	def test_неизвестный_ответ_неверен_а_не_ошибка(self):
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]

		self.assertEqual(self.ответить(попытка, С1, "Третий")["verdict"], {"correct": False})

	def test_ответ_сверяется_по_релизу_попытки(self):
		"""Новый релиз посреди попытки её не ломает: и сверка, и вопросы — из релиза попытки."""
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		новый = релиз_двух_целей(self.ключ)
		квиз = новый["lessons"][0]["quiz"]
		квиз["answers"][С1]["correct"] = "V2"
		квиз["questions"][1]["text"] = "Другой вопрос"
		self.опубликовать(новый)
		self.assertNotEqual(frappe.db.get_value("LMS Course", run.course, "active_release"), run.release)

		ответ = self.ответить(попытка, С1, "V1")

		self.assertTrue(ответ["verdict"]["correct"])
		self.assertEqual(ответ["next_question"]["text"], "Ситуация и вопрос 2")

	def test_журнал_проверки_по_ключам_вопросов(self):
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		self.ответить(попытка, С1, "V2")

		журнал = frappe.get_all(
			"Agent Quiz Event",
			filters={"attempt": попытка},
			fields=["kind", "question", "question_key", "answer", "student_words", "is_correct", "lesson"],
			order_by="creation asc",
		)

		self.assertEqual(
			[(з.kind, з.question, з.question_key) for з in журнал],
			[
				(ПРОВЕРКА_ВОПРОС_ВЫДАН, None, С1),
				(ПРОВЕРКА_ОТВЕТ_ПРИНЯТ, None, С1),
				(ПРОВЕРКА_ВОПРОС_ВЫДАН, None, С2),
			],
		)
		self.assertEqual(
			(журнал[1].answer, журнал[1].student_words, журнал[1].is_correct, журнал[1].lesson),
			("V2", "слова ученика", 0, run.lesson),
		)

	# --- отказы ответа ---

	def test_вопрос_не_из_снимка_отклоняется(self):
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]

		self.отказ(ЧУЖОЙ_ВОПРОС, self.ответить, попытка, "S9/l-1-D1")

	def test_повторный_ответ_на_вопрос_отклоняется(self):
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		self.ответить(попытка, С1)

		self.отказ(ЧУЖОЙ_ВОПРОС, self.ответить, попытка, С1, "V2")

	def test_ответ_в_завершённую_попытку_отклоняется(self):
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		frappe.db.set_value(ПОПЫТКА, попытка, "status", ПОПЫТКА_НЕ_ЗАЧТЕНА)

		self.отказ(ПОПЫТКА_ЗАВЕРШЕНА, self.ответить, попытка, С1)

	def test_ответ_без_слов_ученика_отклоняется(self):
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]

		self.отказ(НУЖНЫ_СЛОВА, release_quiz.ответить, попытка, С1, "V1", "  ")

	def test_ответ_без_доступа_к_курсу_отклоняется(self):
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		frappe.db.delete("LMS Enrollment", {"member": self.ученик, "course": run.course})

		self.отказ(НЕ_ЗАЧИСЛЕН, self.ответить, попытка, С1)

	# --- политика ---

	def test_брошенная_попытка_расходует_лимит(self):
		настроить_квиз(max_attempts=1, retry_delay_minutes=0)
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		self.ответить(попытка, С1, "V2")

		отказ = self.отказ(
			ПОПЫТКИ_ИСЧЕРПАНЫ, release_quiz.начать, run, создать_занятие(self.ученик, run.lesson)
		)
		self.assertEqual(отказ.подробности["attempts_used"], 1)

	def test_лимит_считается_по_уроку_релиза(self):
		настроить_квиз(max_attempts=1, retry_delay_minutes=0)
		run, занятие = self.урок(пример_релиза(self.ключ), "l-1")
		release_quiz.начать(run, занятие)
		второй = прохождения.прохождение(self.ученик, run.course, "l-2")

		начало = release_quiz.начать(второй, создать_занятие(self.ученик, второй.lesson))

		self.assertEqual(frappe.db.get_value(ПОПЫТКА, начало["attempt"], "lesson_key"), "l-2")

	def test_повтор_раньше_паузы_отклоняется_с_временем(self):
		настроить_квиз(max_attempts=5, retry_delay_minutes=1440)
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		закончена = now_datetime()
		frappe.db.set_value(ПОПЫТКА, попытка, {"status": ПОПЫТКА_НЕ_ЗАЧТЕНА, "finished_at": закончена})

		отказ = self.отказ(СЛИШКОМ_РАНО, release_quiz.начать, run, создать_занятие(self.ученик, run.lesson))

		момент = datetime.fromisoformat(отказ.подробности["retry_after"])
		self.assertIsNotNone(момент.tzinfo)
		self.assertEqual(момент.replace(tzinfo=None), add_to_date(закончена, minutes=1440))

	def test_после_паузы_новая_попытка_со_следующим_номером(self):
		настроить_квиз(max_attempts=5, retry_delay_minutes=60)
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		frappe.db.set_value(
			ПОПЫТКА,
			попытка,
			{"status": ПОПЫТКА_НЕ_ЗАЧТЕНА, "finished_at": add_to_date(now_datetime(), minutes=-61)},
		)

		начало = release_quiz.начать(run, создать_занятие(self.ученик, run.lesson))

		self.assertNotEqual(начало["attempt"], попытка)
		self.assertEqual(frappe.db.get_value(ПОПЫТКА, начало["attempt"], "attempt_number"), 2)
