# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Регрессия на утечку эталонов.

Прогоняет **все** методы контракта и ищет в ответах то, чего там быть не
может. Дёшево и ловит самый дорогой класс ошибок: утечка эталона обесценивает
серверный квиз целиком, а с ним и устойчивость схемы к пересказу директивы
агентом.

Проверка намеренно тупая — поиск подстрок по всему JSON. Умная проверка
пропустит поле, добавленное завтра.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import release_quiz
from lms_frappe_app.agent_learning.leak_guards import проверить_ответ
from lms_frappe_app.agent_learning.releases import service as релизы
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	создать_вопрос,
	создать_домашку,
	создать_занятие,
	создать_квиз,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
	создать_урок,
)
from lms_frappe_app.api import manager, public, review, student

ПРАВИЛЬНЫЙ_ВАРИАНТ = "Москва"
НЕВЕРНЫЙ_ВАРИАНТ = "Тула"
ТЕКСТ_ПОЯСНЕНИЯ = "Столицей она стала в пятнадцатом веке"
#: Пояснение неверного варианта — его отдавать можно: ответа оно не называет.
ПОЯСНЕНИЕ_НЕВЕРНОГО = "Тула — оружейный город, но не столица"


class IntegrationTestNoLeak(IntegrationTestCase):
	"""Ни один метод не отдаёт эталон и не протекает структурами Frappe."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)

		self.ученик = создать_ученика(f"leak-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = frappe.db.get_value(
			"Course Chapter", frappe.db.get_value("Course Lesson", self.урок, "chapter"), "course"
		)
		self.вопрос = создать_вопрос(
			"Столица России?",
			варианты=[(ПРАВИЛЬНЫЙ_ВАРИАНТ, True), (НЕВЕРНЫЙ_ВАРИАНТ, False, ПОЯСНЕНИЕ_НЕВЕРНОГО)],
			пояснение=ТЕКСТ_ПОЯСНЕНИЯ,
		)
		создать_квиз(self.урок, [self.вопрос])

		frappe.get_doc(
			{
				"doctype": "Agent Lesson Directive",
				"lesson": self.урок,
				"teaching_directive": "Спросить, какие города ученик считает столицами",
				"success_criteria": "Называет верно",
			}
		).insert(ignore_permissions=True)

		self.организация = создать_организацию(f"Компания {суффикс}")
		добавить_в_организацию(self.ученик, self.организация)
		frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.организация,
				"course": self.курс,
				"deadline": "2026-12-31",
			}
		).insert(ignore_permissions=True)
		self.менеджер = создать_менеджера(f"leakmg-{суффикс}@example.com", self.организация)
		frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"course": self.курс,
				"slug": "summary",
				"title": "Резюме проекта",
				"blocks": [{"block_key": "goal", "title": "Цель", "hint": "Одной фразой"}],
			}
		).insert(ignore_permissions=True)

	def проверить(self, что: str, ответ) -> str:
		"""Ответ без эталонов, внутренностей Frappe и текста пояснения.

		Текст проверяется отдельно от имён полей: утечка вида
		`{"hint": <текст пояснения>}` мимо проверки имён проходит.
		"""
		return проверить_ответ(self, ответ, что, запрещённые_тексты=(ТЕКСТ_ПОЯСНЕНИЯ,))

	def test_ни_один_метод_ученика_не_отдаёт_эталон(self):
		frappe.set_user(self.ученик)

		self.проверить("list_my_courses", student.list_my_courses())
		self.проверить("get_my_progress", student.get_my_progress())

		# Курс старой модели: `start_lesson` ему отказывает, занятие для старых
		# методов заводится напрямую; старт проверяет класс курса из релиза.
		занятие = создать_занятие(self.ученик, self.урок)
		self.проверить(
			"report_issue",
			student.report_issue(занятие, kind="stuck", text="Ученик встал на примере"),
		)
		# Целей у директивы этого урока нет, поэтому отчёт пустой и проходит.
		self.проверить("report_outcomes", student.report_outcomes(занятие, outcomes=[]))
		self.проверить(
			"remember",
			student.remember(kind="fact", key="role", text="Руководитель отдела"),
		)
		self.проверить("my_notes", student.my_notes())
		self.проверить("my_profile", student.my_profile())
		self.проверить("my_profile", student.my_profile(summary=1))
		self.проверить("forget", student.forget(key="role"))
		self.проверить(
			"update_artifact",
			student.update_artifact(self.курс, "summary", "goal", "Открыть кофейню"),
		)
		self.проверить("artifact", student.artifact(self.курс))
		self.проверить("artifact", student.artifact(self.курс, "summary"))
		self.проверить(
			"save_chat_state",
			student.save_chat_state(занятие, json.dumps({"messages": []}), "1"),
		)
		self.проверить("chat_state", student.chat_state(занятие))
		self.проверить(
			"save_scenario_state",
			student.save_scenario_state("profile", json.dumps({"messages": []}), "1"),
		)
		self.проверить("scenario_state", student.scenario_state("profile"))
		self.проверить("count_scenario_turn", student.count_scenario_turn("profile"))
		self.проверить("reset_scenario_state", student.reset_scenario_state("profile"))

	def test_репорты_ученику_без_занятия_вопроса_и_владельца(self):
		"""Ученик видит свои репорты и их итог, но не внутреннюю привязку:
		занятие — устройство платформы, идентификатор вопроса квиза ученику ни
		о чём не говорит, а `owner` у перенесённого репорта — сотрудник."""
		frappe.set_user(self.ученик)
		занятие = создать_занятие(self.ученик, self.урок)
		репорт = student.report_issue(
			занятие, kind="quiz_question_issue", text="Вопрос двусмысленный", question=self.вопрос
		)["data"]["report"]
		frappe.set_user("Administrator")
		frappe.get_doc("Agent Course Report", репорт).update(
			{"status": "Fixed", "resolution": "Переписали вопрос"}
		).save(ignore_permissions=True)
		frappe.set_user(self.ученик)
		запрещённые = (ТЕКСТ_ПОЯСНЕНИЯ, self.вопрос, занятие, "Administrator")

		мои = student.my_reports()
		проверить_ответ(self, мои, "my_reports", запрещённые_тексты=запрещённые)
		итоги = student.student_context(занятие)["data"]["closed_reports"]
		проверить_ответ(self, итоги, "closed_reports", запрещённые_тексты=запрещённые)

		self.assertEqual([р["id"] for р in мои["data"]["reports"]], [репорт])
		self.assertEqual([р["id"] for р in итоги], [репорт])

	def test_ни_один_метод_руководителя_не_отдаёт_эталон(self):
		frappe.set_user(self.ученик)
		создать_занятие(self.ученик, self.урок)

		frappe.set_user(self.менеджер)

		self.проверить("org_report", manager.org_report())
		self.проверить("student_detail", manager.student_detail(self.ученик))

	def test_отчёт_руководителя_не_несёт_ответов_ученика(self):
		# Отчёт про результат, а не про содержание диалога.
		frappe.set_user("Administrator")
		frappe.db.set_value(
			"Agent Lesson Directive",
			{"lesson": self.урок},
			"objectives",
			"Отличать столицу от крупнейшего города",
		)
		frappe.set_user(self.ученик)
		занятие = создать_занятие(self.ученик, self.урок)
		# Заметка агента о том, что сделал ученик, — ровно то, что отчёт
		# руководителя не имеет права раскрывать (learning-services#409).
		self.проверить(
			"mark_objective",
			student.mark_objective(
				занятие, 1, "covered", "ученик перепутал столицу с крупнейшим городом"
			),
		)

		frappe.set_user(self.менеджер)
		проверить_ответ(
			self,
			manager.student_detail(self.ученик),
			"student_detail",
			запрещённые_тексты=("перепутал столицу",),
		)

	def test_методы_куратора_не_отдают_эталон(self):
		"""Очередь и карточка домашки (learning-services#452): сдача, задание и
		журнал — без эталонов и структур Frappe."""
		создать_домашку(self.урок)
		frappe.set_user(self.ученик)
		сдача = student.submit_homework(lesson=self.урок, answer="Сделал")["data"]["submission"]["id"]

		frappe.set_user(self.менеджер)
		self.проверить("review.queue", review.queue())
		self.проверить("review.pending_count", review.pending_count())
		self.проверить("review.submission", review.submission(submission=сдача))
		self.проверить("review.send_back", review.send_back(submission=сдача, version=1, comment="Доделай"))


#: Тексты релиза, которых ученик и руководитель не видят: пояснения верных
#: ответов, пакет агента и карта курса (learning-services#500). Пояснение
#: приходит только к верному ответу и в итоге сданной попытки (F2,
#: learning-services#504) — их проверяют тесты квиза ниже.
ПОЯСНЕНИЕ_РЕЛИЗА = "Пояснение верного варианта из релиза"
ПОЯСНЕНИЕ_ВТОРОГО = "Пояснение второго вопроса из релиза"
ПАКЕТ_АГЕНТА = "Текст пакета агента из релиза"
КАРТА_КУРСА = "Текст карты курса из релиза"
#: Свидетельство агента к пункту прохождения: пункты — инструмент агента,
#: ученику и руководителю они не показываются (learning-services#504).
СВИДЕТЕЛЬСТВО = "Свидетельство агента к пункту прохождения"
СЛОВА_УЧЕНИКА = "Слова ученика в ответ на вопрос из релиза"
#: Подробности пункта из пакета агента — отдаёт только `lesson_item`.
ТЕКСТ_ПУНКТА = "Подробности пункта из пакета агента"
#: Всё закрытое из релиза и прохождения — одним списком на все проверки класса.
ЗАКРЫТОЕ_РЕЛИЗА = (ПОЯСНЕНИЕ_РЕЛИЗА, ПОЯСНЕНИЕ_ВТОРОГО, ПАКЕТ_АГЕНТА, ТЕКСТ_ПУНКТА, КАРТА_КУРСА, СВИДЕТЕЛЬСТВО)
#: Пункты целей агента — ключи и названия: методы уровня целей урока (дерево
#: курса, карта, занятие урока, прогресс в заметке и документе, отчёт
#: руководителя) отдают цели без пунктов (learning-services#506).
ПУНКТЫ_РЕЛИЗА = (
	"term:T1",
	"l-1-D1/V1",
	"refute:M1",
	"Термин «пример»",
	"Выбор: «первый»",
	"Если проявится",
)
ВОПРОС_1, ВОПРОС_2 = "S1/l-1-D1", "S2/l-1-D1"
ПОПЫТКА = "Agent Quiz Attempt"
ОТВЕТ = "Agent Quiz Answer"
ПРОХОЖДЕНИЕ = "Agent Lesson Run"


class IntegrationTestNoLeakRelease(IntegrationTestCase):
	"""Курс из релиза: ответы квиза из индекса, пакет агента, карта и пункты
	прохождения не уходят ни в один ответ ученику и руководителю."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		релиз = пример_релиза(f"leak-{суффикс}")
		for урок in релиз["lessons"]:
			for ответ in урок["quiz"]["answers"].values():
				ответ["explanation"] = ПОЯСНЕНИЕ_РЕЛИЗА
		# Второй вопрос первого урока и порог 50: один неверный ответ из двух
		# сдаёт попытку — так видно, что пояснение к нему приходит в итоге.
		квиз = релиз["lessons"][0]["quiz"]
		квиз["pass_percentage"] = 50
		квиз["questions"].append(
			{
				"key": ВОПРОС_2,
				"objective": "l-1-D1",
				"text": "Второй вопрос",
				"options": [{"key": "V1", "text": "Первый"}, {"key": "V2", "text": "Второй"}],
			}
		)
		квиз["answers"][ВОПРОС_2] = {"correct": "V2", "explanation": ПОЯСНЕНИЕ_ВТОРОГО}
		релиз["agent"] = {"lessons": {"l-1": {"directive": ПАКЕТ_АГЕНТА, "items": {"term:T1": ТЕКСТ_ПУНКТА}}}}
		релиз["map"] = {"nodes": [{"text": КАРТА_КУРСА}]}
		frappe.set_user("Administrator")
		данные = релизы.опубликовать(релиз, None, "Administrator")
		self.курс = данные["course"]
		self.урок = frappe.db.get_value("Course Lesson", {"course": self.курс, "title": "Урок первый"})
		self.ученик = создать_ученика(f"leak-rel-{суффикс}@example.com")
		зачислить(self.ученик, self.урок)
		self.организация = создать_организацию(f"Релиз {суффикс}")
		добавить_в_организацию(self.ученик, self.организация)
		frappe.get_doc(
			{"doctype": "Course Allocation", "organization": self.организация, "course": self.курс}
		).insert(ignore_permissions=True)
		self.менеджер = создать_менеджера(f"leak-rel-mg-{суффикс}@example.com", self.организация)
		# Прохождение с отметкой: свидетельство не должно уйти ни в один ответ.
		self.run = прохождения.прохождение(self.ученик, self.курс, "l-1")
		прохождения.отметить(self.run.name, "term:T1", "done", СВИДЕТЕЛЬСТВО)

	def проверить(self, что: str, ответ, *ещё: str) -> str:
		return проверить_ответ(self, ответ, что, запрещённые_тексты=(*ЗАКРЫТОЕ_РЕЛИЗА, *ещё))

	def ответить(self, попытка: str, вопрос: str, вариант: str) -> dict:
		return release_quiz.ответить(попытка, вопрос, вариант, СЛОВА_УЧЕНИКА)

	def попытка(self) -> dict:
		return release_quiz.начать(self.run, создать_занятие(self.ученик, self.урок))

	def test_ученик_и_руководитель_не_видят_закрытого_из_релиза(self):
		frappe.set_user(self.ученик)
		self.проверить("list_my_courses", student.list_my_courses())
		self.проверить("get_my_progress", student.get_my_progress())
		self.проверить("course_outline", student.course_outline(self.курс), *ПУНКТЫ_РЕЛИЗА)
		self.проверить("course_map", public.course_map(course=self.курс), *ПУНКТЫ_РЕЛИЗА)
		self.проверить("lesson_session", student.lesson_session(self.урок), *ПУНКТЫ_РЕЛИЗА)
		урок = student.start_lesson(lesson=self.урок)
		self.assertTrue(урок["ok"], урок)
		# Пакет агента — только агентским методам, и старт отдаёт его урок
		# (learning-services#506); ответы, карта и свидетельство — никому.
		выдано = проверить_ответ(
			self, урок, "start_lesson", запрещённые_тексты=tuple(т for т in ЗАКРЫТОЕ_РЕЛИЗА if т != ПАКЕТ_АГЕНТА)
		)
		self.assertIn(ПАКЕТ_АГЕНТА, выдано)
		занятие = урок["data"]["session"]
		# Подробности пункта — тоже пакет агента, но только своего пункта.
		выдано = проверить_ответ(
			self,
			student.lesson_item(занятие, "term:T1"),
			"lesson_item",
			запрещённые_тексты=tuple(т for т in ЗАКРЫТОЕ_РЕЛИЗА if т != ТЕКСТ_ПУНКТА),
		)
		self.assertIn(ТЕКСТ_ПУНКТА, выдано)
		self.проверить("lesson_item", student.lesson_item(занятие, "l-1-D1/V1"))
		self.проверить(
			"mark_goal",
			student.mark_goal(занятие, "l-1-D1/V1", "done", СВИДЕТЕЛЬСТВО, resume_from="С примера ученика"),
		)
		# Посреди занятия: прогресс — цели урока, без пунктов.
		заметка = self.проверить(
			"remember", student.remember("observation", "pace", "Торопится", session=занятие), *ПУНКТЫ_РЕЛИЗА
		)
		self.assertIn("objectives_progress", заметка)
		документ = self.проверить(
			"update_artifact",
			student.update_artifact(self.курс, "notebook", "log", rows=[{"topic": "Встреча"}]),
			*ПУНКТЫ_РЕЛИЗА,
		)
		self.assertIn("objectives_progress", документ)
		self.проверить("lesson_session", student.lesson_session(self.урок), *ПУНКТЫ_РЕЛИЗА)
		квиз = student.request_quiz(занятие)
		self.assertNotIn("correct", self.проверить("request_quiz", квиз))
		попытка = квиз["data"]["attempt"]
		первый = student.submit_answer(попытка, ВОПРОС_1, "V2", СЛОВА_УЧЕНИКА)
		self.assertEqual(первый["data"]["verdict"], {"correct": False})
		self.проверить("submit_answer", первый, СЛОВА_УЧЕНИКА)
		итог = student.submit_answer(попытка, ВОПРОС_2, "V1", СЛОВА_УЧЕНИКА)
		self.assertFalse(итог["data"]["result"]["passed"])
		self.проверить("submit_answer", итог, СЛОВА_УЧЕНИКА)
		self.проверить(
			"update_artifact",
			student.update_artifact(self.курс, "notebook", "log", rows=[{"topic": "Первая встреча"}]),
		)
		self.проверить("artifact", student.artifact(self.курс))
		# У таблицы документа своё поле `owner` — блок, который её заводит
		# (CONTRACT.md, «Зачем контракт именно такой»); остальное — полным списком.
		проверить_ответ(
			self,
			student.artifact(self.курс, "notebook"),
			"artifact",
			запрещённые_тексты=ЗАКРЫТОЕ_РЕЛИЗА,
			кроме=("owner",),
		)

		frappe.set_user(self.менеджер)
		self.проверить("org_report", manager.org_report())
		отчёт = self.проверить("student_detail", manager.student_detail(self.ученик), *ПУНКТЫ_РЕЛИЗА)
		self.assertIn("Цель урока «Урок первый»", отчёт)

	def test_снимок_и_ответ_не_называют_верного(self):
		"""Снимок попытки — без `correct`; неверный ответ — только `correct: false`,
		несданная попытка — без пояснений (F2)."""
		начало = self.попытка()
		попытка = начало["attempt"]

		снимок = frappe.db.get_value(ПОПЫТКА, попытка, "questions")
		self.assertNotIn("correct", снимок)
		self.проверить("снимок попытки", снимок)
		выдано = self.проверить("начало попытки", начало)
		self.assertNotIn("correct", выдано)

		первый = self.ответить(попытка, ВОПРОС_1, "V2")
		self.assertEqual(первый["verdict"], {"correct": False})
		self.assertNotIn("correct", json.dumps(первый["next_question"], ensure_ascii=False))
		self.проверить("ответ на вопрос", первый)

		второй = self.ответить(попытка, ВОПРОС_2, "V1")
		self.assertEqual(второй["verdict"], {"correct": False})
		self.assertFalse(второй["result"]["passed"])
		self.assertNotIn("explanations", второй["result"])
		self.проверить("итог несданной попытки", второй)

	def test_пояснение_к_неверному_только_в_итоге_сданной(self):
		"""F2: к верному ответу — сразу, к неверному — только в итоге сданной попытки."""
		попытка = self.попытка()["attempt"]

		неверный = self.ответить(попытка, ВОПРОС_1, "V2")
		self.assertEqual(неверный["verdict"], {"correct": False})
		self.проверить("неверный ответ", неверный)

		верный = self.ответить(попытка, ВОПРОС_2, "V2")
		self.assertEqual(верный["verdict"], {"correct": True, "explanation": ПОЯСНЕНИЕ_ВТОРОГО})
		итог = верный["result"]
		self.assertTrue(итог["passed"])
		self.assertEqual(
			[(п["id"], п["explanation"]) for п in итог["explanations"]], [(ВОПРОС_1, ПОЯСНЕНИЕ_РЕЛИЗА)]
		)
		# Оба пояснения здесь законны: к верному ответу и в итоге сданной попытки.
		законные = (ПОЯСНЕНИЕ_РЕЛИЗА, ПОЯСНЕНИЕ_ВТОРОГО)
		проверить_ответ(
			self,
			верный,
			"итог сданной попытки",
			запрещённые_тексты=tuple(т for т in ЗАКРЫТОЕ_РЕЛИЗА if т not in законные),
		)

	def test_прохождение_не_читают_ученик_и_руководитель(self):
		for кто in (self.ученик, self.менеджер):
			frappe.set_user(кто)
			self.assertFalse(frappe.has_permission(ПРОХОЖДЕНИЕ, "read", doc=self.run.name), кто)
			with self.assertRaises(frappe.PermissionError, msg=кто):
				frappe.get_list(ПРОХОЖДЕНИЕ, fields=["name"])
			for строки in ("Agent Lesson Run Goal", "Agent Lesson Run Objective"):
				with self.assertRaises(frappe.PermissionError, msg=f"{кто}: {строки}"):
					frappe.get_list(строки, parent_doctype=ПРОХОЖДЕНИЕ, fields=["name"])

	def test_ответы_попытки_по_релизу_не_читает_руководитель(self):
		"""Итог попытки руководителю виден, ответы — нет: в записи ответа лежит эталон."""
		попытка = self.попытка()["attempt"]
		self.ответить(попытка, ВОПРОС_1, "V1")
		[ответ] = frappe.get_all(ОТВЕТ, filters={"attempt": попытка}, pluck="name")

		frappe.set_user(self.менеджер)
		self.assertTrue(frappe.has_permission(ПОПЫТКА, "read", doc=попытка), "попытка сотрудника видна")
		self.assertFalse(frappe.has_permission(ОТВЕТ, "read", doc=ответ))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list(ОТВЕТ, filters={"attempt": попытка}, fields=["question_key", "is_correct"])
		self.проверить("student_detail", manager.student_detail(self.ученик), СЛОВА_УЧЕНИКА)
		self.проверить("org_report", manager.org_report(), СЛОВА_УЧЕНИКА)
