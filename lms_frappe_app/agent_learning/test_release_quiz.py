# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Квиз урока из релиза: старт, ответ, итог и закрытие урока (learning-services#504)."""

import copy
import json
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_to_date, now_datetime

from lms_frappe_app.agent_learning import quiz, release_quiz
from lms_frappe_app.agent_learning.access import НЕ_ЗАЧИСЛЕН
from lms_frappe_app.agent_learning.constants import (
	АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ,
	АННУЛИРОВАНА_ПОПЫТКА_ОТСТАЛА,
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
	зачислить,
	настроить_квиз,
	отметить_все_пункты,
	политика_по_умолчанию,
	создать_домашку,
	создать_занятие,
	создать_куратора,
	создать_ученика,
)

ПОПЫТКА = "Agent Quiz Attempt"
ОТВЕТ = "Agent Quiz Answer"
ПОЯСНЕНИЕ = "Потому что так велит условие."
С1, С2 = "S1/l-1-D1", "S2/l-1-D1"
#: Источники порядка попытки вместо `release_quiz._СЛУЧАЙ`: порядок релиза и обратный ему.
ПОРЯДОК_РЕЛИЗА = SimpleNamespace(shuffle=lambda список: None)
ОБРАТНЫЙ = SimpleNamespace(shuffle=list.reverse)


class IntegrationTestКвизИзРелиза(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		"""Курсы, которые тесты только читают, — `общий`: заводятся при первом
		обращении и служат всем тестам класса; попытки, прохождения и занятия — у
		ученика своего теста. Тест, который публикует новую версию курса или правит
		его строки, публикует свой — с ключом `self.ключ`."""
		super().setUpClass()
		# Тесты класса называют вопросы и варианты по местам в релизе; порядок
		# попытки проверяет `test_старт_квиза`.
		cls.enterClassContext(patch.object(release_quiz, "_СЛУЧАЙ", ПОРЯДОК_РЕЛИЗА))
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		cls.куратор = создать_куратора(f"rq-{суффикс}@example.com")
		cls.ключ_общих = f"rq-common-{суффикс}"
		cls.общие = {}

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(политика_по_умолчанию)
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"rq-pupil-{суффикс}@example.com")
		self.ключ = f"rq-{суффикс}"
		frappe.set_user(self.куратор)

	# --- подготовка ---

	def новый_ученик(self) -> None:
		"""Свой ученик — у строки таблицы."""
		self.ученик = создать_ученика(f"rq-pupil-{frappe.generate_hash(length=6)}@example.com")

	def опубликовать(self, релиз: dict) -> str:
		return релизы.опубликовать(релиз, None, self.куратор)["course"]

	def общий(self, **вид) -> str:
		"""Курс из `релиз_двух_целей(**вид)`, который тесты только читают: публикуется
		при первом обращении, дальше отдаётся тот же."""
		ключ = tuple(sorted(вид.items()))
		if ключ not in self.общие:
			номер = len(self.общие)
			self.общие[ключ] = self.опубликовать(релиз_двух_целей(f"{self.ключ_общих}-{номер}", **вид))
		return self.общие[ключ]

	def урок(self, релиз: dict | None = None, ключ_урока: str = "l-1", *, курс: str | None = None):
		"""Курс из релиза, ученик зачислен; отдаёт прохождение урока и занятие по нему.
		`курс` — готовый курс, например общий (`self.общий()`): его тест только
		читает. Без него публикуется свой курс — `релиз`, по умолчанию
		`релиз_двух_целей(self.ключ)`."""
		if курс is None:
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

	def test_старт_квиза(self):
		"""Старт снимает вопросы в попытку без верного варианта и пояснения, в своём
		порядке вопросов и вариантов; отдаёт первый вопрос снимка с ключами
		вариантов и переводит занятие в ожидание квиза. Повторный старт отдаёт ту
		же попытку в том же порядке, ответ сверяется по ключу, а не по месту."""
		run, занятие = self.урок(курс=self.общий())

		with patch.object(release_quiz, "_СЛУЧАЙ", ОБРАТНЫЙ):
			начало = release_quiz.начать(run, занятие)

		with self.subTest("снимок вопросов в порядке попытки, без верного и пояснения"):
			снимок = self.снимок(начало["attempt"])
			self.assertEqual(
				снимок, release_quiz.перемешать(index.вопросы_урока(run.release, "l-1"), ОБРАТНЫЙ)
			)
			self.assertEqual([в["key"] for в in снимок], [С2, С1])
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
		with self.subTest("первый вопрос снимка с ключами вариантов"):
			self.assertEqual(
				начало["question"],
				{
					"id": С2,
					"text": "Ситуация и вопрос 2",
					"kind": "choice",
					"index": 1,
					"total": 2,
					"multiple": False,
					"options": [{"id": "V2", "text": "Второй"}, {"id": "V1", "text": "Первый"}],
				},
			)
		with self.subTest("старт переводит занятие в ожидание квиза"):
			self.assertEqual(
				frappe.db.get_value("Agent Learning Session", занятие, "status"), ЗАНЯТИЕ_ЖДЁТ_КВИЗ
			)
		with self.subTest("повторный старт — тот же порядок"):
			self.assertEqual(release_quiz.начать(run, занятие), начало)
		with self.subTest("ответ — по ключу варианта"):
			ответ = self.ответить(начало["attempt"], С2, "V1")
			self.assertTrue(ответ["verdict"]["correct"])
			self.assertEqual(ответ["next_question"]["id"], С1)

	def test_старт_не_меняет_занятие_не_в_работе(self):
		"""В ожидание квиза переводится только занятие в работе."""
		настроить_квиз(max_attempts=5, retry_delay_minutes=0)
		run, занятие = self.урок(курс=self.общий())
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
		run, занятие = self.урок(курс=self.общий())
		первая = release_quiz.начать(run, занятие)["attempt"]
		self.ответить(первая, С1)

		повтор = release_quiz.начать(run, занятие)

		self.assertEqual(повтор["attempt"], первая)
		self.assertEqual(повтор["question"]["id"], С2)
		self.assertEqual(frappe.db.count(ПОПЫТКА, {"student": self.ученик}), 1)
		self.assertEqual(
			self.журнал(первая),
			[
				(ПРОВЕРКА_ВОПРОС_ВЫДАН, С1),
				(ПРОВЕРКА_ОТВЕТ_ПРИНЯТ, С1),
				(ПРОВЕРКА_ВОПРОС_ВЫДАН, С2),
				(ПРОВЕРКА_ВОПРОС_ВЫДАН, С2),
			],
		)

	def test_отказы_старта(self):
		"""Урок снят из релиза; нет доступа к курсу; занятие другого ученика; урок
		без вопросов — внятный код; прохождение в архиве. У каждого случая свой
		ученик."""
		with self.subTest("урок снят из релиза"):
			релиз = пример_релиза(self.ключ)
			run, занятие = self.урок(релиз, "l-2")
			без_урока = пример_релиза(self.ключ)
			без_урока["chapters"][0]["lessons"] = ["l-1"]
			без_урока["lessons"] = [у for у in без_урока["lessons"] if у["key"] != "l-2"]
			del без_урока["agent"]["lessons"]["l-2"]
			self.опубликовать(без_урока)
			run = прохождения.прохождение(self.ученик, run.course, "l-2")

			self.отказ("lesson_not_in_release", release_quiz.начать, run, занятие)
			self.assertFalse(frappe.db.exists(ПОПЫТКА, {"student": self.ученик}))

		with self.subTest("без доступа к курсу не начать"):
			self.новый_ученик()
			курс = self.общий()
			run = прохождения.прохождение(self.ученик, курс, "l-1")
			занятие = создать_занятие(self.ученик, run.lesson)

			self.отказ(НЕ_ЗАЧИСЛЕН, release_quiz.начать, run, занятие)

		with self.subTest("занятие другого ученика не подходит"):
			self.новый_ученик()
			run, _ = self.урок(курс=self.общий())
			другой = создать_ученика(f"rq-other-{frappe.generate_hash(length=6)}@example.com")

			self.отказ(ЧУЖОЕ_ЗАНЯТИЕ, release_quiz.начать, run, создать_занятие(другой, run.lesson))

		with self.subTest("урок без вопросов даёт внятный код"):
			self.новый_ученик()
			run, занятие = self.урок(релиз_двух_целей(f"{self.ключ}-q"))
			frappe.db.delete("Agent Release Question", {"parent": run.release, "lesson_key": "l-1"})

			self.отказ(КВИЗА_НЕТ, release_quiz.начать, run, занятие)
			self.assertFalse(frappe.db.exists(ПОПЫТКА, {"student": self.ученик}))

		with self.subTest("архивное прохождение квиз не начинает"):
			self.новый_ученик()
			run, занятие = self.урок(курс=self.общий())
			frappe.db.set_value(прохождения.ПРОХОЖДЕНИЕ, run.name, "student", None)

			self.отказ(
				ПРОХОЖДЕНИЕ_В_АРХИВЕ,
				release_quiz.начать,
				frappe.get_doc(прохождения.ПРОХОЖДЕНИЕ, run.name),
				занятие,
			)

	# --- ответ ---

	def test_верный_ответ_с_пояснением(self):
		run, занятие = self.урок(курс=self.общий())
		попытка = release_quiz.начать(run, занятие)["attempt"]

		ответ = self.ответить(попытка, С1, "V1")

		self.assertEqual(ответ["verdict"], {"correct": True, "explanation": ПОЯСНЕНИЕ})
		self.assertEqual(ответ["next_question"]["id"], С2)
		self.assertFalse(ответ["attempt_finished"])
		self.assertNotIn("result", ответ)
		запись = frappe.get_all(
			"Agent Quiz Answer",
			filters={"attempt": попытка},
			fields=["question_key", "objective_key", "answer", "is_correct", "marks", "marks_out_of"],
		)
		self.assertEqual(
			[dict(з) for з in запись],
			[
				{
					"question_key": С1,
					"objective_key": "l-1-D1",
					"answer": "V1",
					"is_correct": 1,
					"marks": 1,
					"marks_out_of": 1,
				}
			],
		)

	# --- отказы ответа ---

	def test_параллельный_ответ_на_тот_же_вопрос_отклоняется(self):
		"""Проверка «уже отвечено» не увидела ответ соседа — держит индекс, попытка цела."""
		run, занятие = self.урок(релиз_двух_целей(self.ключ))
		попытка = release_quiz.начать(run, занятие)["attempt"]
		self.ответить(попытка, С1)

		with patch.object(release_quiz, "_отвечено", return_value=False):
			отказ = self.отказ(ЧУЖОЙ_ВОПРОС, self.ответить, попытка, С1, "V2")

		self.assertNotIn("V1", json.dumps(отказ.подробности, ensure_ascii=False))
		self.assertEqual(frappe.db.count(ОТВЕТ, {"attempt": попытка}), 1)
		self.assertTrue(self.ответить(попытка, С2)["attempt_finished"])

	def test_отказы_ответа(self):
		"""Повторный ответ на вопрос; ответ в завершённую попытку; ответ без доступа
		к курсу. У каждого случая свой ученик."""

		def повторный(попытка, run):
			self.ответить(попытка, С1)

		def завершена(попытка, run):
			frappe.db.set_value(ПОПЫТКА, попытка, "status", ПОПЫТКА_НЕ_ЗАЧТЕНА)

		def без_доступа(попытка, run):
			frappe.db.delete("LMS Enrollment", {"member": self.ученик, "course": run.course})

		случаи = {
			"повторный ответ на вопрос": (повторный, (С1, "V2"), ЧУЖОЙ_ВОПРОС),
			"ответ в завершённую попытку": (завершена, (С1,), ПОПЫТКА_ЗАВЕРШЕНА),
			"ответ без доступа к курсу": (без_доступа, (С1,), НЕ_ЗАЧИСЛЕН),
		}
		for номер, (что, (подготовить, ответ, код)) in enumerate(случаи.items()):
			with self.subTest(что):
				if номер:
					self.новый_ученик()
				run, занятие = self.урок(курс=self.общий())
				попытка = release_quiz.начать(run, занятие)["attempt"]
				подготовить(попытка, run)

				self.отказ(код, self.ответить, попытка, *ответ)

	# --- политика ---

	def test_брошенная_попытка_расходует_лимит(self):
		настроить_квиз(max_attempts=1, retry_delay_minutes=0)
		run, занятие = self.урок(курс=self.общий())
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
		run, занятие = self.урок(релиз_двух_целей(self.ключ))
		попытка = release_quiz.начать(run, занятие)["attempt"]
		frappe.db.set_value(ПОПЫТКА, попытка, {"status": ПОПЫТКА_НЕ_ЗАЧТЕНА, "finished_at": now_datetime()})
		run = self.новый_релиз_урока(run)

		self.отказ(ПОПЫТКИ_ИСЧЕРПАНЫ, release_quiz.начать, run, создать_занятие(self.ученик, run.lesson))

	def test_пауза_переживает_новый_релиз(self):
		настроить_квиз(max_attempts=5, retry_delay_minutes=1440)
		run, занятие = self.урок(релиз_двух_целей(self.ключ))
		попытка = release_quiz.начать(run, занятие)["attempt"]
		frappe.db.set_value(ПОПЫТКА, попытка, {"status": ПОПЫТКА_НЕ_ЗАЧТЕНА, "finished_at": now_datetime()})
		run = self.новый_релиз_урока(run)

		self.отказ(СЛИШКОМ_РАНО, release_quiz.начать, run, создать_занятие(self.ученик, run.lesson))

	def test_повтор_раньше_паузы_отклоняется_с_временем(self):
		настроить_квиз(max_attempts=5, retry_delay_minutes=1440)
		run, занятие = self.урок(курс=self.общий())
		попытка = release_quiz.начать(run, занятие)["attempt"]
		закончена = now_datetime()
		frappe.db.set_value(ПОПЫТКА, попытка, {"status": ПОПЫТКА_НЕ_ЗАЧТЕНА, "finished_at": закончена})

		отказ = self.отказ(СЛИШКОМ_РАНО, release_quiz.начать, run, создать_занятие(self.ученик, run.lesson))

		момент = datetime.fromisoformat(отказ.подробности["retry_after"])
		self.assertIsNotNone(момент.tzinfo)
		self.assertEqual(момент.replace(tzinfo=None), add_to_date(закончена, minutes=1440))

	def test_после_паузы_новая_попытка_со_следующим_номером(self):
		настроить_квиз(max_attempts=5, retry_delay_minutes=60)
		run, занятие = self.урок(курс=self.общий())
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
		"""Проводка `итог_ответов`: снимок, верные ответы и порог попытки дают итог
		в ответе и в записи попытки; граничные случаи зачёта — `TestИтогОтветов`.
		Проводка `_выбранный` по снимку: верный ответ текстом варианта в ином
		регистре и с пробелами, неизвестный ответ — неверный, а не ошибка."""
		run, занятие = self.урок(self.релиз_по_целям())

		ответ = self.сдать(run, занятие, ["  пеРвый ", "V1", "V1", "Третий"])

		ожидаемо = {"l-1-D1": {"correct": 2, "total": 2}, "l-1-D2": {"correct": 1, "total": 2}}
		итог = ответ["result"]
		self.assertEqual(
			(итог["passed"], итог["score"], итог["correct"], итог["total"], итог["pass_threshold"]),
			(True, 0.75, 3, 4, 0.7),
		)
		self.assertEqual(итог["objective_results"], ожидаемо)
		попытка = frappe.get_doc(ПОПЫТКА, ответ["attempt"])
		self.assertEqual((попытка.status, попытка.passed, попытка.score), (ПОПЫТКА_ЗАЧТЕНА, 1, 0.75))
		self.assertIsNotNone(попытка.finished_at)
		self.assertEqual(frappe.parse_json(попытка.objective_results), ожидаемо)
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

		self.assertEqual(ответ["verdict"], {"correct": False})
		self.assertIsNone(ответ["next_question"])
		self.assertTrue(ответ["attempt_finished"])
		итог = ответ["result"]
		self.assertFalse(итог["passed"])
		self.assertNotIn("explanations", итог)
		выдано = json.dumps(ответ, ensure_ascii=False, default=str)
		for утечка in ("V1", "Первый", ПОЯСНЕНИЕ, "explanation", "why_wrong"):
			self.assertNotIn(утечка, выдано)
		self.assertEqual(итог["attempts_left"], 2)
		# Неверный ответ записан без балла из одного.
		self.assertEqual(
			[
				(з.is_correct, з.marks, з.marks_out_of)
				for з in frappe.get_all(
					ОТВЕТ,
					filters={"attempt": ответ["attempt"], "is_correct": 0},
					fields=["is_correct", "marks", "marks_out_of"],
				)
			],
			[(0, 0, 1)],
		)
		закончена = frappe.db.get_value(ПОПЫТКА, ответ["attempt"], "finished_at")
		момент = datetime.fromisoformat(итог["retry_after"])
		self.assertIsNotNone(момент.tzinfo, "время пересдачи — с поясом сайта")
		self.assertEqual(момент.replace(tzinfo=None), add_to_date(закончена, minutes=60))

	def test_остаток_попыток_и_пауза_в_итоге(self):
		"""Последняя попытка — без паузы в итоге. «Без лимита» — ноль в общих
		настройках: остатка нет, а пауза есть. У каждого случая свой ученик."""
		случаи = {
			"последняя попытка без паузы": (1, 0, False),
			"без лимита остаток попыток не число": (0, None, True),
		}
		for номер, (что, (лимит, остаток, пауза)) in enumerate(случаи.items()):
			with self.subTest(что):
				if номер:
					self.новый_ученик()
				настроить_квиз(max_attempts=лимит, retry_delay_minutes=60)
				run, занятие = self.урок(курс=self.общий(вопросов=1))

				итог = self.сдать(run, занятие, ["V2"])["result"]

				self.assertEqual(итог["attempts_left"], остаток)
				self.assertEqual(итог["retry_after"] is not None, пауза)

	def test_несданная_попытка_урок_не_закрывает(self):
		run, занятие = self.урок(курс=self.общий(вопросов=1))

		итог = self.сдать(run, занятие, ["V2"])["result"]

		self.assertEqual(итог["session_status"], ЗАНЯТИЕ_ЖДЁТ_КВИЗ)
		self.assertFalse(
			frappe.db.exists("LMS Course Progress", {"member": self.ученик, "lesson": run.lesson})
		)
		self.assertNotEqual(
			frappe.db.get_value(прохождения.ПРОХОЖДЕНИЕ, run.name, "status"), прохождения.ПРОЙДЕН
		)

	def test_сданный_квиз_закрывает_урок(self):
		run, занятие = self.урок(релиз_двух_целей(self.ключ))
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
			frappe.db.exists(
				"Agent Homework Submission",
				{"member": self.ученик, "lesson": run.lesson, "status": "Assigned"},
			)
		)

	def test_квиз_сдаётся_и_после_закрытия_занятия_по_бездействию(self):
		"""Ученик вернулся к последнему вопросу через сутки — зачёт не теряется, а брошенное занятие брошенным и остаётся."""
		run, занятие = self.урок(курс=self.общий(вопросов=1))
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

	def test_закрытие_без_прохождения_заводит_его_пройденным(self):
		"""Урок закрыт до первой отметки — счёт глав всё равно видит его пройденным."""
		курс = self.общий()
		урок = index.урок(frappe.db.get_value("LMS Course", курс, "active_release"), "l-1")["lesson"]
		зачислить(self.ученик, урок)

		quiz.отметить_урок_пройденным(
			frappe.get_doc("Agent Learning Session", создать_занятие(self.ученик, урок))
		)

		self.assertEqual(
			frappe.db.get_value(
				прохождения.ПРОХОЖДЕНИЕ,
				{"student": self.ученик, "course": курс, "lesson_key": "l-1"},
				"status",
			),
			прохождения.ПРОЙДЕН,
		)
		self.assertEqual(прохождения.главы(self.ученик, курс)[0]["status"], прохождения.ПРОЙДЕН)

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
		run, занятие = self.урок(курс=self.общий())
		self.assertTrue(self.сдать(run, занятие, ["V1", "V1"])["result"]["passed"])
		было = frappe.db.get_value(прохождения.ПРОХОЖДЕНИЕ, run.name, ["passed_at", "modified"], as_dict=True)

		повтор = self.сдать(run, создать_занятие(self.ученик, run.lesson), ["V1", "V1"])

		self.assertTrue(повтор["result"]["passed"])
		стало = frappe.db.get_value(
			прохождения.ПРОХОЖДЕНИЕ, run.name, ["passed_at", "modified"], as_dict=True
		)
		self.assertEqual(стало, было)

	def test_завершённую_попытку_не_завершить_повторно(self):
		run, занятие = self.урок(курс=self.общий(вопросов=1))
		попытка = self.сдать(run, занятие, ["V1"])["attempt"]

		self.отказ(ПОПЫТКА_ЗАВЕРШЕНА, release_quiz.завершить, frappe.get_doc(ПОПЫТКА, попытка))

	def test_снятая_цель_не_подтверждается(self):
		run, _ = self.урок(курс=self.общий())
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

	def test_угловые_скобки_в_тексте_вопроса_не_аннулируют_попытку(self):
		"""Текст вопроса с `<role>`: база хранит его как есть, и прежняя сторона
		сравнения (из базы) совпадает с новой (из релиза)."""
		релиз = релиз_двух_целей(self.ключ, вопросов=3)
		релиз["lessons"][0]["quiz"]["questions"][1]["text"] = "Где тег <role> в промпте?"
		run, _, попытка = self.попытка_с_ответом(релиз)
		релиз["lessons"][0]["title"] = "Урок переименован"

		self.опубликовать(релиз)

		состояние = self.попытка(попытка)
		self.assertEqual((состояние.status, состояние.release), (ПОПЫТКА_ИДЁТ, self.действующий(run.course)))

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
		del без_урока["agent"]["lessons"]["l-2"]

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
		self.assertEqual(
			начало["previous_attempt_cancelled"], {"attempt": попытка, "reason": АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ}
		)
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

	def аннулированная_публикацией(self):
		"""Попытка, которую аннулировала публикация (`quiz_changed`), пункты урока закрыты,
		пользователь — ученик. Отдаёт (занятие, попытка)."""
		релиз = релиз_двух_целей(self.ключ)
		run, занятие = self.урок(релиз)
		отметить_все_пункты(run.name)
		попытка = release_quiz.начать(run, занятие)["attempt"]
		релиз["lessons"][0]["quiz"]["answers"][С1]["correct"] = "V2"
		self.опубликовать(релиз)
		self.assertEqual(self.попытка(попытка).status, ПОПЫТКА_АННУЛИРОВАНА)
		frappe.set_user(self.ученик)
		return занятие, попытка

	def сообщена(self, попытка: str) -> bool:
		return bool(frappe.db.get_value(ПОПЫТКА, попытка, "cancel_reported"))

	def test_аннулированная_публикацией_сообщается_новой_попыткой_один_раз(self):
		"""Агент вернулся к квизу через `request_quiz`, а не ответом: новая попытка
		говорит, почему вопросы начались заново, — один раз."""
		занятие, попытка = self.аннулированная_публикацией()
		self.assertFalse(self.сообщена(попытка))

		первый = student.request_quiz(занятие)
		повтор = student.request_quiz(занятие)

		self.assertTrue(первый["ok"], первый)
		self.assertEqual(
			первый["data"]["previous_attempt_cancelled"],
			{"attempt": попытка, "reason": АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ},
		)
		self.assertTrue(self.сообщена(попытка))
		self.assertEqual(повтор["data"]["attempt"], первый["data"]["attempt"])
		self.assertNotIn("previous_attempt_cancelled", повтор["data"])

	def test_аннулированная_публикацией_сообщается_и_в_новом_занятии(self):
		"""Занятие закрыто по бездействию, ученик вернулся к квизу в новом занятии."""
		занятие, попытка = self.аннулированная_публикацией()
		frappe.db.set_value("Agent Learning Session", занятие, "status", ЗАНЯТИЕ_БРОШЕНО)
		урок = frappe.db.get_value("Agent Learning Session", занятие, "lesson")
		новое = создать_занятие(self.ученик, урок)

		ответ = student.request_quiz(новое)

		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(
			ответ["data"]["previous_attempt_cancelled"],
			{"attempt": попытка, "reason": АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ},
		)

	def test_аннулированная_после_отказа_ответа_не_сообщается_второй_раз(self):
		"""Агент узнал об аннулировании из отказа `submit_answer` — новая попытка о ней молчит."""
		занятие, попытка = self.аннулированная_публикацией()

		отказ = student.submit_answer(попытка, С1, "V1", "слова ученика")
		новая = student.request_quiz(занятие)

		self.assertEqual(отказ["error"]["code"], АННУЛИРОВАНА)
		self.assertTrue(self.сообщена(попытка))
		self.assertTrue(новая["ok"], новая)
		self.assertNotEqual(новая["data"]["attempt"], попытка)
		self.assertNotIn("previous_attempt_cancelled", новая["data"])

	def test_о_прежней_аннулированной_не_сообщается_после_новой_попытки(self):
		"""Сообщается только последняя попытка урока: после аннулированной ученик
		уже начал новую в другом занятии, а квиз просит в третьем."""
		занятие, попытка = self.аннулированная_публикацией()
		# Последняя попытка выбирается по метке создания, а часы контейнера тестов
		# отступают назад (learning-services#509).
		frappe.db.set_value(ПОПЫТКА, попытка, "creation", add_to_date(now_datetime(), days=-1))
		урок = frappe.db.get_value("Agent Learning Session", занятие, "lesson")
		второе, третье = (создать_занятие(self.ученик, урок) for _ in range(2))
		self.assertTrue(student.request_quiz(второе)["ok"])
		# Отметку снимаем: проверяется, что молчит выборка, а не отметка.
		frappe.db.set_value(ПОПЫТКА, попытка, "cancel_reported", 0)

		ответ = student.request_quiz(третье)

		self.assertTrue(ответ["ok"], ответ)
		self.assertNotIn("previous_attempt_cancelled", ответ["data"])

	def test_архивная_аннулированная_после_сброса_не_сообщается(self):
		"""Сброс прогресса архивирует аннулированную попытку: в новом прохождении
		урока о ней не сообщается."""
		from lms_frappe_app.agent_learning.reset import сбросить_прогресс

		занятие, попытка = self.аннулированная_публикацией()
		курс = frappe.db.get_value(ПОПЫТКА, попытка, "course")
		урок = frappe.db.get_value("Agent Learning Session", занятие, "lesson")
		frappe.set_user("Administrator")
		сбросить_прогресс(self.ученик, курс, "Administrator")
		зачислить(self.ученик, урок)
		run = прохождения.прохождение(self.ученик, курс, "l-1")

		начало = release_quiz.начать(run, создать_занятие(self.ученик, урок, run=run.name))

		self.assertNotIn("previous_attempt_cancelled", начало)
		self.assertFalse(self.сообщена(попытка))

	def гонка_с_публикацией(self, run=None, занятие: str | None = None):
		"""Попытка, заведённая в гонке: старт квиза прочёл прежний релиз, а публикация её не застала.
		`run` и `занятие` — урок курса из `релиз_двух_целей(self.ключ)`, иначе заводятся.
		Отдаёт (прохождение по новому релизу, занятие, попытка на прежнем релизе)."""
		релиз = релиз_двух_целей(self.ключ)
		if run is None:
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
			{"attempt": попытка, "session": занятие, "reason": АННУЛИРОВАНА_ПОПЫТКА_ОТСТАЛА},
		)
		self.assertIn("начата на прежней версии курса", str(отказ))
		self.assertEqual(self.попытка(попытка).cancel_reason, АННУЛИРОВАНА_ПОПЫТКА_ОТСТАЛА)
		self.assertEqual(self.попытка(попытка).status, ПОПЫТКА_АННУЛИРОВАНА)
		self.assertFalse(frappe.db.exists(ОТВЕТ, {"attempt": попытка}))
		self.assertEqual(self.журнал(попытка)[-1], (ПРОВЕРКА_ПОПЫТКА_АННУЛИРОВАНА, None))
		# Повтор — тот же отказ по сохранённой причине: квиз не назван изменившимся.
		повтор = self.отказ(АННУЛИРОВАНА, self.ответить, попытка, С1)
		self.assertEqual(повтор.подробности["reason"], АННУЛИРОВАНА_ПОПЫТКА_ОТСТАЛА)
		self.assertIn("начата на прежней версии курса", str(повтор))

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
			{"attempt": попытка, "reason": АННУЛИРОВАНА_ПОПЫТКА_ОТСТАЛА},
		)
		self.assertEqual(
			(self.попытка(попытка).status, self.попытка(попытка).cancel_reason),
			(ПОПЫТКА_АННУЛИРОВАНА, АННУЛИРОВАНА_ПОПЫТКА_ОТСТАЛА),
		)
		self.assertTrue(frappe.db.get_value(ПОПЫТКА, попытка, "cancel_reported"))
		повтор = self.отказ(АННУЛИРОВАНА, self.ответить, попытка, С1)
		self.assertIn("начата на прежней версии курса", str(повтор))
		новая = self.попытка(данные["attempt"])
		self.assertEqual((новая.release, новая.attempt_number), (self.действующий(run.course), 1))

	def test_отставшая_аннулирована_стартом_с_отказом_и_сообщается_следующим(self):
		"""Старт аннулировал отставшую попытку и отказал паузой: отказ флага не несёт,
		и о попытке сообщает следующий старт."""
		run, занятие = self.урок(релиз_двух_целей(self.ключ))
		проваленная = self.сдать(run, занятие, ["V2", "V2"])["attempt"]
		self.assertEqual(self.попытка(проваленная).status, ПОПЫТКА_НЕ_ЗАЧТЕНА)
		давно = add_to_date(now_datetime(), days=-1)
		# Метка создания — тоже в прошлом: последняя попытка урока выбирается по
		# ней, а часы контейнера тестов отступают назад (learning-services#509).
		frappe.db.set_value(ПОПЫТКА, проваленная, {"finished_at": давно, "creation": давно})
		_, _, отставшая = self.гонка_с_публикацией(run, занятие)
		frappe.db.set_value(ПОПЫТКА, проваленная, "finished_at", now_datetime())
		run = прохождения.прохождение(self.ученик, run.course, "l-1")

		self.отказ(СЛИШКОМ_РАНО, release_quiz.начать, run, занятие)
		self.assertEqual(
			(self.попытка(отставшая).status, self.попытка(отставшая).cancel_reason),
			(ПОПЫТКА_АННУЛИРОВАНА, АННУЛИРОВАНА_ПОПЫТКА_ОТСТАЛА),
		)
		self.assertFalse(self.сообщена(отставшая))
		frappe.db.set_value(ПОПЫТКА, проваленная, "finished_at", давно)

		начало = release_quiz.начать(run, занятие)

		self.assertEqual(
			начало["previous_attempt_cancelled"],
			{"attempt": отставшая, "reason": АННУЛИРОВАНА_ПОПЫТКА_ОТСТАЛА},
		)
		self.assertTrue(self.сообщена(отставшая))

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
		"""Дробный порог и порог 0 — без подмены умолчанием поля (`100`): попытка
		без верных при пороге 0 зачтена."""
		for порог in (55.5, 0):
			with self.subTest(порог=порог):
				run, занятие = self.урок(релиз_двух_целей(f"{self.ключ}-{порог}", порог=порог, вопросов=1))

				попытка = release_quiz.начать(run, занятие)["attempt"]

				self.assertEqual(self.попытка(попытка).pass_percentage, порог)
				if порог == 0:
					итог = self.ответить(попытка, С1, "V2")["result"]
					self.assertEqual((итог["passed"], итог["correct"], итог["pass_threshold"]), (True, 0, 0))

	def test_publish_release_при_взаимоблокировке_и_таймауте_busy(self):
		"""Попытку под публикацией изменил или держит ответ ученика: откат целиком и `busy`."""
		from lms_frappe_app.api import authoring

		релиз = релиз_двух_целей(self.ключ)
		self.опубликовать(релиз)

		for ошибка in (frappe.QueryDeadlockError("1213"), frappe.QueryTimeoutError("1205")):
			# Откат подменён, и записанное первым проходом остаётся: каждому — своя версия.
			релиз["lessons"][0]["title"] = f"Новое название, {type(ошибка).__name__}"
			with (
				self.subTest(ошибка=type(ошибка).__name__),
				patch.object(release_quiz, "перенести_попытки", side_effect=ошибка),
				patch.object(frappe.db, "rollback") as откат,
			):
				ответ = authoring.publish_release(release=релиз)

				self.assertEqual(ответ["error"]["code"], "busy")
				откат.assert_called_once_with()

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
		_, занятие = self.урок(релиз_двух_целей(self.ключ))

		with self.гонка_за_прохождение() as откат:
			ответ = self.закрыть(занятие)

		self.assertEqual(ответ["error"]["code"], "busy")
		self.assertEqual(ответ["error"]["session"], занятие)
		откат.assert_called_once_with()

	def test_submit_answer_при_гонке_за_прохождение_busy(self):
		"""Сданная попытка закрывает урок и пишет прохождение — гонка за него даёт `busy`."""
		run, занятие = self.урок(релиз_двух_целей(self.ключ))
		отметить_все_пункты(run.name)
		frappe.set_user(self.ученик)
		попытка = student.request_quiz(занятие)["data"]["attempt"]
		student.submit_answer(попытка, С1, "V1", "слова ученика")

		with self.гонка_за_прохождение() as откат:
			ответ = student.submit_answer(попытка, С2, "V1", "слова ученика")

		self.assertEqual(ответ["error"]["code"], "busy")
		self.assertEqual(ответ["error"]["attempt"], попытка)
		откат.assert_called_once_with()


class TestСравнениеКвиза(UnitTestCase):
	"""Без базы: сравнение квиза — чистая функция."""

	def test_сравнение_квиза_без_пояснений(self):
		"""Пояснение не в счёт, остальное — в счёт, пустой квиз ни с чем не совпадает."""
		вопрос = {
			"key": С1,
			"objective": "l-1-D1",
			"text": "Т",
			"options": [{"key": "V1", "text": "А"}, {"key": "V2", "text": "Б"}],
			"correct": "V1",
			"explanation": "П",
		}
		for что, было, стало, тот_же in (
			("другое пояснение", [вопрос], [{**вопрос, "explanation": "Другое"}], True),
			("другой верный вариант", [вопрос], [{**вопрос, "correct": "V2"}], False),
			("пустой квиз", [], [], False),
		):
			with self.subTest(что):
				self.assertEqual(release_quiz.квиз_тот_же(было, стало), тот_же)


class TestПеремешать(UnitTestCase):
	"""Без базы: порядок вопросов и вариантов новой попытки — `перемешать`."""

	def test_порядок_вопросов_и_вариантов(self):
		"""Вопросы и варианты — в порядке источника случайности, ключи и тексты —
		как в релизе, вход не меняется; по умолчанию у каждого вызова свой порядок."""
		вопросы = [
			{
				"key": f"S{н}",
				"objective": "D1",
				"text": f"Вопрос {н}",
				"options": [{"key": f"V{в}", "text": f"Вариант {в}"} for в in range(1, 5)],
			}
			for н in range(1, 5)
		]
		исходные = copy.deepcopy(вопросы)

		def состав(список):
			return sorted(
				(в["key"], в["objective"], в["text"], sorted((о["key"], о["text"]) for о in в["options"]))
				for в in список
			)

		with self.subTest("порядок — от источника случайности"):
			итог = release_quiz.перемешать(вопросы, ОБРАТНЫЙ)
			self.assertEqual([в["key"] for в in итог], ["S4", "S3", "S2", "S1"])
			for вопрос in итог:
				self.assertEqual([о["key"] for о in вопрос["options"]], ["V4", "V3", "V2", "V1"])
			self.assertEqual(состав(итог), состав(вопросы))
			self.assertEqual(вопросы, исходные)
		with self.subTest("по умолчанию — свой порядок у каждого вызова"):
			итоги = [release_quiz.перемешать(вопросы) for _ in range(20)]
			for итог in итоги:
				self.assertEqual(состав(итог), состав(вопросы))
			self.assertGreater(len({json.dumps(итог) for итог in итоги}), 1)


class TestВыбранныйВариант(UnitTestCase):
	"""Без базы: сравнение ответа с вариантами вопроса — `_выбранный`."""

	def test_ответ_и_варианты(self):
		"""Ключ, затем текст варианта, без учёта регистра и пробелов; список — только из одного."""
		варианты = [{"key": "V1", "text": "Первый"}, {"key": "V2", "text": "Второй"}]
		# Текст варианта `V2` совпадает с ключом `V1`.
		спорные = [{"key": "V1", "text": "Первый"}, {"key": "V2", "text": "V1"}]
		случаи = {
			"ключ": ("V1", варианты, "V1"),
			"ключ без учёта регистра и пробелов": (" v1 ", варианты, "V1"),
			"текст варианта": ("Второй", варианты, "V2"),
			"текст без учёта регистра и пробелов": ("  пеРвый ", варианты, "V1"),
			"пробелы внутри текста сводятся": ("Пер вый", [{"key": "V1", "text": "Пер   вый"}], "V1"),
			"ключ важнее текста": ("V1", спорные, "V1"),
			"список из одного": (["V1"], варианты, "V1"),
			"кортеж из одного текстом": (("второй",), варианты, "V2"),
			"список из двух": (["V1", "V2"], варианты, None),
			"пустой список": ([], варианты, None),
			"неизвестный ответ": ("Третий", варианты, None),
			"пустой ответ": ("", варианты, None),
			"нет ответа": (None, варианты, None),
			"число — по строке": (1, [{"key": "1", "text": "Один"}], "1"),
			"нет вариантов": ("V1", [], None),
		}
		for что, (ответ, список, ожидаемо) in случаи.items():
			with self.subTest(что):
				self.assertEqual(release_quiz._выбранный(ответ, список), ожидаемо)


class TestИтогОтветов(UnitTestCase):
	"""Без базы: зачёт попытки — `итог_ответов` и `подтверждённые_цели`."""

	@staticmethod
	def вопросы(*цели: str) -> list[dict]:
		"""Снимок: вопрос `Q{номер}` на цель из `цели` по порядку."""
		return [{"key": f"Q{номер}", "objective": цель} for номер, цель in enumerate(цели, 1)]

	def test_порог_и_доля(self):
		"""Зачёт — `верных × 100 ≥ порог × всего` в целых; доля — три знака."""
		четыре = self.вопросы(*["D1"] * 4)
		три = self.вопросы(*["D1"] * 3)
		случаи = {
			"3 из 4, порог 70 — сдан": (четыре, {"Q1", "Q2", "Q3"}, 70, (True, 0.75, 3, 4)),
			"3 из 4, порог 75 — ровно на пороге, сдан": (четыре, {"Q1", "Q2", "Q3"}, 75, (True, 0.75, 3, 4)),
			"3 из 4, порог 80 — не сдан": (четыре, {"Q1", "Q2", "Q3"}, 80, (False, 0.75, 3, 4)),
			"2 из 3, дробный порог 66.67 — не сдан": (три, {"Q1", "Q2"}, 66.67, (False, 0.667, 2, 3)),
			"2 из 3, дробный порог 66.66 — сдан": (три, {"Q1", "Q2"}, 66.66, (True, 0.667, 2, 3)),
			"57 из 100, порог 57 — без потери границы": (
				self.вопросы(*["D1"] * 100),
				{f"Q{н}" for н in range(1, 58)},
				57,
				(True, 0.57, 57, 100),
			),
			"порог 0 — сдан и без верных": (self.вопросы("D1", "D1"), set(), 0, (True, 0.0, 0, 2)),
			"порог 100 — все верны": (три, {"Q1", "Q2", "Q3"}, 100, (True, 1.0, 3, 3)),
			"порог 100 — одна ошибка": (три, {"Q1", "Q2"}, 100, (False, 0.667, 2, 3)),
			"порог числом с плавающей точкой": (четыре, {"Q1", "Q2", "Q3"}, 75.0, (True, 0.75, 3, 4)),
			"без вопросов — не сдан и при пороге 0": ([], set(), 0, (False, 0.0, 0, 0)),
			"верный вне снимка не в счёт": (self.вопросы("D1"), {"Q9"}, 50, (False, 0.0, 0, 1)),
		}
		for что, (вопросы, верные, порог, ожидаемо) in случаи.items():
			with self.subTest(что):
				итог = release_quiz.итог_ответов(вопросы, верные, порог)
				self.assertEqual((итог["passed"], итог["score"], итог["correct"], итог["total"]), ожидаемо)

	def test_итог_по_целям_и_подтверждение(self):
		"""Цели — в порядке снимка; подтверждена цель, все вопросы которой верны."""
		вопросы = self.вопросы("D2", "D1", "D2", "D1", "D3")
		случаи = {
			"часть целей верна целиком": (
				{"Q1", "Q2", "Q3", "Q4"},
				{
					"D2": {"correct": 2, "total": 2},
					"D1": {"correct": 2, "total": 2},
					"D3": {"correct": 0, "total": 1},
				},
				{"D1", "D2"},
			),
			"в цели одна ошибка": (
				{"Q1", "Q3", "Q4", "Q5"},
				{
					"D2": {"correct": 2, "total": 2},
					"D1": {"correct": 1, "total": 2},
					"D3": {"correct": 1, "total": 1},
				},
				{"D2", "D3"},
			),
			"ни одного верного": (
				set(),
				{
					"D2": {"correct": 0, "total": 2},
					"D1": {"correct": 0, "total": 2},
					"D3": {"correct": 0, "total": 1},
				},
				set(),
			),
		}
		for что, (верные, по_целям, подтверждены) in случаи.items():
			with self.subTest(что):
				итог = release_quiz.итог_ответов(вопросы, верные, 70)
				self.assertEqual(итог["objective_results"], по_целям)
				self.assertEqual(list(итог["objective_results"]), ["D2", "D1", "D3"])
				self.assertEqual(release_quiz.подтверждённые_цели(итог["objective_results"]), подтверждены)
		self.assertEqual(release_quiz.итог_ответов([], set(), 70)["objective_results"], {})


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
