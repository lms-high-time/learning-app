# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Квиз урока из релиза: старт, ответ, итог и закрытие урока (learning-services#504)."""

import json
from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from lms_frappe_app.agent_learning import release_quiz
from lms_frappe_app.agent_learning.access import НЕ_ЗАЧИСЛЕН
from lms_frappe_app.agent_learning.constants import (
	ЗАНЯТИЕ_БРОШЕНО,
	ЗАНЯТИЕ_ЖДЁТ_КВИЗ,
	ЗАНЯТИЕ_ЗАВЕРШЕНО,
	ПОПЫТКА_ЗАЧТЕНА,
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
from lms_frappe_app.api import student
from lms_frappe_app.tests.release_sample import пример_релиза, релиз_двух_целей
from lms_frappe_app.tests.sample_data import (
	зачислить,
	настроить_квиз,
	политика_по_умолчанию,
	создать_домашку,
	создать_занятие,
	создать_куратора,
	создать_ученика,
)

ПОПЫТКА = "Agent Quiz Attempt"
ОТВЕТ = "Agent Quiz Answer"
ПОЯСНЕНИЕ = "Потому что так велит условие."
С1, С2, С3, С4 = (f"S{номер}/l-1-D1" for номер in range(1, 5))


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

	def сдать(self, run, занятие: str, ответы: list[str]) -> dict:
		"""Попытка с ответами по порядку снимка; отдаёт ответ на последний вопрос."""
		попытка = release_quiz.начать(run, занятие)["attempt"]
		for вопрос, ответ in zip(self.снимок(попытка), ответы, strict=True):
			последний = self.ответить(попытка, вопрос["key"], ответ)
		return {"attempt": попытка, **последний}

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

	def test_старт_не_меняет_занятие_не_в_работе(self):
		"""Как у `quiz.начать_попытку`: в ожидание квиза переводится только занятие в работе."""
		настроить_квиз(max_attempts=5, retry_delay_minutes=0)
		run, занятие = self.урок()
		первая = release_quiz.начать(run, занятие)["attempt"]
		frappe.db.set_value(ПОПЫТКА, первая, {"status": ПОПЫТКА_НЕ_ЗАЧТЕНА, "finished_at": now_datetime()})

		for статус in (ЗАНЯТИЕ_ЖДЁТ_КВИЗ, ЗАНЯТИЕ_БРОШЕНО):
			with self.subTest(статус=статус):
				frappe.db.set_value("Agent Learning Session", занятие, "status", статус)
				попытка = release_quiz.начать(run, занятие)["attempt"]
				frappe.db.set_value(ПОПЫТКА, попытка, "status", ПОПЫТКА_НЕ_ЗАЧТЕНА)

				self.assertNotEqual(попытка, первая)
				self.assertEqual(frappe.db.get_value("Agent Learning Session", занятие, "status"), статус)

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

	def test_открытая_попытка_продолжается_после_снятия_урока(self):
		run, занятие = self.урок(пример_релиза(self.ключ), "l-2")
		попытка = release_quiz.начать(run, занятие)["attempt"]
		без_урока = пример_релиза(self.ключ)
		без_урока["chapters"][0]["lessons"] = ["l-1"]
		без_урока["lessons"] = [у for у in без_урока["lessons"] if у["key"] != "l-2"]
		self.опубликовать(без_урока)
		run = прохождения.прохождение(self.ученик, run.course, "l-2")

		повтор = release_quiz.начать(run, занятие)

		self.assertEqual(повтор["attempt"], попытка)
		self.assertEqual(повтор["question"]["id"], self.снимок(попытка)[0]["key"])
		self.assertTrue(self.ответить(попытка, повтор["question"]["id"], "V1")["verdict"]["correct"])

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
		self.assertFalse(ответ["attempt_finished"])
		self.assertNotIn("result", ответ)
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
		self.assertTrue(ответ["attempt_finished"])
		self.assertEqual(frappe.db.get_value(ПОПЫТКА, попытка, "status"), ПОПЫТКА_НЕ_ЗАЧТЕНА)
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

	def test_ключ_варианта_без_учёта_регистра_и_списком_из_одного(self):
		run, занятие = self.урок(релиз_двух_целей(self.ключ, вопросов=3))
		попытка = release_quiz.начать(run, занятие)["attempt"]

		self.assertTrue(self.ответить(попытка, С1, " v1 ")["verdict"]["correct"])
		self.assertTrue(self.ответить(попытка, С2, ["V1"])["verdict"]["correct"])
		self.assertFalse(self.ответить(попытка, "S3/l-1-D1", ["V1", "V2"])["verdict"]["correct"])

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

	def test_дубль_ответа_не_пускает_база(self):
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		self.ответить(попытка, С1)

		with self.assertRaises((frappe.UniqueValidationError, frappe.DuplicateEntryError)):
			frappe.get_doc({"doctype": ОТВЕТ, "attempt": попытка, "question_key": С1}).insert(
				ignore_permissions=True
			)

	def test_параллельный_ответ_на_тот_же_вопрос_отклоняется(self):
		"""Проверка «уже отвечено» не увидела ответ соседа — держит индекс, попытка цела."""
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		self.ответить(попытка, С1)

		with patch.object(release_quiz, "_отвечено", return_value=False):
			отказ = self.отказ(ЧУЖОЙ_ВОПРОС, self.ответить, попытка, С1, "V2")

		self.assertNotIn("V1", json.dumps(отказ.подробности, ensure_ascii=False))
		self.assertEqual(frappe.db.count(ОТВЕТ, {"attempt": попытка}), 1)
		self.assertTrue(self.ответить(попытка, С2)["attempt_finished"])

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

	def test_попытки_другого_урока_не_в_счёт(self):
		настроить_квиз(max_attempts=1, retry_delay_minutes=0)
		run, занятие = self.урок(пример_релиза(self.ключ), "l-1")
		release_quiz.начать(run, занятие)
		второй = прохождения.прохождение(self.ученик, run.course, "l-2")

		начало = release_quiz.начать(второй, создать_занятие(self.ученик, второй.lesson))

		self.assertEqual(frappe.db.get_value(ПОПЫТКА, начало["attempt"], "lesson_key"), "l-2")

	def новый_релиз_урока(self, run):
		"""Новый релиз того же урока; отдаёт прохождение, сверенное с ним."""
		новый = релиз_двух_целей(self.ключ)
		новый["lessons"][0]["quiz"]["questions"][0]["text"] = "Другой вопрос"
		self.опубликовать(новый)
		run = прохождения.прохождение(self.ученик, run.course, run.lesson_key)
		self.assertNotEqual(run.release, frappe.db.get_value(ПОПЫТКА, {"student": self.ученик}, "release"))
		return run

	def test_лимит_переживает_новый_релиз(self):
		настроить_квиз(max_attempts=1, retry_delay_minutes=0)
		run, занятие = self.урок()
		release_quiz.начать(run, занятие)
		run = self.новый_релиз_урока(run)

		self.отказ(ПОПЫТКИ_ИСЧЕРПАНЫ, release_quiz.начать, run, создать_занятие(self.ученик, run.lesson))

	def test_пауза_переживает_новый_релиз(self):
		настроить_квиз(max_attempts=5, retry_delay_minutes=1440)
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		frappe.db.set_value(ПОПЫТКА, попытка, {"status": ПОПЫТКА_НЕ_ЗАЧТЕНА, "finished_at": now_datetime()})
		run = self.новый_релиз_урока(run)

		self.отказ(СЛИШКОМ_РАНО, release_quiz.начать, run, создать_занятие(self.ученик, run.lesson))

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

	# --- итог ---

	def test_порог_урока_из_релиза_в_целых(self):
		"""4 вопроса, 3 верных (75%): порог 70 — сдан, 80 — нет; порог платформы не читается."""
		настроить_квиз(pass_threshold=0.9)
		for порог, сдан in ((70, True), (75, True), (80, False)):
			with self.subTest(порог=порог):
				релиз = релиз_двух_целей(f"{self.ключ}-{порог}", порог=порог, вопросов=4)
				run, занятие = self.урок(релиз)

				ответ = self.сдать(run, занятие, ["V1", "V1", "V1", "V2"])

				self.assertTrue(ответ["attempt_finished"])
				итог = ответ["result"]
				self.assertEqual(
					(итог["passed"], итог["score"], итог["correct"], итог["total"], итог["pass_threshold"]),
					(сдан, 0.75, 3, 4, порог / 100),
				)
				попытка = frappe.get_doc(ПОПЫТКА, ответ["attempt"])
				self.assertEqual(
					(попытка.status, попытка.passed, попытка.score),
					(ПОПЫТКА_ЗАЧТЕНА if сдан else ПОПЫТКА_НЕ_ЗАЧТЕНА, int(сдан), 0.75),
				)
				self.assertIsNotNone(попытка.finished_at)

	def релиз_по_целям(self) -> dict:
		"""4 вопроса: S1, S2 — на `l-1-D1`, S3, S4 — на `l-1-D2`."""
		релиз = релиз_двух_целей(self.ключ, вопросов=4)
		for вопрос in релиз["lessons"][0]["quiz"]["questions"][2:]:
			вопрос["objective"] = "l-1-D2"
		return релиз

	def подтверждены(self, run) -> dict:
		строки = frappe.get_all(
			"Agent Lesson Run Objective",
			filters={"parent": run.name},
			fields=["objective_key", "quiz_confirmed"],
		)
		return {с.objective_key: с.quiz_confirmed for с in строки}

	def test_итог_по_целям_и_подтверждение_квизом(self):
		run, занятие = self.урок(self.релиз_по_целям())

		ответ = self.сдать(run, занятие, ["V1", "V1", "V1", "V2"])

		ожидаемо = {"l-1-D1": {"correct": 2, "total": 2}, "l-1-D2": {"correct": 1, "total": 2}}
		self.assertTrue(ответ["result"]["passed"])
		self.assertEqual(ответ["result"]["objective_results"], ожидаемо)
		self.assertEqual(
			frappe.parse_json(frappe.db.get_value(ПОПЫТКА, ответ["attempt"], "objective_results")), ожидаемо
		)
		self.assertEqual(self.подтверждены(run), {"l-1-D1": 1, "l-1-D2": 0})

	def test_несданная_попытка_не_подтверждает_а_позже_не_снимает(self):
		настроить_квиз(max_attempts=5, retry_delay_minutes=0)
		релиз = self.релиз_по_целям()
		релиз["lessons"][0]["quiz"]["pass_percentage"] = 80
		run, занятие = self.урок(релиз)

		провал = self.сдать(run, занятие, ["V1", "V1", "V1", "V2"])
		self.assertFalse(провал["result"]["passed"])
		self.assertEqual(провал["result"]["objective_results"]["l-1-D1"], {"correct": 2, "total": 2})
		self.assertEqual(self.подтверждены(run), {"l-1-D1": 0, "l-1-D2": 0})

		self.assertTrue(
			self.сдать(run, создать_занятие(self.ученик, run.lesson), ["V1"] * 4)["result"]["passed"]
		)
		self.assertEqual(self.подтверждены(run), {"l-1-D1": 1, "l-1-D2": 1})

		повтор = self.сдать(run, создать_занятие(self.ученик, run.lesson), ["V2"] * 4)
		self.assertFalse(повтор["result"]["passed"])
		self.assertEqual(self.подтверждены(run), {"l-1-D1": 1, "l-1-D2": 1})
		self.assertEqual(
			frappe.db.get_value(прохождения.ПРОХОЖДЕНИЕ, run.name, "status"), прохождения.ПРОЙДЕН
		)

	def test_сданная_попытка_отдаёт_пояснения_к_неверным(self):
		run, занятие = self.урок(релиз_двух_целей(self.ключ, вопросов=4))

		итог = self.сдать(run, занятие, ["V1", "V2", "V1", "V1"])["result"]

		self.assertTrue(итог["passed"])
		self.assertEqual(
			итог["explanations"],
			[{"id": С2, "text": "Ситуация и вопрос 2", "explanation": ПОЯСНЕНИЕ}],
		)
		self.assertNotIn("attempts_left", итог)
		self.assertNotIn("retry_after", итог)

	def test_несданная_попытка_пояснений_и_верных_вариантов_не_отдаёт(self):
		настроить_квиз(max_attempts=3, retry_delay_minutes=60)
		run, занятие = self.урок(релиз_двух_целей(self.ключ, порог=80, вопросов=4))

		ответ = self.сдать(run, занятие, ["V1", "V1", "V1", "V2"])

		итог = ответ["result"]
		self.assertFalse(итог["passed"])
		self.assertNotIn("explanations", итог)
		выдано = json.dumps(ответ, ensure_ascii=False, default=str)
		for утечка in ("V1", "Первый", ПОЯСНЕНИЕ, "explanation", "why_wrong"):
			self.assertNotIn(утечка, выдано)
		self.assertEqual(итог["attempts_left"], 2)
		закончена = frappe.db.get_value(ПОПЫТКА, ответ["attempt"], "finished_at")
		момент = datetime.fromisoformat(итог["retry_after"])
		self.assertEqual(момент.replace(tzinfo=None), add_to_date(закончена, minutes=60))

	def test_последняя_попытка_без_паузы_в_итоге(self):
		настроить_квиз(max_attempts=1, retry_delay_minutes=60)
		run, занятие = self.урок(релиз_двух_целей(self.ключ, вопросов=1))

		итог = self.сдать(run, занятие, ["V2"])["result"]

		self.assertEqual((итог["attempts_left"], итог["retry_after"]), (0, None))

	def test_несданная_попытка_урок_не_закрывает(self):
		run, занятие = self.урок(релиз_двух_целей(self.ключ, вопросов=1))

		итог = self.сдать(run, занятие, ["V2"])["result"]

		self.assertEqual(итог["session_status"], ЗАНЯТИЕ_ЖДЁТ_КВИЗ)
		self.assertFalse(
			frappe.db.exists("LMS Course Progress", {"member": self.ученик, "lesson": run.lesson})
		)
		self.assertNotEqual(
			frappe.db.get_value(прохождения.ПРОХОЖДЕНИЕ, run.name, "status"), прохождения.ПРОЙДЕН
		)

	def test_сданный_квиз_закрывает_урок(self):
		run, занятие = self.урок()
		создать_домашку(run.lesson)

		ответ = self.сдать(run, занятие, ["V1", "V1"])

		self.assertEqual(ответ["result"]["session_status"], ЗАНЯТИЕ_ЗАВЕРШЕНО)
		self.assertEqual(frappe.db.get_value("Agent Learning Session", занятие, "status"), ЗАНЯТИЕ_ЗАВЕРШЕНО)
		self.assertEqual(
			frappe.db.get_value(
				"LMS Course Progress", {"member": self.ученик, "lesson": run.lesson}, "status"
			),
			"Complete",
		)
		прохождение = frappe.db.get_value(
			прохождения.ПРОХОЖДЕНИЕ, run.name, ["status", "passed_at"], as_dict=True
		)
		self.assertEqual(прохождение.status, прохождения.ПРОЙДЕН)
		self.assertIsNotNone(прохождение.passed_at)
		self.assertTrue(
			frappe.db.exists("Agent Homework Submission", {"member": self.ученик, "lesson": run.lesson})
		)
		self.assertFalse(frappe.db.exists("LMS Quiz Submission", {"member": self.ученик}))
		self.assertFalse(frappe.db.get_value(ПОПЫТКА, ответ["attempt"], "submission"))

	# --- закрытие урока без квиза ---

	def закрыть(self, занятие: str) -> dict:
		frappe.set_user(self.ученик)
		try:
			return student.complete_lesson(занятие)
		finally:
			frappe.set_user(self.куратор)

	def test_урок_из_релиза_с_обязательным_квизом_так_не_закрыть(self):
		run, занятие = self.урок()

		ответ = self.закрыть(занятие)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.НУЖЕН_КВИЗ)
		self.assertFalse(
			frappe.db.exists("LMS Course Progress", {"member": self.ученик, "lesson": run.lesson})
		)
		self.assertNotEqual(
			frappe.db.get_value(прохождения.ПРОХОЖДЕНИЕ, run.name, "status"), прохождения.ПРОЙДЕН
		)

	def test_квиз_не_обязателен_урок_закрывается_и_пройден(self):
		настроить_квиз(quiz_required=0)
		run, занятие = self.урок()

		ответ = self.закрыть(занятие)

		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(
			frappe.db.get_value(прохождения.ПРОХОЖДЕНИЕ, run.name, "status"), прохождения.ПРОЙДЕН
		)
		self.assertTrue(
			frappe.db.exists(
				"LMS Course Progress", {"member": self.ученик, "lesson": run.lesson, "status": "Complete"}
			)
		)

	def test_закрытие_без_прохождения_заводит_его_пройденным(self):
		"""Урок закрыт до первой отметки — счёт глав всё равно видит его пройденным."""
		настроить_квиз(quiz_required=0)
		курс = self.опубликовать(релиз_двух_целей(self.ключ))
		урок = index.урок(frappe.db.get_value("LMS Course", курс, "active_release"), "l-1")["lesson"]
		зачислить(self.ученик, урок)

		self.assertTrue(self.закрыть(создать_занятие(self.ученик, урок))["ok"])

		self.assertEqual(
			frappe.db.get_value(
				прохождения.ПРОХОЖДЕНИЕ,
				{"student": self.ученик, "course": курс, "lesson_key": "l-1"},
				"status",
			),
			прохождения.ПРОЙДЕН,
		)
		self.assertEqual(прохождения.главы(self.ученик, курс)[0]["status"], прохождения.ПРОЙДЕН)
