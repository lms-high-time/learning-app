# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Сигналы агенту — выводы сервера из истории ученика (learning-services#416)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from lms_frappe_app.agent_learning import signals
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_занятие,
	создать_организацию,
	создать_ученика,
	создать_урок,
	курс_из_релиза,
	урок_релиза,
)
from lms_frappe_app.api import student


class IntegrationTestSignals(IntegrationTestCase):
	"""Сигналы отметок целей — курс старой модели: урок из двух целей у ученика
	организации, срок курса далеко. Сигналы старта — `IntegrationTestStartSignals`."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"sig-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = frappe.db.get_value(
			"Course Chapter", frappe.db.get_value("Course Lesson", self.урок, "chapter"), "course"
		)
		frappe.get_doc(
			{
				"doctype": "Agent Lesson Directive",
				"lesson": self.урок,
				"objectives": "Понимать цикл\nУметь читать код",
				"teaching_directive": "Начать с примера",
			}
		).insert(ignore_permissions=True)
		self.организация = создать_организацию(f"Компания {суффикс}")
		добавить_в_организацию(self.ученик, self.организация)
		self.назначение = frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.организация,
				"course": self.курс,
				"deadline": add_days(nowdate(), 90),
				"mandatory": 1,
			}
		).insert(ignore_permissions=True)
		self.суффикс = суффикс
		frappe.set_user(self.ученик)

	# --- старт урока ---

	def test_одно_занятие_с_touched_ещё_не_сигнал_а_отметка_touched_делает_его(self):
		self._прошлое([("Понимать цикл", "touched")])
		занятие = создать_занятие(self.ученик, self.урок)

		ответ = student.mark_objective(занятие, 1, "touched", "начать с примера")["data"]

		self.assertEqual(
			ответ["signals"],
			[{"code": "objective_struggling", "objective": 1, "text": "Понимать цикл", "sessions": 2}],
		)

	def test_брошенное_с_отметками_не_срыв(self):
		self._прошлое([])
		self._прошлое([("Понимать цикл", "covered")])

		self.assertEqual(signals.брошены_подряд(signals.история_урока(self.ученик, self.урок, "")), [])

	def test_не_больше_трёх_по_приоритету(self):
		занятие = frappe.get_doc(
			{"doctype": "Agent Learning Session", "signals_shown": ""}
		)
		занятие.db_set = lambda *а, **к: None
		кандидаты = [
			{"code": "deadline_pace"},
			{"code": "abandoned_in_row"},
			{"code": "objective_struggling", "objective": 1},
			{"code": "objective_struggling", "objective": 2},
			{"code": "lesson_wrapup"},
		]

		отобраны = signals.отобрать(занятие, кандидаты)

		self.assertEqual(
			[с["code"] for с in отобраны],
			["lesson_wrapup", "objective_struggling", "objective_struggling"],
		)

	# --- отметки ---

	def test_все_цели_отмечены_итог_урока_один_раз(self):
		занятие = создать_занятие(self.ученик, self.урок)
		первая = student.mark_objective(занятие, 1, "covered", "Объяснил цикл")["data"]
		self.assertEqual(первая["signals"], [])

		вторая = student.mark_objective(занятие, 2, "touched", "с чтения чужого кода")["data"]
		self.assertEqual(вторая["signals"], [{"code": "lesson_wrapup", "empty_blocks": []}])

		повтор = student.mark_objective(занятие, 2, "covered", "Прочитал цикл вслух")["data"]
		self.assertEqual(повтор["signals"], [])

	def test_половина_целей_а_документ_не_начат(self):
		self._документ()
		занятие = создать_занятие(self.ученик, self.урок)

		ответ = student.mark_objective(занятие, 1, "covered", "Объяснил цикл")["data"]

		self.assertEqual(
			ответ["signals"],
			[
				{
					"code": "blocks_empty",
					"blocks": [{"artifact": f"doc-{self.суффикс}", "key": "goal", "title": "Цель"}],
				}
			],
		)

	def test_начатый_документ_не_сигнал(self):
		self._документ()
		student.update_artifact(self.курс, f"doc-{self.суффикс}", "goal", "Научиться читать циклы")
		занятие = создать_занятие(self.ученик, self.урок)

		ответ = student.mark_objective(занятие, 1, "covered", "Объяснил цикл")["data"]

		self.assertEqual(ответ["signals"], [])

	# --- данные ---

	def _прошлое(self, отметки: list[tuple[str, str]]) -> None:
		"""Прошлое занятие по уроку, брошенное с этими отметками."""
		frappe.set_user("Administrator")
		занятие = frappe.get_doc("Agent Learning Session", создать_занятие(self.ученик, self.урок))
		for цель, статус in отметки:
			занятие.append("outcomes", {"objective": цель, "status": статус})
		занятие.status = "Abandoned"
		занятие.save(ignore_permissions=True)
		frappe.set_user(self.ученик)

	def _документ(self) -> None:
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"course": self.курс,
				"slug": f"doc-{self.суффикс}",
				"title": f"Документ {self.суффикс}",
				"version": 1,
				"is_active": 1,
				"blocks": [{"block_key": "goal", "title": "Цель", "lesson": self.урок}],
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)


class IntegrationTestStartSignals(IntegrationTestCase):
	"""Сигналы старта урока курса из релиза: брошенные попытки и темп к сроку."""

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

	def брошенное(self) -> None:
		занятие = создать_занятие(self.ученик, self.урок)
		frappe.db.set_value("Agent Learning Session", занятие, "status", "Abandoned")

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
