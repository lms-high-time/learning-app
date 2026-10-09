# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Квиз урока из релиза: старт, ответ, итог и закрытие урока (learning-services#504)."""

import copy
import json
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from lms_frappe_app.agent_learning import quiz, release_quiz
from lms_frappe_app.agent_learning.access import НЕ_ЗАЧИСЛЕН, ОРГАНИЗАЦИЯ_ПРИОСТАНОВЛЕНА
from lms_frappe_app.agent_learning.constants import (
	АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ,
	АННУЛИРОВАНА_УРОК_СНЯТ,
	ЗАНЯТИЕ_БРОШЕНО,
	ЗАНЯТИЕ_ЖДЁТ_КВИЗ,
	ЗАНЯТИЕ_ЗАВЕРШЕНО,
	ПОПЫТКА_АННУЛИРОВАНА,
	ПОПЫТКА_ЗАЧТЕНА,
	ПОПЫТКА_ИДЁТ,
	ПОПЫТКА_НЕ_ЗАЧТЕНА,
	ПРОВЕРКА_ВОПРОС_ВЫДАН,
	ПРОВЕРКА_ОТВЕТ_ПРИНЯТ,
	ПРОВЕРКА_ПОПЫТКА_АННУЛИРОВАНА,
	ПРОВЕРКА_ПОПЫТКА_ПЕРЕНЕСЕНА,
)
from lms_frappe_app.agent_learning.errors import ПРОХОЖДЕНИЕ_В_АРХИВЕ, ЧУЖОЕ_ЗАНЯТИЕ, Отказ
from lms_frappe_app.agent_learning.quiz import (
	КВИЗА_НЕТ,
	НУЖНЫ_СЛОВА,
	ПОПЫТКА_ЗАВЕРШЕНА,
	ПОПЫТКИ_ИСЧЕРПАНЫ,
	СЛИШКОМ_РАНО,
	ЧУЖОЙ_ВОПРОС,
)
from lms_frappe_app.agent_learning.release_quiz import АННУЛИРОВАНА
from lms_frappe_app.agent_learning.releases import index, retention
from lms_frappe_app.agent_learning.releases import service as релизы
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.api import student
from lms_frappe_app.tests.release_sample import пример_релиза, релиз_двух_целей
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	настроить_квиз,
	отметить_все_пункты,
	политика_по_умолчанию,
	создать_домашку,
	создать_занятие,
	создать_куратора,
	создать_организацию,
	создать_ученика,
)

ПОПЫТКА = "Agent Quiz Attempt"
ОТВЕТ = "Agent Quiz Answer"
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
			(попытка.release, попытка.lesson_key, попытка.lesson, попытка.course),
			(run.release, "l-1", run.lesson, run.course),
		)
		self.assertEqual(
			(попытка.session, попытка.student, попытка.attempt_number), (занятие, self.ученик, 1)
		)

	def test_первый_вопрос_с_ключами_вариантов(self):
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
		"""В ожидание квиза переводится только занятие в работе."""
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

	def test_без_доступа_к_курсу_не_начать(self):
		курс = self.опубликовать(релиз_двух_целей(self.ключ))
		run = прохождения.прохождение(self.ученик, курс, "l-1")
		занятие = создать_занятие(self.ученик, run.lesson)

		self.отказ(НЕ_ЗАЧИСЛЕН, release_quiz.начать, run, занятие)

	def test_занятие_другого_ученика_не_подходит(self):
		run, _ = self.урок()
		другой = создать_ученика(f"rq-other-{frappe.generate_hash(length=6)}@example.com")

		self.отказ(ЧУЖОЕ_ЗАНЯТИЕ, release_quiz.начать, run, создать_занятие(другой, run.lesson))

	def test_урок_без_вопросов_даёт_внятный_код(self):
		run, занятие = self.урок()
		frappe.db.delete("Agent Release Question", {"parent": run.release, "lesson_key": "l-1"})

		self.отказ(КВИЗА_НЕТ, release_quiz.начать, run, занятие)
		self.assertFalse(frappe.db.exists(ПОПЫТКА, {"student": self.ученик}))

	def test_квиз_по_курсу_приостановленной_организации_не_начать(self):
		"""Доступ проверяется и при старте попытки: занятие могло начаться до приостановки."""
		run, занятие = self.урок()
		frappe.set_user("Administrator")
		организация = создать_организацию(f"Компания {frappe.generate_hash(length=6)}")
		добавить_в_организацию(self.ученик, организация)
		frappe.get_doc(
			{"doctype": "Course Allocation", "organization": организация, "course": run.course}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Learning Organization", организация, "status", "Suspended")

		self.отказ(ОРГАНИЗАЦИЯ_ПРИОСТАНОВЛЕНА, release_quiz.начать, run, занятие)

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
			fields=["question_key", "objective_key", "answer", "is_correct"],
		)
		self.assertEqual(
			[dict(з) for з in запись],
			[
				{
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

	def test_ключ_важнее_текста(self):
		"""Текст варианта `V2` совпадает с ключом `V1`: ответ `V1` — это вариант `V1`."""
		релиз = релиз_двух_целей(self.ключ)
		[вопрос] = [в for в in релиз["lessons"][0]["quiz"]["questions"] if в["key"] == С1]
		[второй] = [в for в in вопрос["options"] if в["key"] == "V2"]
		второй["text"] = "V1"
		self.assertEqual(релиз["lessons"][0]["quiz"]["answers"][С1]["correct"], "V1")
		run, занятие = self.урок(релиз)
		попытка = release_quiz.начать(run, занятие)["attempt"]

		self.assertTrue(self.ответить(попытка, С1, "V1")["verdict"]["correct"])

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

	def test_журнал_проверки_по_ключам_вопросов(self):
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		self.ответить(попытка, С1, "V2")

		журнал = frappe.get_all(
			"Agent Quiz Event",
			filters={"attempt": попытка},
			fields=["kind", "question_key", "answer", "student_words", "is_correct", "lesson"],
			order_by="creation asc",
		)

		self.assertEqual(
			[(з.kind, з.question_key) for з in журнал],
			[
				(ПРОВЕРКА_ВОПРОС_ВЫДАН, С1),
				(ПРОВЕРКА_ОТВЕТ_ПРИНЯТ, С1),
				(ПРОВЕРКА_ВОПРОС_ВЫДАН, С2),
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

	def test_ответ_в_попытку_без_релиза_отклоняется_без_ответа(self):
		"""Попытка без релиза — не на действующем: аннулируется, ответ не принят и в журнал не идёт."""
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		frappe.db.set_value(ПОПЫТКА, попытка, "release", None)

		отказ = self.отказ(АННУЛИРОВАНА, self.ответить, попытка, С1)

		self.assertEqual(отказ.подробности["reason"], АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ)
		self.assertFalse(frappe.db.exists(ОТВЕТ, {"attempt": попытка}))
		self.assertFalse(
			frappe.db.exists("Agent Quiz Event", {"attempt": попытка, "kind": ПРОВЕРКА_ОТВЕТ_ПРИНЯТ})
		)

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
		попытка = release_quiz.начать(run, занятие)["attempt"]
		frappe.db.set_value(ПОПЫТКА, попытка, {"status": ПОПЫТКА_НЕ_ЗАЧТЕНА, "finished_at": now_datetime()})
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
		"""4 вопроса, 3 верных (75%): порог 70 — сдан, 80 — нет."""
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
		"""4 вопроса: `S1/l-1-D1`, `S2/l-1-D1` — на `l-1-D1`, `S3/l-1-D2`, `S4/l-1-D2` — на `l-1-D2`."""
		релиз = релиз_двух_целей(self.ключ, вопросов=4)
		квиз = релиз["lessons"][0]["quiz"]
		for вопрос in квиз["questions"][2:]:
			прежний = вопрос["key"]
			вопрос["key"], вопрос["objective"] = прежний.replace("l-1-D1", "l-1-D2"), "l-1-D2"
			квиз["answers"][вопрос["key"]] = квиз["answers"].pop(прежний)
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
		self.assertIsNotNone(момент.tzinfo, "время пересдачи — с поясом сайта")
		self.assertEqual(момент.replace(tzinfo=None), add_to_date(закончена, minutes=60))

	def test_последняя_попытка_без_паузы_в_итоге(self):
		настроить_квиз(max_attempts=1, retry_delay_minutes=60)
		run, занятие = self.урок(релиз_двух_целей(self.ключ, вопросов=1))

		итог = self.сдать(run, занятие, ["V2"])["result"]

		self.assertEqual((итог["attempts_left"], итог["retry_after"]), (0, None))

	def test_без_лимита_остаток_попыток_не_число(self):
		"""«Без лимита» — ноль в общих настройках: остатка нет, а пауза есть."""
		настроить_квиз(max_attempts=0, retry_delay_minutes=60)
		run, занятие = self.урок(релиз_двух_целей(self.ключ, вопросов=1))

		итог = self.сдать(run, занятие, ["V2"])["result"]

		self.assertIsNone(итог["attempts_left"])
		self.assertIsNotNone(итог["retry_after"])

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

	def test_квиз_сдаётся_и_после_закрытия_занятия_по_бездействию(self):
		"""Ученик вернулся к последнему вопросу через сутки — зачёт не теряется, а брошенное занятие брошенным и остаётся."""
		run, занятие = self.урок(релиз_двух_целей(self.ключ, вопросов=1))
		попытка = release_quiz.начать(run, занятие)["attempt"]
		frappe.db.set_value("Agent Learning Session", занятие, "status", ЗАНЯТИЕ_БРОШЕНО)

		итог = self.ответить(попытка, С1, "V1")["result"]

		self.assertTrue(итог["passed"])
		self.assertEqual(итог["session_status"], ЗАНЯТИЕ_БРОШЕНО)
		self.assertEqual(frappe.db.get_value(ПОПЫТКА, попытка, "status"), ПОПЫТКА_ЗАЧТЕНА)
		self.assertEqual(
			frappe.db.get_value(прохождения.ПРОХОЖДЕНИЕ, run.name, "status"), прохождения.ПРОЙДЕН
		)

	# --- закрытие урока без квиза ---

	def закрыть(self, занятие: str) -> dict:
		frappe.set_user(self.ученик)
		try:
			return student.complete_lesson(занятие)
		finally:
			frappe.set_user(self.куратор)

	def test_урок_из_релиза_с_обязательным_квизом_так_не_закрыть(self):
		run, занятие = self.урок()
		отметить_все_пункты(run.name)

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
		отметить_все_пункты(run.name)

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
		курс = self.опубликовать(релиз_двух_целей(self.ключ))
		урок = index.урок(frappe.db.get_value("LMS Course", курс, "active_release"), "l-1")["lesson"]
		зачислить(self.ученик, урок)

		quiz.отметить_урок_пройденным(frappe.get_doc("Agent Learning Session", создать_занятие(self.ученик, урок)))

		self.assertEqual(
			frappe.db.get_value(
				прохождения.ПРОХОЖДЕНИЕ,
				{"student": self.ученик, "course": курс, "lesson_key": "l-1"},
				"status",
			),
			прохождения.ПРОЙДЕН,
		)
		self.assertEqual(прохождения.главы(self.ученик, курс)[0]["status"], прохождения.ПРОЙДЕН)

	def test_дробный_порог_в_целых(self):
		"""3 вопроса, 2 верных (66,666…%): порог 66.67 не сдан, 66.66 — сдан."""
		for порог, сдан in ((66.67, False), (66.66, True)):
			with self.subTest(порог=порог):
				релиз = релиз_двух_целей(f"{self.ключ}-{порог}", порог=порог, вопросов=3)
				run, занятие = self.урок(релиз)

				итог = self.сдать(run, занятие, ["V1", "V1", "V2"])["result"]

				self.assertEqual(итог["passed"], сдан)

	def test_порог_из_релиза_попытки_а_не_нового(self):
		"""Новый релиз сменил только порог: попытка перенесена и оценивается порогом своего старта."""
		run, занятие = self.урок(релиз_двух_целей(self.ключ, порог=70, вопросов=4))
		попытка = release_quiz.начать(run, занятие)["attempt"]
		for вопрос in (С1, С2, "S3/l-1-D1"):
			self.ответить(попытка, вопрос, "V1")
		self.опубликовать(релиз_двух_целей(self.ключ, порог=80, вопросов=4))
		self.assertNotEqual(frappe.db.get_value("LMS Course", run.course, "active_release"), run.release)

		итог = self.ответить(попытка, "S4/l-1-D1", "V2")["result"]

		self.assertEqual((итог["passed"], итог["pass_threshold"]), (True, 0.7))
		self.assertEqual(
			frappe.db.get_value(ПОПЫТКА, попытка, "release"),
			frappe.db.get_value("LMS Course", run.course, "active_release"),
		)

	def test_повторный_зачёт_не_двигает_прохождение(self):
		# Политика — своя, а не из общих настроек: их читают через общий кеш, и
		# параллельный прогон мог сменить паузу перед повтором посреди теста.
		политика = {"quiz_required": 1, "max_attempts": 5, "retry_delay_minutes": 0}
		self.enterContext(patch.object(release_quiz, "политика_квиза_для_курса", return_value=политика))
		run, занятие = self.урок()
		self.assertTrue(self.сдать(run, занятие, ["V1", "V1"])["result"]["passed"])
		было = frappe.db.get_value(прохождения.ПРОХОЖДЕНИЕ, run.name, ["passed_at", "modified"], as_dict=True)

		повтор = self.сдать(run, создать_занятие(self.ученик, run.lesson), ["V1", "V1"])

		self.assertTrue(повтор["result"]["passed"])
		стало = frappe.db.get_value(
			прохождения.ПРОХОЖДЕНИЕ, run.name, ["passed_at", "modified"], as_dict=True
		)
		self.assertEqual(стало, было)

	def test_завершённую_попытку_не_завершить_повторно(self):
		run, занятие = self.урок(релиз_двух_целей(self.ключ, вопросов=1))
		попытка = self.сдать(run, занятие, ["V1"])["attempt"]

		self.отказ(ПОПЫТКА_ЗАВЕРШЕНА, release_quiz.завершить, frappe.get_doc(ПОПЫТКА, попытка))

	def test_попытка_без_вопросов_не_сдана(self):
		run, занятие = self.урок()
		попытка = release_quiz.начать(run, занятие)["attempt"]
		frappe.db.set_value(ПОПЫТКА, попытка, "questions", "[]")

		итог = release_quiz.завершить(frappe.get_doc(ПОПЫТКА, попытка))

		self.assertEqual((итог["passed"], итог["total"], итог["score"]), (False, 0, 0.0))

	def test_снятая_цель_не_подтверждается(self):
		run, _ = self.урок()
		frappe.db.set_value(
			"Agent Lesson Run Objective", {"parent": run.name, "objective_key": "l-1-D2"}, "removed", 1
		)

		прохождения.отметить_пройденным(self.ученик, run.course, "l-1", {"l-1-D1", "l-1-D2"})

		self.assertEqual(self.подтверждены(run), {"l-1-D1": 1, "l-1-D2": 0})

	# --- попытка между релизами (learning-services#514) ---

	def попытка_с_ответом(self, релиз: dict | None = None):
		"""Попытка урока `l-1` с верным ответом на первый вопрос: (прохождение, занятие, попытка)."""
		run, занятие = self.урок(релиз or релиз_двух_целей(self.ключ, вопросов=3))
		попытка = release_quiz.начать(run, занятие)["attempt"]
		self.ответить(попытка, С1)
		return run, занятие, попытка

	def действующий(self, курс: str) -> str:
		return frappe.db.get_value("LMS Course", курс, "active_release")

	def попытка(self, имя: str):
		return frappe.db.get_value(
			ПОПЫТКА,
			имя,
			["status", "cancel_reason", "release", "finished_at", "attempt_number", "pass_percentage"],
			as_dict=True,
		)

	def журнал(self, попытка: str) -> list[tuple]:
		return [
			(з.kind, з.question_key)
			for з in frappe.get_all(
				"Agent Quiz Event",
				filters={"attempt": попытка},
				fields=["kind", "question_key"],
				order_by="creation asc, occurred_at asc",
			)
		]

	def test_неизменный_квиз_переносит_попытку_и_она_доходит_до_итога(self):
		"""Порог новой версии — 90, попытки — 70 со старта: 3 из 4 сдают."""
		релиз = релиз_двух_целей(self.ключ, вопросов=4)
		run, _, попытка = self.попытка_с_ответом(релиз)
		снимок = self.снимок(попытка)
		релиз["lessons"][0]["title"] = "Урок переименован"
		релиз["lessons"][0]["quiz"]["pass_percentage"] = 90

		self.опубликовать(релиз)

		новый = self.действующий(run.course)
		self.assertNotEqual(новый, run.release)
		состояние = self.попытка(попытка)
		self.assertEqual((состояние.status, состояние.release), (ПОПЫТКА_ИДЁТ, новый))
		self.assertFalse(состояние.cancel_reason)
		self.assertIsNone(состояние.finished_at)
		self.assertEqual(self.снимок(попытка), снимок)
		self.assertEqual(frappe.db.count(ОТВЕТ, {"attempt": попытка}), 1)
		self.assertEqual(self.журнал(попытка)[-1], (ПРОВЕРКА_ПОПЫТКА_ПЕРЕНЕСЕНА, None))

		self.ответить(попытка, С2)
		self.ответить(попытка, "S3/l-1-D1")
		итог = self.ответить(попытка, "S4/l-1-D1", "V2")["result"]

		self.assertEqual((итог["passed"], итог["correct"], итог["pass_threshold"]), (True, 3, 0.7))

	def test_правка_только_пояснения_переносит_попытку(self):
		релиз = релиз_двух_целей(self.ключ, вопросов=3)
		run, _, попытка = self.попытка_с_ответом(релиз)
		релиз["lessons"][0]["quiz"]["answers"][С2]["explanation"] = "Пояснение новой версии."

		self.опубликовать(релиз)

		self.assertEqual(self.попытка(попытка).release, self.действующий(run.course))
		вердикт = self.ответить(попытка, С2)["verdict"]
		self.assertEqual(вердикт, {"correct": True, "explanation": "Пояснение новой версии."})

	def test_изменённый_квиз_аннулирует_попытку(self):
		"""Любая правка того, что решает проверку, — `quiz_changed`; ответы и журнал на месте."""

		def вопрос(квиз, номер):
			return квиз["questions"][номер]

		def убрать_третий(квиз):
			квиз["answers"].pop(квиз["questions"].pop()["key"])

		def добавить_вопрос(квиз):
			квиз["questions"].append({**copy.deepcopy(вопрос(квиз, 1)), "key": "S4/l-1-D1"})
			квиз["answers"]["S4/l-1-D1"] = {"correct": "V1", "explanation": ПОЯСНЕНИЕ}

		правки = {
			"текст вопроса": lambda к: вопрос(к, 1).update(text="Другая ситуация"),
			"текст варианта": lambda к: вопрос(к, 1)["options"][1].update(text="Иной"),
			"ключ варианта": lambda к: (
				вопрос(к, 1)["options"][1].update(key="V9"),
				к["answers"][С2].update(correct="V1"),
			),
			"новый вариант": lambda к: вопрос(к, 1)["options"].append({"key": "V3", "text": "Третий"}),
			"порядок вариантов": lambda к: вопрос(к, 1)["options"].reverse(),
			"верный вариант": lambda к: к["answers"][С2].update(correct="V2"),
			"цель вопроса": lambda к: вопрос(к, 1).update(objective="l-1-D2"),
			"порядок вопросов": lambda к: к["questions"].insert(1, к["questions"].pop(2)),
			"вопрос убран": убрать_третий,
			"вопрос добавлен": добавить_вопрос,
		}
		for номер, (что, правка) in enumerate(правки.items()):
			with self.subTest(что=что):
				релиз = релиз_двух_целей(f"{self.ключ}-{номер}", вопросов=3)
				run, _, попытка = self.попытка_с_ответом(релиз)
				правка(релиз["lessons"][0]["quiz"])

				self.опубликовать(релиз)

				состояние = self.попытка(попытка)
				self.assertEqual(
					(состояние.status, состояние.cancel_reason, состояние.release),
					(ПОПЫТКА_АННУЛИРОВАНА, АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ, run.release),
				)
				self.assertIsNotNone(состояние.finished_at)
				self.assertEqual(frappe.db.count(ОТВЕТ, {"attempt": попытка}), 1)
				self.assertEqual(
					self.журнал(попытка),
					[
						(ПРОВЕРКА_ВОПРОС_ВЫДАН, С1),
						(ПРОВЕРКА_ОТВЕТ_ПРИНЯТ, С1),
						(ПРОВЕРКА_ВОПРОС_ВЫДАН, С2),
						(ПРОВЕРКА_ПОПЫТКА_АННУЛИРОВАНА, None),
					],
				)

	def test_снятый_урок_аннулирует_попытку(self):
		run, занятие = self.урок(пример_релиза(self.ключ), "l-2")
		попытка = release_quiz.начать(run, занятие)["attempt"]
		без_урока = пример_релиза(self.ключ)
		без_урока["chapters"][0]["lessons"] = ["l-1"]
		без_урока["lessons"] = [у for у in без_урока["lessons"] if у["key"] != "l-2"]

		self.опубликовать(без_урока)

		состояние = self.попытка(попытка)
		self.assertEqual(
			(состояние.status, состояние.cancel_reason), (ПОПЫТКА_АННУЛИРОВАНА, АННУЛИРОВАНА_УРОК_СНЯТ)
		)
		отказ = self.отказ(АННУЛИРОВАНА, self.ответить, попытка, "S1/l-2-D1")
		self.assertEqual(
			отказ.подробности, {"attempt": попытка, "session": занятие, "reason": АННУЛИРОВАНА_УРОК_СНЯТ}
		)
		self.assertNotIn("request_quiz", отказ.сообщение)
		run = прохождения.прохождение(self.ученик, run.course, "l-2")
		self.отказ("lesson_not_in_release", release_quiz.начать, run, занятие)

	def test_попытка_на_освобождённом_релизе_аннулируется_без_сравнения(self):
		"""Попытка на v1 при действующем v2, публикуется v3 с тем же квизом: v1 освобождён —
		аннулирование; не освобождён — перенос по сравнению."""

		def снимок_пуст(релиз):
			frappe.db.set_value(index.РЕЛИЗ, релиз, "snapshot", None, update_modified=False)

		def строк_нет(релиз):
			frappe.db.delete(index.ВОПРОС, {"parenttype": index.РЕЛИЗ, "parent": релиз})

		варианты = (
			("снимок пуст", снимок_пуст, ПОПЫТКА_АННУЛИРОВАНА),
			("строк вопросов нет", строк_нет, ПОПЫТКА_АННУЛИРОВАНА),
			("не освобождён", lambda _: None, ПОПЫТКА_ИДЁТ),
		)
		for номер, (как, освободить, статус) in enumerate(варианты):
			with self.subTest(как=как):
				релиз = релиз_двух_целей(f"{self.ключ}-{номер}", вопросов=3)
				run, _, попытка = self.попытка_с_ответом(релиз)
				v1 = run.release
				релиз["lessons"][0]["title"] = "Версия 2"
				# v1 освобождает здесь сам вариант, а не публикация v2.
				with patch.object(retention, "освободить_прежние"):
					self.опубликовать(релиз)
				frappe.db.set_value(ПОПЫТКА, попытка, "release", v1)
				освободить(v1)
				релиз["lessons"][0]["title"] = "Версия 3"

				self.опубликовать(релиз)

				состояние = self.попытка(попытка)
				self.assertEqual(состояние.status, статус)
				if статус == ПОПЫТКА_АННУЛИРОВАНА:
					self.assertEqual(состояние.cancel_reason, АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ)
				else:
					self.assertEqual(состояние.release, self.действующий(run.course))

	def test_аннулированная_не_в_счёт_лимита_и_паузы(self):
		"""Лимит в одну попытку и сутки паузы: после аннулирования новая попытка — сразу и с тем же номером."""
		настроить_квиз(max_attempts=1, retry_delay_minutes=1440)
		релиз = релиз_двух_целей(self.ключ, вопросов=3)
		run, занятие, попытка = self.попытка_с_ответом(релиз)
		релиз["lessons"][0]["quiz"]["questions"][0]["text"] = "Другая ситуация"
		self.опубликовать(релиз)
		run = прохождения.прохождение(self.ученик, run.course, "l-1")

		начало = release_quiz.начать(run, занятие)

		self.assertNotEqual(начало["attempt"], попытка)
		self.assertNotIn("previous_attempt_cancelled", начало)
		новая = self.попытка(начало["attempt"])
		self.assertEqual(
			(новая.status, новая.release, новая.attempt_number),
			(ПОПЫТКА_ИДЁТ, self.действующий(run.course), 1),
		)
		self.assertEqual(начало["question"]["text"], "Другая ситуация")

	def test_submit_answer_в_аннулированную_попытку(self):
		релиз = релиз_двух_целей(self.ключ)
		run, занятие = self.урок(релиз)
		отметить_все_пункты(run.name)
		frappe.set_user(self.ученик)
		попытка = student.request_quiz(занятие)["data"]["attempt"]
		frappe.set_user(self.куратор)
		релиз["lessons"][0]["quiz"]["answers"][С1]["correct"] = "V2"
		self.опубликовать(релиз)
		frappe.set_user(self.ученик)

		ответ = student.submit_answer(попытка, С1, "V1", "слова ученика")

		self.assertFalse(ответ["ok"])
		ошибка = ответ["error"]
		self.assertEqual(
			(ошибка["code"], ошибка["reason"], ошибка["session"], ошибка["attempt"]),
			(АННУЛИРОВАНА, АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ, занятие, попытка),
		)
		self.assertIn("request_quiz", ошибка["message"])
		self.assertFalse(frappe.db.exists(ОТВЕТ, {"attempt": попытка}))

	def гонка_с_публикацией(self):
		"""Попытка, заведённая в гонке: старт квиза прочёл прежний релиз, а публикация её не застала.
		Отдаёт (прохождение по новому релизу, занятие, попытка на прежнем релизе)."""
		релиз = релиз_двух_целей(self.ключ)
		run, занятие = self.урок(релиз)
		прежний = run.release
		релиз["lessons"][0]["title"] = "Новое название"
		self.опубликовать(релиз)
		run = прохождения.прохождение(self.ученик, run.course, "l-1")
		попытка = release_quiz.начать(run, занятие)["attempt"]
		frappe.db.set_value(ПОПЫТКА, попытка, "release", прежний)
		return run, занятие, попытка

	def test_попытка_из_гонки_аннулируется_при_ответе(self):
		_, занятие, попытка = self.гонка_с_публикацией()

		отказ = self.отказ(АННУЛИРОВАНА, self.ответить, попытка, С1)

		self.assertEqual(
			отказ.подробности,
			{"attempt": попытка, "session": занятие, "reason": АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ},
		)
		self.assertEqual(self.попытка(попытка).status, ПОПЫТКА_АННУЛИРОВАНА)
		self.assertFalse(frappe.db.exists(ОТВЕТ, {"attempt": попытка}))
		self.assertEqual(self.журнал(попытка)[-1], (ПРОВЕРКА_ПОПЫТКА_АННУЛИРОВАНА, None))
		# Повтор — тот же отказ по сохранённой причине.
		self.отказ(АННУЛИРОВАНА, self.ответить, попытка, С1)

	def test_попытка_из_гонки_аннулируется_при_старте_и_даёт_новую(self):
		run, занятие, попытка = self.гонка_с_публикацией()
		отметить_все_пункты(run.name)
		frappe.set_user(self.ученик)

		ответ = student.request_quiz(занятие)

		self.assertTrue(ответ["ok"], ответ)
		данные = ответ["data"]
		self.assertNotEqual(данные["attempt"], попытка)
		self.assertEqual(
			данные["previous_attempt_cancelled"],
			{"attempt": попытка, "reason": АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ},
		)
		self.assertEqual(self.попытка(попытка).status, ПОПЫТКА_АННУЛИРОВАНА)
		новая = self.попытка(данные["attempt"])
		self.assertEqual((новая.release, новая.attempt_number), (self.действующий(run.course), 1))

	def test_сброс_прогресса_архивирует_аннулированную(self):
		from lms_frappe_app.agent_learning.reset import сбросить_прогресс

		релиз = релиз_двух_целей(self.ключ, вопросов=3)
		run, _, попытка = self.попытка_с_ответом(релиз)
		релиз["lessons"][0]["quiz"]["questions"][0]["text"] = "Другая ситуация"
		self.опубликовать(релиз)
		frappe.set_user("Administrator")

		сбросить_прогресс(self.ученик, run.course, "Administrator")

		архив = frappe.db.get_value(ПОПЫТКА, попытка, ["student", "archived_student", "status"], as_dict=True)
		self.assertEqual(
			(архив.student, архив.archived_student, архив.status),
			(None, self.ученик, ПОПЫТКА_АННУЛИРОВАНА),
		)

	def test_порог_снимается_в_попытку_на_старте(self):
		run, занятие = self.урок(релиз_двух_целей(self.ключ, порог=55.5))

		попытка = release_quiz.начать(run, занятие)["attempt"]

		self.assertEqual(self.попытка(попытка).pass_percentage, 55.5)

	def test_порог_ноль_зачитывает_попытку_без_верных(self):
		run, занятие = self.урок(релиз_двух_целей(self.ключ, порог=0))

		итог = self.сдать(run, занятие, ["V2", "V2"])["result"]

		self.assertEqual((итог["passed"], итог["correct"], итог["pass_threshold"]), (True, 0, 0))

	def test_publish_release_при_взаимоблокировке_busy(self):
		"""Попытку под публикацией изменил ответ ученика: откат целиком и `busy`."""
		from lms_frappe_app.api import authoring

		релиз = релиз_двух_целей(self.ключ)
		self.опубликовать(релиз)
		релиз["lessons"][0]["title"] = "Новое название"

		with (
			patch.object(release_quiz, "перенести_попытки", side_effect=frappe.QueryDeadlockError("1213")),
			patch.object(frappe.db, "rollback") as откат,
		):
			ответ = authoring.publish_release(release=релиз)

		self.assertEqual(ответ["error"]["code"], "busy")
		откат.assert_called_once_with()

	def test_сравнение_квиза_без_пояснений(self):
		"""Чистая функция: пояснение не в счёт, остальное — в счёт, пустой квиз ни с чем не совпадает."""
		вопрос = {
			"key": С1,
			"objective": "l-1-D1",
			"text": "Т",
			"options": [{"key": "V1", "text": "А"}, {"key": "V2", "text": "Б"}],
			"correct": "V1",
			"explanation": "П",
		}
		self.assertTrue(release_quiz.квиз_тот_же([вопрос], [{**вопрос, "explanation": "Другое"}]))
		self.assertFalse(release_quiz.квиз_тот_же([вопрос], [{**вопрос, "correct": "V2"}]))
		self.assertFalse(release_quiz.квиз_тот_же([], []))

	# --- гонка за прохождение в методах контракта ---

	@contextmanager
	def гонка_за_прохождение(self):
		"""Блокировка прохождения падает взаимоблокировкой; отдаёт подменённый откат — тест держит свои записи."""
		with (
			patch.object(прохождения, "прохождение", side_effect=frappe.QueryDeadlockError("1213")),
			patch.object(frappe.db, "rollback") as откат,
		):
			yield откат

	def test_complete_lesson_при_гонке_за_прохождение_busy(self):
		настроить_квиз(quiz_required=0)
		_, занятие = self.урок()

		with self.гонка_за_прохождение() as откат:
			ответ = self.закрыть(занятие)

		self.assertEqual(ответ["error"]["code"], "busy")
		self.assertEqual(ответ["error"]["session"], занятие)
		откат.assert_called_once_with()

	def test_submit_answer_при_гонке_за_прохождение_busy(self):
		"""Сданная попытка закрывает урок и пишет прохождение — гонка за него даёт `busy`."""
		run, занятие = self.урок()
		отметить_все_пункты(run.name)
		frappe.set_user(self.ученик)
		попытка = student.request_quiz(занятие)["data"]["attempt"]
		student.submit_answer(попытка, С1, "V1", "слова ученика")

		with self.гонка_за_прохождение() as откат:
			ответ = student.submit_answer(попытка, С2, "V1", "слова ученика")

		self.assertEqual(ответ["error"]["code"], "busy")
		self.assertEqual(ответ["error"]["attempt"], попытка)
		откат.assert_called_once_with()

	def test_архивное_прохождение_квиз_не_начинает(self):
		run, занятие = self.урок()
		frappe.db.set_value(прохождения.ПРОХОЖДЕНИЕ, run.name, "student", None)

		self.отказ(ПРОХОЖДЕНИЕ_В_АРХИВЕ, release_quiz.начать, frappe.get_doc(прохождения.ПРОХОЖДЕНИЕ, run.name), занятие)


class IntegrationTestПубликацияИЧужиеПопытки(IntegrationTestCase):
	"""Публикация блокирует открытые попытки только своего курса (learning-services#514).

	Отдельный класс — отдельная транзакция: `IntegrationTestCase` откатывает
	базу в конце класса, и блокировки промежутков индекса попыток, взятые
	публикациями соседних тестов, мешали бы второму соединению.
	"""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"rqx-{суффикс}@example.com")
		self.ученик = создать_ученика(f"rqx-pupil-{суффикс}@example.com")
		self.ключ = f"rqx-{суффикс}"
		frappe.set_user(self.куратор)

	def второе_соединение(self):
		"""Своё соединение с базой сайта — другая транзакция; откатывается и закрывается после теста.

		`Why:` не `secondary_connection` Frappe: тот зовёт `frappe.connect`, и
		состояние запроса основного соединения после него другое."""
		from frappe.database import get_db

		соединение = get_db(
			socket=frappe.conf.db_socket,
			host=frappe.conf.db_host,
			port=frappe.conf.db_port,
			user=frappe.conf.db_user or frappe.conf.db_name,
			password=frappe.conf.db_password,
			cur_db_name=frappe.conf.db_name,
		)
		соединение.connect()
		self.addCleanup(соединение.close)
		self.addCleanup(соединение.rollback)
		соединение.sql("set session innodb_lock_wait_timeout = 2")
		return соединение

	def test_публикация_не_ждёт_попытку_другого_курса(self):
		"""Другая транзакция держит незафиксированную попытку чужого курса — публикация
		её не ждёт: открытые попытки отбираются по индексу `(course, status)`, а не
		блокировкой всей таблицы."""
		релиз = релиз_двух_целей(self.ключ)
		курс = релизы.опубликовать(релиз, None, self.куратор)["course"]
		run = прохождения.прохождение(self.ученик, курс, "l-1")
		зачислить(self.ученик, run.lesson)
		попытка = release_quiz.начать(run, создать_занятие(self.ученик, run.lesson))["attempt"]
		соседняя = self.второе_соединение()
		соседняя.sql(
			"""insert into `tabAgent Quiz Attempt`
			(name, creation, modified, course, status, session, student, lesson_key)
			values (%s, now(), now(), %s, %s, 'other-session', 'other@example.com', 'l-1')""",
			(frappe.generate_hash(length=10), f"other-{frappe.generate_hash(length=8)}", ПОПЫТКА_ИДЁТ),
		)
		[(прежнее_ожидание,)] = frappe.db.sql("select @@session.innodb_lock_wait_timeout")
		frappe.db.sql("set session innodb_lock_wait_timeout = 2")
		self.addCleanup(frappe.db.sql, f"set session innodb_lock_wait_timeout = {int(прежнее_ожидание)}")
		релиз["lessons"][0]["title"] = "Новое название"

		релизы.опубликовать(релиз, None, self.куратор)

		self.assertEqual(
			frappe.db.get_value(ПОПЫТКА, попытка, "release"),
			frappe.db.get_value("LMS Course", курс, "active_release"),
		)
