# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Журнал проверки: выданные вопросы и принятые ответы со словами ученика.

`Why:` на квиз отвечает агент, и серверный вердикт не говорит, отвечал ли сам
ученик. Журнал — материал для разбора курса: что спросили, что агент прислал,
что написал ученик и что решил сервер. В нём ответы рядом с вердиктом, то есть
готовые эталоны, поэтому читают его только администраторы платформы.
"""

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.agent_learning import quiz
from lms_frappe_app.api import student
from lms_frappe_app.tests.release_sample import релиз_двух_целей
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	занятие_релиза,
	зачислить_на_курс,
	курс_из_релиза,
	отметить_все_пункты,
	политика_по_умолчанию,
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
)

ЖУРНАЛ = "Agent Quiz Event"
ВЫДАН = "Question Issued"
ПРИНЯТ = "Answer Submitted"


class IntegrationTestQuizLog(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		"""Курс тесты только читают; ученик, организация и попытка — у каждого теста свои."""
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.курс, _ = курс_из_релиза(релиз=релиз_двух_целей(f"qlog-{frappe.generate_hash(length=6)}"))

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)

		self.организация = создать_организацию(f"Компания {суффикс}")
		self.ученик = создать_ученика(f"qlog-{суффикс}@example.com")
		добавить_в_организацию(self.ученик, self.организация)

		зачислить_на_курс(self.ученик, self.курс)
		self.первый, self.второй = "S1/l-1-D1", "S2/l-1-D1"
		self.занятие = занятие_релиза(self.ученик, self.курс, "l-1")
		self.урок = frappe.db.get_value("Agent Learning Session", self.занятие, "lesson")
		# Занятие в пространстве организации: так попытку читает руководитель,
		# и запрет на журнал проверяется там, где он действительно что-то
		# отнимает.
		frappe.db.set_value("Agent Learning Session", self.занятие, "organization", self.организация)
		отметить_все_пункты(frappe.db.get_value("Agent Learning Session", self.занятие, "run"))

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
				"question_key",
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
		self.assertTrue(self.ответить(self.первый, "V1", "Первый, конечно")["ok"])
		итог = self.ответить(self.второй, "мимо", "Не помню, пусть будет «мимо»")
		self.assertTrue(итог["data"]["attempt_finished"])

		события = self.события()

		self.assertEqual(
			[(с.kind, с.question_key) for с in события],
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
			("V1", "Первый, конечно", 1),
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

	# --- слова ученика обязательны ---

	def test_отказ_в_ответе_ничего_не_пишет(self):
		"""Без слов ученика ответ не принят, ответ на чужой вопрос отклонён. Ни
		ответа, ни записи о нём: отказ ничего не меняет, и тот же вопрос остаётся
		открытым для ответа со словами."""
		отказы = {
			"слова: None": (self.первый, None, quiz.НУЖНЫ_СЛОВА),
			"слова: пустая строка": (self.первый, "", quiz.НУЖНЫ_СЛОВА),
			"слова: пробелы": (self.первый, "   \n\t", quiz.НУЖНЫ_СЛОВА),
			"чужой вопрос": ("S9/l-1-D1", "да", quiz.ЧУЖОЙ_ВОПРОС),
		}
		for что, (вопрос, слова, код) in отказы.items():
			with self.subTest(что):
				ответ = self.ответить(вопрос, "V1", слова)

				self.assertFalse(ответ["ok"])
				self.assertEqual(ответ["error"]["code"], код)
				self.assertFalse(frappe.db.exists("Agent Quiz Answer", {"attempt": self.попытка}))
				self.assertEqual([с.kind for с in self.события()], [ВЫДАН])

		self.assertTrue(self.ответить(self.первый, "V1", "Первый")["ok"])

	def test_длинные_слова_обрезаются_а_не_отклоняются(self):
		ответ = self.ответить(self.первый, "V1", "я" * (quiz.ДЛИНА_СЛОВ + 500))

		self.assertTrue(ответ["ok"])
		принят = next(с for с in self.события() if с.kind == ПРИНЯТ)
		self.assertEqual(len(принят.student_words), quiz.ДЛИНА_СЛОВ)

	def test_слова_на_вердикт_не_влияют(self):
		"""Судит сервер по `answer`: слова только лежат в журнале."""
		вердикт = self.ответить(self.первый, "V2", "Первый, я уверен")["data"]["verdict"]

		self.assertFalse(вердикт["correct"])

	# --- кто читает ---

	def test_журнал_читают_только_администраторы_платформы(self):
		"""В журнале ответы рядом с вердиктом — готовые эталоны. Ученик журнал не
		читает. Руководитель попытку своего сотрудника читает, а журнал по ней —
		нет. Модератор журнал читает, но не правит."""
		self.ответить(self.первый, "V1", "Первый")
		frappe.set_user("Administrator")
		события = frappe.get_all(ЖУРНАЛ, filters={"attempt": self.попытка}, pluck="name")
		событие = события[0]
		менеджер = создать_менеджера(f"qlogm-{frappe.generate_hash(length=6)}@example.com", self.организация)
		модератор = создать_куратора(f"qlogmod-{frappe.generate_hash(length=6)}@example.com", роль="Moderator")

		frappe.set_user(менеджер)
		self.assertTrue(frappe.has_permission("Agent Quiz Attempt", "read", doc=self.попытка))

		случаи = {
			"ученик": (self.ученик, False, []),
			"руководитель": (менеджер, False, []),
			"модератор": (модератор, True, sorted(события)),
		}
		for что, (кто, читает, видно) in случаи.items():
			with self.subTest(что):
				frappe.set_user(кто)
				try:
					self.assertEqual(frappe.has_permission(ЖУРНАЛ, "read"), читает)
					self.assertEqual(frappe.has_permission(ЖУРНАЛ, "read", doc=событие), читает)
					self.assertEqual(sorted(self._видно_списком()), видно)
					for право in ("write", "create", "delete"):
						self.assertFalse(frappe.has_permission(ЖУРНАЛ, право, doc=событие), право)
				finally:
					frappe.set_user("Administrator")

	def _видно_списком(self) -> list[str]:
		"""Что пользователь получает списком — как через `/api/resource`."""
		try:
			return frappe.get_list(ЖУРНАЛ, filters={"attempt": self.попытка}, pluck="name")
		except frappe.PermissionError:
			return []


class TestQuizLogSchema(UnitTestCase):
	"""Схема журнала: читает метаданные, в базу не пишет."""

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
