# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Сигналы агенту — выводы сервера из истории ученика (learning-services#416)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from lms_frappe_app.agent_learning import signals
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_занятие,
	создать_организацию,
	создать_ученика,
	курс_из_релиза,
	урок_релиза,
)
from lms_frappe_app.api import student


class IntegrationTestSignalsPriority(IntegrationTestCase):
	def test_по_приоритету_и_один_раз_на_занятие(self):
		занятие = frappe.get_doc({"doctype": "Agent Learning Session", "signals_shown": ""})
		записано = {}
		занятие.db_set = lambda поле, значение, **_: записано.update({поле: значение})
		кандидаты = [{"code": "deadline_pace"}, {"code": "blocks_empty"}, {"code": "abandoned_in_row"}]

		отобраны = signals.отобрать(занятие, кандидаты)

		self.assertEqual([с["code"] for с in отобраны], ["abandoned_in_row", "blocks_empty", "deadline_pace"])
		занятие.signals_shown = записано["signals_shown"]
		self.assertEqual(signals.отобрать(занятие, кандидаты), [])


class IntegrationTestStartSignals(IntegrationTestCase):
	"""Сигналы урока курса из релиза — по прохождению: брошенные попытки, пустой
	документ, темп к сроку; после отметки пункта — пустой документ."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"sig-start-{суффикс}@example.com")
		self.курс, _ = курс_из_релиза()
		self.урок = урок_релиза(self.курс, "l-1")
		организация = создать_организацию(f"Компания {суффикс}")
		добавить_в_организацию(self.ученик, организация)
		self.назначение = frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": организация,
				"course": self.курс,
				"deadline": add_days(nowdate(), 90),
				"mandatory": 1,
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)

	def старт(self) -> dict:
		ответ = student.start_lesson(lesson=self.урок)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def брошенное(self, с_отметкой: bool = False) -> None:
		run = прохождения.прохождение(self.ученик, self.курс, "l-1")
		занятие = создать_занятие(self.ученик, self.урок, run=run.name)
		if с_отметкой:
			прохождения.отметить(
				run.name, "refute:M1", "done", "Ученик сам назвал заблуждение", занятие=занятие
			)
		frappe.db.set_value("Agent Learning Session", занятие, "status", "Abandoned")

	def цель_разобрана(self) -> None:
		"""Оба обязательных пункта единственной цели урока — `done`."""
		run = прохождения.прохождение(self.ученик, self.курс, "l-1").name
		прохождения.отметить(run, "term:T1", "done", "Назвал термин")
		прохождения.отметить(run, "l-1-D1/V1", "done", "Выбрал первый")

	def срок(self, срок: str) -> None:
		frappe.set_user("Administrator")
		self.назначение.db_set("deadline", срок)
		frappe.set_user(self.ученик)

	def test_без_истории_сигналов_нет(self):
		self.assertEqual(self.старт()["signals"], [])

	def test_брошенные_подряд_без_отметок_один_раз_на_занятие(self):
		self.брошенное()
		self.брошенное()

		self.assertEqual(self.старт()["signals"], [{"code": "abandoned_in_row", "attempts": 2}])
		# Второй вызов по тому же занятию — продолжение, а не новость.
		self.assertEqual(self.старт()["signals"], [])

	def test_брошенное_с_отметкой_пункта_рвёт_цепочку(self):
		self.брошенное()
		self.брошенное(с_отметкой=True)
		self.брошенное()

		self.assertEqual(self.старт()["signals"], [])

	def test_половина_целей_разобрана_а_документ_пуст(self):
		self.цель_разобрана()

		self.assertEqual(
			self.старт()["signals"],
			[{"code": "blocks_empty", "blocks": [{"artifact": "notebook", "key": "log", "title": "Журнал"}]}],
		)

	def test_начатый_документ_не_сигнал(self):
		self.цель_разобрана()
		student.update_artifact(self.курс, "notebook", "log", rows=[{"topic": "Первая встреча"}])

		self.assertEqual(self.старт()["signals"], [])

	def test_отметка_разобравшая_цель_сигналит_один_раз(self):
		занятие = self.старт()["session"]

		первая = student.mark_goal(занятие, "term:T1", "done", "Назвал термин")["data"]
		вторая = student.mark_goal(занятие, "l-1-D1/V1", "done", "Выбрал первый")["data"]
		повтор = student.mark_goal(занятие, "l-1-D1/V1", "done", "Выбрал первый ещё раз")["data"]

		self.assertEqual(первая["signals"], [])
		self.assertEqual(
			вторая["signals"],
			[{"code": "blocks_empty", "blocks": [{"artifact": "notebook", "key": "log", "title": "Журнал"}]}],
		)
		self.assertEqual(повтор["signals"], [])

	def test_срок_жмёт_темп_в_уроках_без_дат(self):
		self.срок(add_days(nowdate(), 2))

		self.assertEqual(
			self.старт()["signals"],
			[{"code": "deadline_pace", "lessons_left": 3, "lessons_per_week": 11, "overdue": False}],
		)

	def test_срок_прошёл(self):
		self.срок(add_days(nowdate(), -3))

		self.assertEqual(
			self.старт()["signals"],
			[{"code": "deadline_pace", "lessons_left": 3, "lessons_per_week": None, "overdue": True}],
		)
