# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import release_quiz
from lms_frappe_app.agent_learning.releases import service as релизы
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.api import admin, student
from lms_frappe_app.tests.release_sample import релиз_двух_целей
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	создать_вопрос,
	создать_домашку,
	создать_занятие,
	создать_квиз,
	создать_куратора,
	создать_организацию,
	создать_ученика,
	создать_урок,
	сдать_отчёт,
)


class IntegrationTestResetProgress(IntegrationTestCase):
	"""Сброс прогресса ученика по курсу из админки (learning-services#313)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"reset-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		вопрос = создать_вопрос("Два плюс два?", варианты=[("4", True), ("5", False)])
		создать_квиз(self.урок, [вопрос])
		self.курс = зачислить(self.ученик, self.урок)
		frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"course": self.курс,
				"slug": "summary",
				"title": "Резюме",
				"blocks": [{"block_key": "goal", "title": "Цель", "lesson": self.урок}],
			}
		).insert(ignore_permissions=True)

		# Ученик позанимался: занятие, попытка квиза, документ, заметки, репорт.
		frappe.set_user(self.ученик)
		self.занятие = создать_занятие(self.ученик, self.урок)
		student.update_artifact(self.курс, "summary", "goal", "Открыть кофейню")
		student.remember("observation", "pace", "Любит примеры", session=self.занятие)
		student.remember("fact", "role", "Владелец кофейни")
		self.репорт = student.report_issue(self.занятие, "stuck", "Непонятен пример")["data"]["report"]
		сдать_отчёт(self.занятие)
		self.попытка = student.request_quiz(self.занятие)["data"]["attempt"]
		frappe.set_user("Administrator")

	def запись(self) -> str:
		return frappe.db.get_value("LMS Enrollment", {"member": self.ученик, "course": self.курс})

	def сбросить(self, кто: str = "Administrator") -> dict:
		frappe.set_user(кто)
		ответ = admin.reset_student_progress(self.запись())
		frappe.set_user("Administrator")
		return ответ

	def test_занятия_попытки_документ_и_заметки_уходят_в_архив(self):
		итог = self.сбросить()["data"]

		self.assertEqual(
			(итог["sessions_archived"], итог["attempts_archived"], итог["artifacts_archived"], итог["notes_archived"]),
			(1, 1, 1, 1),
		)
		занятие = frappe.db.get_value(
			"Agent Learning Session", self.занятие, ["student", "archived_student", "status"], as_dict=True
		)
		self.assertIsNone(занятие.student)
		self.assertEqual(занятие.archived_student, self.ученик)
		self.assertEqual(занятие.status, "Abandoned", "открытое занятие закрыто, а не брошено задачей")
		попытка = frappe.db.get_value(
			"Agent Quiz Attempt", self.попытка, ["student", "archived_student", "status"], as_dict=True
		)
		self.assertEqual((попытка.student, попытка.archived_student, попытка.status), (None, self.ученик, "Abandoned"))
		self.assertTrue(
			frappe.db.exists("Agent Student Note", {"student": self.ученик, "note_key": "role"}),
			"факт без курса — не прогресс по курсу",
		)

	def test_сдачи_домашки_уходят_в_архив(self):
		"""Повторное прохождение начинается с чистого листа (learning-services#439)."""
		создать_домашку(self.урок)
		frappe.set_user(self.ученик)
		сдача = student.submit_homework(lesson=self.урок, answer="Сделал")["data"]["submission"]["id"]
		итог = self.сбросить()["data"]

		self.assertEqual(итог["homework_archived"], 1)
		запись = frappe.db.get_value(
			"Agent Homework Submission", сдача, ["member", "archived_student", "archived_at"], as_dict=True
		)
		self.assertIsNone(запись.member)
		self.assertEqual(запись.archived_student, self.ученик)
		self.assertIsNotNone(запись.archived_at)

		зачислить(self.ученик, self.урок)
		frappe.set_user(self.ученик)
		self.assertIsNone(student.homework(lesson=self.урок)["data"]["submission"])
		заново = student.submit_homework(lesson=self.урок, answer="Сделал заново")["data"]["submission"]
		self.assertNotEqual(заново["id"], сдача)
		self.assertEqual(заново["version"], 1)

	def test_запись_на_курс_снимается(self):
		self.сбросить()

		self.assertIsNone(self.запись())
		frappe.set_user(self.ученик)
		self.assertNotIn(self.курс, [к["id"] for к in student.list_my_courses()["data"]["courses"]])

	def test_ответы_на_репорты_ученик_видит_и_после_сброса(self):
		self.сбросить()

		frappe.set_user(self.ученик)
		репорты = student.my_reports()["data"]["reports"]

		self.assertIn(self.репорт, [р["id"] for р in репорты])

	def test_по_назначению_организации_запись_выдаётся_заново(self):
		организация = создать_организацию(f"Компания {frappe.generate_hash(length=6)}")
		добавить_в_организацию(self.ученик, организация)
		frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": организация,
				"course": self.курс,
				"mandatory": 1,
			}
		).insert(ignore_permissions=True)
		прежняя = self.запись()

		итог = self.сбросить()["data"]

		self.assertTrue(итог["enrolled_again"])
		self.assertIsNotNone(self.запись())
		self.assertNotEqual(self.запись(), прежняя, "запись новая, без прежнего прогресса")

	def test_след_сброса_в_карточке_ученика(self):
		self.сбросить()

		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{"reference_doctype": "User", "reference_name": self.ученик, "comment_type": "Info"},
			)
		)

	def test_ученик_и_чужой_куратор_сбросить_не_могут(self):
		куратор = создать_куратора(f"cc-{frappe.generate_hash(length=6)}@example.com")
		for кто in (self.ученик, куратор):
			with self.subTest(кто=кто), self.assertRaises(frappe.PermissionError):
				self.сбросить(кто)
		self.assertIsNotNone(self.запись(), "после отказа ничего не тронуто")

	def test_преподаватель_курса_сбросить_может(self):
		куратор = создать_куратора(f"in-{frappe.generate_hash(length=6)}@example.com")
		курс = frappe.get_doc("LMS Course", self.курс)
		курс.append("instructors", {"instructor": куратор})
		курс.save(ignore_permissions=True)

		self.assertTrue(self.сбросить(куратор)["ok"])

	def test_несуществующая_запись_отказ_кодом(self):
		ответ = admin.reset_student_progress("нет-такой")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], admin.ЗАПИСЬ_НЕ_НАЙДЕНА)


class IntegrationTestResetProgressRelease(IntegrationTestCase):
	"""Сброс по курсу из релиза: прохождения уроков — в архив (learning-services#504)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"reset-run-{суффикс}@example.com")
		self.ключ = f"reset-{суффикс}"
		self.курс = релизы.опубликовать(релиз_двух_целей(self.ключ), None, "Administrator")["course"]
		self.run = прохождения.прохождение(self.ученик, self.курс, "l-1")
		зачислить(self.ученик, self.run.lesson)
		прохождения.отметить(self.run.name, "term:T1", "done", "Ученик объяснил сам")
		self.попытка = release_quiz.начать(self.run, создать_занятие(self.ученик, self.run.lesson))["attempt"]

	def сбросить(self) -> dict:
		запись = frappe.db.get_value("LMS Enrollment", {"member": self.ученик, "course": self.курс})
		return admin.reset_student_progress(запись)["data"]

	def test_прохождение_и_попытка_по_релизу_уходят_в_архив(self):
		итог = self.сбросить()

		self.assertEqual((итог["runs_archived"], итог["attempts_archived"]), (1, 1))
		run = frappe.db.get_value(
			"Agent Lesson Run", self.run.name, ["student", "archived_student", "archived_at"], as_dict=True
		)
		self.assertIsNone(run.student)
		self.assertEqual(run.archived_student, self.ученик)
		self.assertIsNotNone(run.archived_at)
		self.assertEqual(
			frappe.db.get_value("Agent Quiz Attempt", self.попытка, ["student", "status"]),
			(None, "Abandoned"),
		)

	def test_после_сброса_урок_проходится_заново(self):
		"""Архивное прохождение не мешает новому и не идёт в счёт глав и сверки."""
		прежний_релиз = self.run.release
		self.сбросить()
		зачислить(self.ученик, self.run.lesson)

		заново = прохождения.прохождение(self.ученик, self.курс, "l-1")

		self.assertNotEqual(заново.name, self.run.name)
		self.assertEqual((заново.status, заново.started_at), ("not_started", None))
		self.assertEqual({п.status for п in заново.goals}, {"open"})
		[глава] = прохождения.главы(self.ученик, self.курс)
		self.assertEqual((глава["status"], глава["lessons_started"]), ("not_started", 0))

		второй = релиз_двух_целей(self.ключ)
		второй["lessons"][0]["objectives"][0]["goals"][0]["title"] = "Термин «другой пример»"
		with patch.object(frappe, "enqueue"):
			релизы.опубликовать(второй, None, "Administrator")
		self.assertEqual(прохождения.сверить_курс(self.курс), 1, "сверено только живое прохождение")
		self.assertEqual(frappe.db.get_value("Agent Lesson Run", self.run.name, "release"), прежний_релиз)

	def test_после_записи_заново_занятие_первое_и_попытки_полные(self):
		frappe.set_user(self.ученик)
		до = student.start_lesson(lesson=self.run.lesson)["data"]
		frappe.set_user("Administrator")
		self.сбросить()
		зачислить(self.ученик, self.run.lesson)

		frappe.set_user(self.ученик)
		урок = student.start_lesson(lesson=self.run.lesson)["data"]

		self.assertNotEqual(урок["session"], до["session"])
		self.assertEqual(урок["start"]["opening"], "first_in_course")
		self.assertEqual(урок["history"]["lessons"], [])
		self.assertEqual({п["status"] for ц in урок["lesson_map"] for п in ц["goals"]}, {"open"})
		if до["quiz"]["attempts_left"] is not None:
			self.assertGreater(урок["quiz"]["attempts_left"], до["quiz"]["attempts_left"])
