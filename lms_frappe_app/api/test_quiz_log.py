# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Журнал проверки: выданные вопросы и принятые ответы со словами ученика.

`Why:` на квиз отвечает агент, и серверный вердикт не говорит, отвечал ли сам
ученик. Журнал — материал для разбора курса: что спросили, что агент прислал,
что написал ученик и что решил сервер. В нём ответы рядом с вердиктом, то есть
готовые эталоны, поэтому читают его только администраторы платформы.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import quiz
from lms_frappe_app.api import student
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	политика_по_умолчанию,
	создать_вопрос,
	создать_занятие,
	создать_квиз,
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_урок,
	создать_ученика,
)

ЖУРНАЛ = "Agent Quiz Event"
ВЫДАН = "Question Issued"
ПРИНЯТ = "Answer Submitted"


class IntegrationTestQuizLog(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)

		self.организация = создать_организацию(f"Компания {суффикс}")
		self.ученик = создать_ученика(f"qlog-{суффикс}@example.com")
		добавить_в_организацию(self.ученик, self.организация)
		self.менеджер = создать_менеджера(f"qlogm-{суффикс}@example.com", self.организация)

		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(self.ученик, self.урок)
		self.первый = создать_вопрос(f"Столица? {суффикс}", варианты=[("Москва", True), ("Тула", False)])
		self.второй = создать_вопрос(f"Оператор повторения? {суффикс}", возможные_ответы=["цикл"])
		создать_квиз(self.урок, [self.первый, self.второй])
		self.занятие = создать_занятие(self.ученик, self.урок)
		# Занятие в пространстве организации: так попытку читает руководитель,
		# и запрет на журнал проверяется там, где он действительно что-то
		# отнимает.
		frappe.db.set_value("Agent Learning Session", self.занятие, "organization", self.организация)

		frappe.set_user(self.ученик)
		начало = student.request_quiz(self.занятие)
		self.assertTrue(начало["ok"], начало)
		self.попытка = начало["data"]["attempt"]

	def события(self) -> list:
		return frappe.get_all(
			ЖУРНАЛ,
			filters={"attempt": self.попытка},
			fields=[
				"kind",
				"question",
				"answer",
				"student_words",
				"is_correct",
				"session",
				"student",
				"course",
				"lesson",
				"occurred_at",
			],
			order_by="occurred_at asc, creation asc",
		)

	def ответить(self, вопрос: str, ответ: str, слова=None) -> dict:
		return student.submit_answer(self.попытка, вопрос, ответ, слова)

	# --- что пишется ---

	def test_попытка_целиком_ложится_в_журнал_по_порядку(self):
		self.assertTrue(self.ответить(self.первый, "1", "Москва, конечно")["ok"])
		итог = self.ответить(self.второй, "мимо", "Не помню, пусть будет «мимо»")
		self.assertTrue(итог["data"]["attempt_finished"])

		события = self.события()

		self.assertEqual(
			[(с.kind, с.question) for с in события],
			[
				(ВЫДАН, self.первый),
				(ПРИНЯТ, self.первый),
				(ВЫДАН, self.второй),
				(ПРИНЯТ, self.второй),
			],
		)
		первый, второй = события[1], события[3]
		self.assertEqual(
			(первый.answer, первый.student_words, первый.is_correct),
			("1", "Москва, конечно", 1),
		)
		self.assertEqual(
			(второй.answer, второй.student_words, второй.is_correct),
			("мимо", "Не помню, пусть будет «мимо»", 0),
		)
		# Выданный вопрос — без ответа и слов: их ещё нет.
		self.assertFalse(события[0].answer or события[0].student_words)
		for событие in события:
			self.assertEqual(
				(событие.session, событие.student, событие.course, событие.lesson),
				(self.занятие, self.ученик, self.курс, self.урок),
			)
			self.assertTrue(событие.occurred_at)

	def test_возвращённая_попытка_выдаёт_вопрос_ещё_раз(self):
		"""Каждая выдача — факт для разбора: видно, сколько раз ученик
		возвращался к вопросу."""
		повтор = student.request_quiz(self.занятие)["data"]

		self.assertEqual(повтор["attempt"], self.попытка)
		self.assertEqual(
			[(с.kind, с.question) for с in self.события()],
			[(ВЫДАН, self.первый), (ВЫДАН, self.первый)],
		)

	# --- слова ученика обязательны ---

	def test_без_слов_ученика_ответ_не_принят(self):
		for слова in (None, "", "   \n\t"):
			with self.subTest(слова=слова):
				ответ = self.ответить(self.первый, "1", слова)

				self.assertFalse(ответ["ok"])
				self.assertEqual(ответ["error"]["code"], quiz.НУЖНЫ_СЛОВА)

		# Ни ответа, ни записи о нём: отказ ничего не меняет, и тот же вопрос
		# остаётся открытым для ответа со словами.
		self.assertFalse(frappe.db.exists("Agent Quiz Answer", {"attempt": self.попытка}))
		self.assertEqual([с.kind for с in self.события()], [ВЫДАН])
		self.assertTrue(self.ответить(self.первый, "1", "Москва")["ok"])

	def test_отклонённый_ответ_в_журнал_не_попадает(self):
		чужой = создать_вопрос("Не из этого квиза", варианты=[("да", True), ("нет", False)])

		ответ = self.ответить(чужой, "1", "да")

		self.assertEqual(ответ["error"]["code"], quiz.ЧУЖОЙ_ВОПРОС)
		self.assertEqual([с.kind for с in self.события()], [ВЫДАН])

	def test_длинные_слова_обрезаются_а_не_отклоняются(self):
		ответ = self.ответить(self.первый, "1", "я" * (quiz.ДЛИНА_СЛОВ + 500))

		self.assertTrue(ответ["ok"])
		принят = next(с for с in self.события() if с.kind == ПРИНЯТ)
		self.assertEqual(len(принят.student_words), quiz.ДЛИНА_СЛОВ)

	def test_слова_на_вердикт_не_влияют(self):
		"""Судит сервер по `answer`: слова только лежат в журнале."""
		вердикт = self.ответить(self.первый, "2", "Москва, я уверен")["data"]["verdict"]

		self.assertFalse(вердикт["correct"])

	# --- кто читает ---

	def test_ученик_и_руководитель_журнал_не_читают(self):
		"""В журнале ответы рядом с вердиктом — готовые эталоны. Руководитель
		попытку своего сотрудника читает, а журнал по ней — нет."""
		self.ответить(self.первый, "1", "Москва")
		событие = frappe.get_all(ЖУРНАЛ, filters={"attempt": self.попытка}, pluck="name", limit=1)[0]

		frappe.set_user(self.менеджер)
		self.assertTrue(frappe.has_permission("Agent Quiz Attempt", "read", doc=self.попытка))

		for кто in (self.ученик, self.менеджер):
			with self.subTest(кто=кто):
				frappe.set_user(кто)
				self.assertFalse(frappe.has_permission(ЖУРНАЛ, "read"))
				self.assertFalse(frappe.has_permission(ЖУРНАЛ, "read", doc=событие))
				self.assertEqual(self._видно_списком(), [])

	def test_модератор_журнал_читает_но_не_правит(self):
		self.ответить(self.первый, "1", "Москва")
		событие = frappe.get_all(ЖУРНАЛ, filters={"attempt": self.попытка}, pluck="name", limit=1)[0]
		модератор = создать_куратора(
			f"qlogmod-{frappe.generate_hash(length=6)}@example.com", роль="Moderator"
		)

		frappe.set_user(модератор)

		self.assertTrue(frappe.has_permission(ЖУРНАЛ, "read", doc=событие))
		self.assertIn(событие, self._видно_списком())
		for право in ("write", "create", "delete"):
			self.assertFalse(frappe.has_permission(ЖУРНАЛ, право, doc=событие), право)

	def test_роли_журнала_только_администраторы_платформы(self):
		"""Пин на схему: право, выданное ещё одной роли, открыло бы эталоны."""
		права = frappe.get_meta(ЖУРНАЛ).permissions

		self.assertEqual({п.role for п in права}, {"System Manager", "Moderator"})
		for п in права:
			self.assertTrue(п.read)
			self.assertFalse(п.write or п.create or п.delete, п.role)

	def test_из_попытки_в_desk_видны_её_события(self):
		связи = {(с.link_doctype, с.link_fieldname) for с in frappe.get_meta("Agent Quiz Attempt").links}

		self.assertIn((ЖУРНАЛ, "attempt"), связи)

	def _видно_списком(self) -> list[str]:
		"""Что пользователь получает списком — как через `/api/resource`."""
		try:
			return frappe.get_list(ЖУРНАЛ, filters={"attempt": self.попытка}, pluck="name")
		except frappe.PermissionError:
			return []
