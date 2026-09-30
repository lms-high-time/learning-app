# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Запись ответа — основание выставленного зачёта.

`Why:` итог попытки не хранится отдельно, он считается по этим записям: сколько
набрано из скольких. Поэтому важны две вещи — что баллы в записи отвечают
вердикту, и что завести её может только сервер. Ответ, записанный мимо метода,
означал бы зачёт, за которым не стоит ни одной сверки с эталоном.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.quiz import начать_попытку, принять_ответ
from lms_frappe_app.tests.sample_data import (
	зачислить,
	политика_по_умолчанию,
	создать_вопрос,
	создать_занятие,
	создать_квиз,
	создать_ученика,
	создать_урок,
)


class IntegrationTestAgentQuizAnswer(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"answer-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.вопрос = создать_вопрос(
			f"Столица? {суффикс}", варианты=[("Москва", True), ("Тверь", False)]
		)
		создать_квиз(self.урок, [self.вопрос], баллов_за_вопрос=5)
		зачислить(self.ученик, self.урок)
		self.попытка = начать_попытку(создать_занятие(self.ученик, self.урок))["attempt"]

	def запись(self):
		return frappe.get_doc("Agent Quiz Answer", {"attempt": self.попытка, "question": self.вопрос})

	def test_верный_ответ_приносит_вес_вопроса(self):
		принять_ответ(self.попытка, self.вопрос, "1", "слова ученика")

		запись = self.запись()
		self.assertTrue(запись.is_correct)
		self.assertEqual(запись.marks, 5)
		self.assertEqual(запись.marks_out_of, 5)

	def test_неверный_ответ_обнуляет_баллы_но_не_вес(self):
		"""`Why:` доля считается как «набрано из скольких», и потерянный вес
		вопроса поднял бы итог: ошибка стала бы выгоднее пропуска."""
		принять_ответ(self.попытка, self.вопрос, "2", "слова ученика")

		запись = self.запись()
		self.assertFalse(запись.is_correct)
		self.assertEqual(запись.marks, 0)
		self.assertEqual(запись.marks_out_of, 5)

	def test_ответ_хранится_обрезанным_до_предела_поля(self):
		"""Агент волен прислать в ответ хоть всё занятие; запись — про вердикт."""
		принять_ответ(self.попытка, self.вопрос, "я" * 900, "слова ученика")

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
					"question": self.вопрос,
					"answer": "1",
					"is_correct": 1,
					"marks": 5,
					"marks_out_of": 5,
				}
			).insert()
