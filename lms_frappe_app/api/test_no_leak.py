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

from lms_frappe_app.agent_learning.leak_guards import проверить_ответ
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_вопрос,
	создать_квиз,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
	создать_урок,
)
from lms_frappe_app.api import manager, student

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

		урок = student.start_lesson()
		выдано = self.проверить("start_lesson", урок)
		# Директива в start_lesson быть обязана — она адресована агенту.
		self.assertIn("Спросить, какие города", выдано)

		занятие = урок["data"]["session"]
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

		квиз = student.request_quiz(занятие)
		выдано = self.проверить("request_quiz", квиз)
		# Варианты видны, а какой из них верный — нет.
		self.assertIn(ПРАВИЛЬНЫЙ_ВАРИАНТ, выдано)
		self.assertIn(НЕВЕРНЫЙ_ВАРИАНТ, выдано)

		попытка = квиз["data"]["attempt"]
		ответ = student.submit_answer(попытка, self.вопрос, "2", "слова ученика")
		self.проверить("submit_answer", ответ)
		# Неверный ответ не должен подсказывать верный: проверку текста
		# пояснения делает `проверить`.
		self.assertFalse(ответ["data"]["verdict"]["correct"])

	def test_репорты_ученику_без_занятия_вопроса_и_владельца(self):
		"""Ученик видит свои репорты и их итог, но не внутреннюю привязку:
		занятие — устройство платформы, идентификатор вопроса квиза ученику ни
		о чём не говорит, а `owner` у перенесённого репорта — сотрудник."""
		frappe.set_user(self.ученик)
		занятие = student.start_lesson(lesson=self.урок)["data"]["session"]
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
		итоги = student.start_lesson(lesson=self.урок)["data"]["student_context"]["closed_reports"]
		проверить_ответ(self, итоги, "closed_reports", запрещённые_тексты=запрещённые)

		self.assertEqual([р["id"] for р in мои["data"]["reports"]], [репорт])
		self.assertEqual([р["id"] for р in итоги], [репорт])

	def test_зачин_и_обещание_адресованы_ученику_а_не_утечка(self):
		"""Зачин урока и обещание курса — для ученика, в отличие от директивы
		с грифом `teacher_only`. Лежат верхним уровнем ответа и проверку утечек
		проходят; спрятать их под гриф значило бы запретить агенту произносить
		то, ради чего они заведены (#238)."""
		frappe.db.set_value("Course Lesson", self.урок, "lesson_hook", "Зачем тема сейчас")
		frappe.db.set_value("LMS Course", self.курс, "course_promise", "Что получите к концу")
		frappe.set_user(self.ученик)

		ответ = student.start_lesson(lesson=self.урок)
		self.проверить("start_lesson", ответ)
		данные = ответ["data"]

		self.assertEqual(данные["lesson_hook"], "Зачем тема сейчас")
		self.assertEqual(данные["course_promise"], "Что получите к концу")
		self.assertNotIn("lesson_hook", данные["directive"] or {})

	def test_пояснение_приходит_только_к_верному_ответу(self):
		frappe.set_user(self.ученик)
		занятие = student.start_lesson()["data"]["session"]
		попытка = student.request_quiz(занятие)["data"]["attempt"]

		ответ = student.submit_answer(попытка, self.вопрос, "1", "слова ученика")

		self.assertTrue(ответ["data"]["verdict"]["correct"])
		self.assertIn(ТЕКСТ_ПОЯСНЕНИЯ, ответ["data"]["verdict"]["explanation"])

	def test_неверный_ответ_поясняется_без_пояснения_верного(self):
		"""Ошибившийся получает «почему нет» к своему варианту, но не текст,
		поясняющий верный: тот называет ответ до следующей попытки."""
		frappe.set_user(self.ученик)
		занятие = student.start_lesson()["data"]["session"]
		попытка = student.request_quiz(занятие)["data"]["attempt"]

		ответ = student.submit_answer(попытка, self.вопрос, "2", "слова ученика")

		self.проверить("submit_answer", ответ)
		вердикт = ответ["data"]["verdict"]
		self.assertFalse(вердикт["correct"])
		self.assertEqual(вердикт["why_wrong"], ПОЯСНЕНИЕ_НЕВЕРНОГО)

	def test_ни_один_метод_руководителя_не_отдаёт_эталон(self):
		frappe.set_user(self.ученик)
		занятие = student.start_lesson()["data"]["session"]
		попытка = student.request_quiz(занятие)["data"]["attempt"]
		student.submit_answer(попытка, self.вопрос, "1", "слова ученика")

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
		занятие = student.start_lesson()["data"]["session"]
		# Заметка агента о том, что сделал ученик, — ровно то, что отчёт
		# руководителя не имеет права раскрывать (learning-services#409).
		self.проверить(
			"mark_objective",
			student.mark_objective(
				занятие, 1, "covered", "ученик перепутал столицу с крупнейшим городом"
			),
		)
		попытка = student.request_quiz(занятие)["data"]["attempt"]
		student.submit_answer(попытка, self.вопрос, "2", "слова ученика")

		frappe.set_user(self.менеджер)
		# Реплика ученика из журнала — то, что отчёт не имеет права раскрывать.
		# Прежняя проверка искала здесь текст варианта ответа, которого в
		# записи не бывает: ответ хранится номером.
		проверить_ответ(
			self,
			manager.student_detail(self.ученик),
			"student_detail",
			запрещённые_тексты=("перепутал столицу",),
		)
