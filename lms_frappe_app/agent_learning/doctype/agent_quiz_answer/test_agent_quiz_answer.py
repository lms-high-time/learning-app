# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Запись ответа — основание выставленного зачёта.

`Why:` итог попытки не хранится отдельно, он считается по этим записям: сколько
верных из скольких. Поэтому важны две вещи — что запись отвечает вердикту, и
что завести её может только сервер. Ответ, записанный мимо метода, означал бы
зачёт, за которым не стоит ни одной сверки с эталоном.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import release_quiz
from lms_frappe_app.agent_learning.releases import service as релизы
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.tests.release_sample import релиз_двух_целей
from lms_frappe_app.tests.sample_data import (
	зачислить,
	политика_по_умолчанию,
	создать_занятие,
	создать_ученика,
)

ВОПРОС = "S1/l-1-D1"


class IntegrationTestAgentQuizAnswer(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"answer-{суффикс}@example.com")
		курс = релизы.опубликовать(
			релиз_двух_целей(f"answer-{суффикс}", вопросов=1), None, "Administrator"
		)["course"]
		run = прохождения.прохождение(self.ученик, курс, "l-1")
		зачислить(self.ученик, run.lesson)
		self.попытка = release_quiz.начать(run, создать_занятие(self.ученик, run.lesson))["attempt"]

	def ответить(self, ответ: str) -> None:
		release_quiz.ответить(self.попытка, ВОПРОС, ответ, "слова ученика")

	def запись(self):
		return frappe.get_doc("Agent Quiz Answer", {"attempt": self.попытка, "question_key": ВОПРОС})

	def test_верный_ответ_приносит_балл(self):
		self.ответить("V1")

		запись = self.запись()
		self.assertTrue(запись.is_correct)
		self.assertEqual((запись.marks, запись.marks_out_of), (1, 1))
		self.assertEqual(запись.objective_key, "l-1-D1")

	def test_неверный_ответ_обнуляет_балл_но_не_вес(self):
		"""`Why:` доля считается как «верных из скольких», и потерянный вес
		вопроса поднял бы итог: ошибка стала бы выгоднее пропуска."""
		self.ответить("V2")

		запись = self.запись()
		self.assertFalse(запись.is_correct)
		self.assertEqual((запись.marks, запись.marks_out_of), (0, 1))

	def test_ответ_хранится_обрезанным_до_предела_поля(self):
		"""Агент волен прислать в ответ хоть всё занятие; запись — про вердикт."""
		self.ответить("я" * 900)

		self.assertEqual(len(self.запись().answer), 500)

	def test_ответ_записывается_только_сервером(self):
		"""Ученик не заводит себе ответов: у роли нет права на запись.

		`Why:` вердикт ставит сервер, сверив ответ с эталоном. Запись, заведённая
		ученику прямым REST, дала бы зачёт без единой сверки — и серверный квиз,
		несущее решение всей схемы, перестал бы что-либо значить.
		"""
		frappe.set_user(self.ученик)

		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "Agent Quiz Answer",
					"attempt": self.попытка,
					"question_key": ВОПРОС,
					"answer": "V1",
					"is_correct": 1,
					"marks": 1,
					"marks_out_of": 1,
				}
			).insert()
