# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.access import (
	НЕ_ЗАЧИСЛЕН,
	ОРГАНИЗАЦИЯ_ПРИОСТАНОВЛЕНА,
	доступен_курс,
	курсы_ученика,
	политика_квиза_для_курса,
)
from lms_frappe_app.tests.sample_data import (
	политика_по_умолчанию,
	добавить_в_организацию,
	создать_курс,
	создать_организацию,
	создать_ученика,
)


class IntegrationTestAccess(IntegrationTestCase):
	"""Доступ там, где счастливый путь кончается."""

	def setUp(self):
		политика_по_умолчанию()

	def заново(self) -> None:
		"""Свои курс и ученик — у теста или у строки таблицы."""
		self.курс = создать_курс(f"Курс {frappe.generate_hash(length=6)}")
		self.ученик = создать_ученика(f"uch-{frappe.generate_hash(length=6)}@example.com")

	def организация(self, **поля):
		return создать_организацию(f"Компания {frappe.generate_hash(length=6)}", **поля)

	def назначить(self, организация, **поля):
		return frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": организация,
				"course": self.курс,
				**поля,
			}
		).insert(ignore_permissions=True)

	def записать_самостоятельно(self):
		frappe.get_doc(
			{
				"doctype": "LMS Enrollment",
				"member": self.ученик,
				"course": self.курс,
				"member_type": "Student",
			}
		).insert(ignore_permissions=True)

	def мой_курс(self):
		return next((к for к in курсы_ученика(self.ученик) if к["course"] == self.курс), None)

	# --- курсы ученика ---

	def test_курсы_ученика(self):
		"""Частный ученик видит свои курсы: источник списка — зачисление, иначе
		самозаписавшийся не увидел бы ничего — назначений у него нет вовсе.
		Назначение добавляет дедлайн и обязательность; прошедший дедлайн — просрочка;
		из двух назначений — ближайший дедлайн; действующее назначение перекрывает
		приостановленное. У каждого случая свои курс и ученик."""
		with self.subTest("ученик без организации видит свои курсы"):
			self.заново()
			self.записать_самостоятельно()

			курс = self.мой_курс()

			self.assertIsNotNone(курс)
			self.assertIsNone(курс["organization"])
			self.assertIsNone(курс["deadline"])
			self.assertFalse(курс["mandatory"])

		with self.subTest("назначение добавляет дедлайн и обязательность"):
			self.заново()
			организация = self.организация()
			добавить_в_организацию(self.ученик, организация)
			self.назначить(организация, deadline="2026-12-31", mandatory=1)

			курс = self.мой_курс()

			self.assertEqual(str(курс["deadline"]), "2026-12-31")
			self.assertTrue(курс["mandatory"])
			self.assertFalse(курс["overdue"])

		with self.subTest("прошедший дедлайн помечается просрочкой"):
			self.заново()
			организация = self.организация()
			добавить_в_организацию(self.ученик, организация)
			self.назначить(организация, deadline="2020-01-01")

			self.assertTrue(self.мой_курс()["overdue"])

		with self.subTest("ближайший дедлайн из двух назначений"):
			self.заново()
			первая = self.организация()
			вторая = self.организация()
			добавить_в_организацию(self.ученик, первая)
			добавить_в_организацию(self.ученик, вторая)
			self.назначить(первая, deadline="2026-12-31")
			self.назначить(вторая, deadline="2026-06-30")

			self.assertEqual(str(self.мой_курс()["deadline"]), "2026-06-30")

		with self.subTest("действующее назначение перекрывает приостановленное"):
			self.заново()
			приостановленная = self.организация()
			действующая = self.организация()
			добавить_в_организацию(self.ученик, приостановленная)
			добавить_в_организацию(self.ученик, действующая)
			self.назначить(приостановленная)
			self.назначить(действующая, deadline="2026-12-31")
			frappe.db.set_value("Learning Organization", приостановленная, "status", "Suspended")

			курс = self.мой_курс()

			self.assertIsNotNone(курс)
			self.assertEqual(курс["organization"], действующая)

	# --- политика квиза ---

	def test_политика_квиза_из_назначений(self):
		"""Попытки и пауза у всех одни — платформы; различается только
		обязательность (#353): квиз обязателен, если требует хоть одна из двух
		организаций; приостановленная организация на политику не влияет — квиз
		обязателен по настройкам платформы. У каждого случая свои курс и ученик."""
		случаи = {
			"квиз обязателен, если требует хоть одна из двух": ((("No", False), ("Yes", False)), None),
			"приостановленная организация не влияет на политику": (
				(("No", True),),
				"квиз обязателен по настройкам платформы",
			),
		}
		for что, (организации, пояснение) in случаи.items():
			with self.subTest(что):
				self.заново()
				for требование, приостановить in организации:
					организация = self.организация(quiz_required=требование)
					добавить_в_организацию(self.ученик, организация)
					self.назначить(организация)
					if приостановить:
						frappe.db.set_value("Learning Organization", организация, "status", "Suspended")

				политика = политика_квиза_для_курса(self.ученик, self.курс)

				self.assertTrue(политика["quiz_required"], пояснение)
				self.assertEqual(политика["max_attempts"], 3)

	# --- доступ ---

	def test_курс_приостановленной_организации_скрывается(self):
		"""Зачисление переживает приостановку — иначе потеряется прогресс.

		Прежняя редакция проверяла только наличие записи, которую ни одна
		ветка кода и не трогает: такой assert не мог покраснеть.
		"""
		self.заново()
		организация = self.организация()
		добавить_в_организацию(self.ученик, организация)
		self.назначить(организация)
		frappe.db.set_value("Learning Organization", организация, "status", "Suspended")

		self.assertIsNone(self.мой_курс(), "курс приостановленной организации виден")

		можно, причина = доступен_курс(self.ученик, self.курс)
		self.assertFalse(можно)
		self.assertEqual(причина, ОРГАНИЗАЦИЯ_ПРИОСТАНОВЛЕНА)

		frappe.db.set_value("Learning Organization", организация, "status", "Active")
		self.assertIsNotNone(self.мой_курс(), "доступ не вернулся после возобновления")

	def test_незачисленный_ученик_получает_внятный_код(self):
		self.заново()
		можно, причина = доступен_курс(self.ученик, self.курс)
		self.assertFalse(можно)
		self.assertEqual(причина, НЕ_ЗАЧИСЛЕН)
