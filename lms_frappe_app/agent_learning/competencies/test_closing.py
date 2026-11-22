# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Закрытие урока по компетенциям (learning-services#809)."""

import json

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.agent_learning.competencies.closing import засчёты_по_записям, непроверенные_по_записям


class _Запись(dict):
	__getattr__ = dict.get


def запись(имя, компетенция, исход, уровень=0, урок="l-3", вид="Evidence", выведено=None, курс="c"):
	return _Запись(
		name=имя,
		kind=вид,
		course=курс,
		competency=компетенция,
		outcome=исход,
		level=уровень,
		lesson_key=урок,
		withdrawn=json.dumps(выведено) if выведено is not None else None,
	)


ЦЕЛИ = [{"competency": "B2", "target": 2}, {"competency": "I1", "target": 3}]


class TestНепроверенные(UnitTestCase):
	def test_что_засчитывается(self):
		for случай, записи, осталось in (
			("ничего нет", [], ["B2", "I1"]),
			# В занятиях урока засчитывается проверка с любым исходом, кроме «недостаточно».
			(
				"затруднение и пропуск в уроке",
				[запись("e1", "B2", "difficulty", 2), запись("e2", "I1", "skipped")],
				[],
			),
			("недостаточно данных — не проверка", [запись("e1", "B2", "insufficient")], ["B2", "I1"]),
			("подтверждено ниже цели в уроке", [запись("e1", "I1", "confirmed", 1)], ["B2"]),
			# Из другого урока — только подтверждение не ниже цели.
			(
				"подтверждено не ниже цели в другом уроке",
				[запись("e1", "I1", "confirmed", 3, урок="l-1")],
				["B2"],
			),
			(
				"подтверждено ниже цели в другом уроке",
				[запись("e1", "I1", "confirmed", 2, урок="l-1")],
				["B2", "I1"],
			),
			("затруднение в другом уроке", [запись("e1", "B2", "difficulty", 2, урок="l-1")], ["B2", "I1"]),
			# Проверка в уроке была — засчитывается, даже если пересмотр потом вывел подтверждение.
			(
				"подтверждение из этого урока выведено пересмотром",
				[
					запись("e1", "B2", "confirmed", 2),
					запись("r1", "B2", None, 1, вид="Revision", выведено=["e1"]),
				],
				["I1"],
			),
			(
				"подтверждение из другого урока выведено пересмотром",
				[
					запись("e1", "I1", "confirmed", 3, урок="l-1"),
					запись("r1", "I1", None, 1, урок="l-1", вид="Revision", выведено=["e1"]),
				],
				["B2", "I1"],
			),
			# Журнал компетенции каталога — по всем курсам (learning-services#826): записи
			# другого курса засчитываются только подтверждением не ниже цели.
			(
				"подтверждено не ниже цели в другом курсе",
				[запись("e1", "I1", "confirmed", 3, курс="other")],
				["B2"],
			),
			(
				"затруднение и пропуск в одноимённом уроке другого курса",
				[
					запись("e1", "B2", "difficulty", 2, курс="other"),
					запись("e2", "I1", "skipped", курс="other"),
				],
				["B2", "I1"],
			),
			(
				"подтверждение другого курса выведено пересмотром в этом",
				[
					запись("e1", "I1", "confirmed", 3, курс="other"),
					запись("r1", "I1", None, 1, вид="Revision", выведено=["e1"]),
				],
				["B2", "I1"],
			),
		):
			with self.subTest(случай):
				self.assertEqual(
					[ц["competency"] for ц in непроверенные_по_записям(записи, "c", "l-3", ЦЕЛИ)], осталось
				)


class TestЗасчёты(UnitTestCase):
	def test_засчёт_только_из_другого_курса(self):
		for случай, записи, засчитано in (
			("ничего нет", [], []),
			("подтверждение этого курса", [запись("e1", "I1", "confirmed", 3, урок="l-1")], []),
			("пропуск в уроке", [запись("e1", "I1", "skipped")], []),
			(
				"другой курс — последнее из закрывающих",
				[
					запись("e1", "I1", "confirmed", 4, курс="a"),
					запись("e2", "I1", "confirmed", 2, курс="b"),
					запись("e3", "I1", "confirmed", 3, курс="b"),
				],
				[{"competency": "I1", "target": 3, "evidence": "e3", "course": "b", "level": 3}],
			),
			(
				"цель закрывает и запись этого курса",
				[запись("e1", "I1", "confirmed", 3, курс="a"), запись("e2", "I1", "difficulty", 3)],
				[],
			),
			(
				"подтверждение другого курса ниже цели",
				[запись("e1", "B2", "confirmed", 1, курс="a")],
				[],
			),
		):
			with self.subTest(случай):
				self.assertEqual(засчёты_по_записям(записи, "c", "l-3", ЦЕЛИ), засчитано)


class IntegrationTestЗакрытиеУрока(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from lms_frappe_app.tests.release_sample import пример_релиза, с_компетенциями
		from lms_frappe_app.tests.sample_data import курс_из_релиза, политика_по_умолчанию

		frappe.set_user("Administrator")
		политика_по_умолчанию()
		cls.курс, cls.релиз = курс_из_релиза(
			релиз=с_компетенциями(пример_релиза(f"closing-{frappe.generate_hash(length=6)}"))
		)

	def setUp(self):
		from lms_frappe_app.api import student
		from lms_frappe_app.tests.sample_data import зачислить_на_курс, создать_ученика, урок_релиза

		self.addCleanup(frappe.set_user, "Administrator")
		self.ученик = создать_ученика(f"closing-{frappe.generate_hash(length=6)}@example.com")
		зачислить_на_курс(self.ученик, self.курс)
		frappe.set_user(self.ученик)
		self.student = student
		старт = student.start_lesson(lesson=урок_релиза(self.курс, "l-3"))["data"]
		self.занятие = старт["session"]
		self.старт = старт

	def свидетельство(self, компетенция: str, исход: str, ключ: str, **поля) -> dict:
		ответ = self.student.record_evidence(
			self.занятие, компетенция, исход, ключ, task="Задание урока", **поля
		)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def test_урок_с_картой_без_квиза(self):
		self.assertEqual(self.старт["quiz"]["required"], False)
		self.assertIsNone(self.старт["quiz"]["attempts_left"])
		ответ = self.student.request_quiz(self.занятие)
		self.assertEqual(ответ["error"]["code"], "quiz_not_used", ответ)

	def test_отказ_со_списком_затем_закрытие(self):
		from lms_frappe_app.tests.sample_data import отметить_все_пункты

		run = frappe.db.get_value("Agent Learning Session", self.занятие, "run")
		отметить_все_пункты(run, self.занятие)
		отказ = self.student.complete_lesson(self.занятие)
		self.assertEqual(отказ["error"]["code"], "competencies_open", отказ)
		self.assertEqual(
			отказ["error"]["competencies"],
			[
				{"competency": "B2", "target": 2, "title": "Цена ошибочного действия"},
				{"competency": "I1", "target": 3, "title": "Обоснование устройства"},
			],
		)
		# Проверено — B2 отказом ученика, I1 затруднением; освоения не требуется.
		пропуск = self.свидетельство("B2", "skipped", "c-1")
		self.свидетельство(
			"I1",
			"difficulty",
			"c-2",
			level=3,
			student_action="Собрал устройство без цены ошибки",
			assessment="Нет связи с ценой ошибки",
			task_form="work",
			hints=False,
			corrected=False,
			indirect=False,
		)
		# Записанное после отказа в закрытии — с пометкой для аудита.
		self.assertEqual(frappe.db.get_value("Agent Competency Event", пропуск["event"], "after_refusal"), 1)
		закрыт = self.student.complete_lesson(self.занятие)
		self.assertTrue(закрыт["ok"], закрыт)
		self.assertEqual(закрыт["data"]["session_status"], "Completed")

	def test_шаг_компетенции_вместо_закрытия(self):
		"""Пункты закрыты, компетенции нет — `next_step` ведёт к свидетельствам, а не к закрытию."""
		from lms_frappe_app.tests.sample_data import отметить_все_пункты, урок_релиза

		run = frappe.db.get_value("Agent Learning Session", self.занятие, "run")
		отметить_все_пункты(run, self.занятие)
		шаг = self.student.start_lesson(lesson=урок_релиза(self.курс, "l-3"))["data"]["next_step"]
		self.assertEqual(шаг["kind"], "competencies")
		self.assertEqual([ц["competency"] for ц in шаг["competencies"]], ["B2", "I1"])


class IntegrationTestПопыткаПриКарте(IntegrationTestCase):
	def test_публикация_карты_аннулирует_открытую_попытку(self):
		"""Урок получил цели по компетенциям — открытая попытка аннулирована: сданная закрыла бы
		урок мимо проверки компетенций."""
		from lms_frappe_app.agent_learning.releases import service
		from lms_frappe_app.api import student
		from lms_frappe_app.tests.release_sample import пример_релиза, с_компетенциями
		from lms_frappe_app.tests.sample_data import (
			зачислить_на_курс,
			курс_из_релиза,
			отметить_все_пункты,
			политика_по_умолчанию,
			создать_ученика,
			урок_релиза,
		)

		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		политика_по_умолчанию()
		ключ = f"closing-quiz-{frappe.generate_hash(length=6)}"
		курс, _ = курс_из_релиза(релиз=пример_релиза(ключ))
		ученик = создать_ученика(f"{ключ}@example.com")
		зачислить_на_курс(ученик, курс)
		frappe.set_user(ученик)
		занятие = student.start_lesson(lesson=урок_релиза(курс, "l-1"))["data"]["session"]
		отметить_все_пункты(frappe.db.get_value("Agent Learning Session", занятие, "run"), занятие)
		попытка = student.request_quiz(занятие)["data"]["attempt"]

		frappe.set_user("Administrator")
		service.опубликовать(с_компетенциями(пример_релиза(ключ)), None, "Administrator")

		self.assertEqual(
			frappe.db.get_value("Agent Quiz Attempt", попытка, ["status", "cancel_reason"]),
			("Cancelled", "quiz_not_used"),
		)
