# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import release_quiz
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.tests.sample_data import (
	зачислить_на_курс,
	курс_из_релиза,
	добавить_в_организацию,
	создать_занятие,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
	создать_урок,
)
from lms_frappe_app.api import manager


class IntegrationTestManagerAPI(IntegrationTestCase):
	"""Отчётность менеджера: только своя организация, только результаты."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)

		self.компания_а = создать_организацию(f"Компания А {суффикс}")
		self.компания_б = создать_организацию(f"Компания Б {суффикс}")

		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = frappe.db.get_value(
			"Course Chapter", frappe.db.get_value("Course Lesson", self.урок, "chapter"), "course"
		)

		self.ученик_а = создать_ученика(f"sa-{суффикс}@example.com")
		self.ученик_б = создать_ученика(f"sb-{суффикс}@example.com")
		добавить_в_организацию(self.ученик_а, self.компания_а)
		добавить_в_организацию(self.ученик_б, self.компания_б)

		for организация in (self.компания_а, self.компания_б):
			frappe.get_doc(
				{
					"doctype": "Course Allocation",
					"organization": организация,
					"course": self.курс,
					"deadline": "2026-12-31",
					"mandatory": 1,
				}
			).insert(ignore_permissions=True)

		self.менеджер = создать_менеджера(f"mg-{суффикс}@example.com", self.компания_а)

	def отчёт(self, **аргументы):
		frappe.set_user(self.менеджер)
		return manager.org_report(**аргументы)["data"]["rows"]

	def test_ученики_чужой_организации_в_отчёт_не_попадают(self):
		строки = self.отчёт()
		self.assertIn(self.ученик_а, {с["user"] for с in строки})
		self.assertNotIn(self.ученик_б, {с["user"] for с in строки})

	def test_отчёт_несёт_дедлайн_статус_и_долю_пройденного(self):
		строка = next(с for с in self.отчёт() if с["user"] == self.ученик_а)

		self.assertEqual(строка["status"], "not_started")
		self.assertEqual(строка["progress"], 0.0)
		self.assertEqual(str(строка["deadline"]), "2026-12-31")
		self.assertTrue(строка["mandatory"])
		self.assertFalse(строка["overdue"])

	def test_отчёт_показывает_с_какой_попытки_сдан_урок(self):
		"""Зачёт — за 100% без лимита: «сдан» не отличает понявшего от перебравшего (#353)."""
		занятие = создать_занятие(self.ученик_а, self.урок)
		for номер, сдана in ((1, 0), (2, 1)):
			frappe.get_doc(
				{
					"doctype": "Agent Quiz Attempt",
					"session": занятие,
					"student": self.ученик_а,
					"lesson": self.урок,
					"course": self.курс,
					"attempt_number": номер,
					"status": "Passed" if сдана else "Failed",
					"passed": сдана,
				}
			).insert(ignore_permissions=True)

		строка = next(с for с in self.отчёт() if с["user"] == self.ученик_а)

		self.assertEqual(строка["quiz"], {"passed": 1, "first_try": 0})

	def test_отчёт_показывает_заполненность_документа(self):
		"""Урок засчитывает квиз — документ виден отдельно (learning-services#296)."""
		frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"course": self.курс,
				"slug": "summary",
				"title": "Резюме",
				"blocks": [
					{"block_key": "goal", "title": "Цель"},
					{"block_key": "sponsor", "title": "Спонсор"},
				],
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Agent Student Artifact",
				"student": self.ученик_а,
				"course": self.курс,
				"artifact": "summary",
				"organization": self.компания_а,
				"blocks": [
					{"block_key": "goal", "content": "Открыть кофейню"},
					{"block_key": "sponsor", "content": "  "},
					{"block_key": "removed", "content": "из старой схемы"},
				],
			}
		).insert(ignore_permissions=True)

		строка = next(с for с in self.отчёт() if с["user"] == self.ученик_а)

		self.assertEqual(строка["document"], {"blocks_total": 2, "blocks_filled": 1})

	def test_курс_без_документа_даёт_нули(self):
		строка = next(с for с in self.отчёт() if с["user"] == self.ученик_а)

		self.assertEqual(строка["document"], {"blocks_total": 0, "blocks_filled": 0})

	def test_фильтр_по_статусу_отсекает_остальных(self):
		self.assertEqual(self.отчёт(status="completed"), [])
		self.assertTrue(self.отчёт(status="not_started"))

	def test_фильтр_по_курсу(self):
		self.assertTrue(self.отчёт(course=self.курс))
		self.assertEqual(self.отчёт(course="несуществующий-курс"), [])

	def test_менеджер_без_организаций_видит_пустой_отчёт(self):
		# Роль сама по себе не открывает ничего — нужна связка с членством.
		одиночка = создать_ученика(f"lone-{frappe.generate_hash(length=6)}@example.com")
		frappe.get_doc("User", одиночка).add_roles("Organization Manager")

		frappe.set_user(одиночка)

		self.assertEqual(manager.org_report()["data"]["rows"], [])

	def test_подробности_ученика_без_текстов_ответов(self):
		# Отчёт про результат, а не про содержание диалога с агентом.
		занятие = создать_занятие(self.ученик_а, self.урок)
		frappe.get_doc("Agent Learning Session", занятие).записать_событие(
			"Directive Issued", "ученик спросил про вложенные циклы"
		)

		frappe.set_user(self.менеджер)
		данные = manager.student_detail(self.ученик_а)["data"]

		self.assertEqual(данные["user"], self.ученик_а)
		self.assertTrue(данные["sessions"])
		self.assertNotIn("вложенные циклы", json.dumps(данные, ensure_ascii=False, default=str))

	def test_подробности_показывают_покрытие_целей(self):
		"""Руководителю нужно знать, какие темы разобраны, а какие нет.

		Это тот же учебный результат, что и зачёт, просто мельче: в отличие
		от заметок об ученике, он про результат, а не про разговор. Покрытие —
		цели урока из прохождения, без пунктов и свидетельств
		(learning-services#506); у занятия курса без релиза его нет.
		"""
		курс, _ = курс_из_релиза()
		frappe.get_doc(
			{"doctype": "Course Allocation", "organization": self.компания_а, "course": курс}
		).insert(ignore_permissions=True)
		run = прохождения.прохождение(self.ученик_а, курс, "l-1")
		занятие = создать_занятие(self.ученик_а, run.lesson, run=run.name)
		прохождения.отметить(run.name, "term:T1", "done", "Свидетельство агента", занятие=занятие)
		создать_занятие(self.ученик_а, self.урок)

		frappe.set_user(self.менеджер)
		сессии = {с["course"]: с for с in manager.student_detail(self.ученик_а)["data"]["sessions"]}

		self.assertEqual(
			сессии[курс]["objectives"],
			[{"key": "l-1-D1", "text": "Цель урока «Урок первый»", "status": "touched"}],
		)
		self.assertEqual(сессии[self.курс]["objectives"], [])
		self.assertNotIn("Свидетельство агента", json.dumps(сессии, ensure_ascii=False, default=str))

	def test_подробности_несут_итог_попытки_квиза_из_релиза(self):
		"""Попытка — урок, номер, статус, доля, зачёт и время; ни вопросов, ни ответов."""
		курс, _ = курс_из_релиза()
		frappe.get_doc(
			{"doctype": "Course Allocation", "organization": self.компания_а, "course": курс}
		).insert(ignore_permissions=True)
		зачислить_на_курс(self.ученик_а, курс)
		run = прохождения.прохождение(self.ученик_а, курс, "l-1")
		начало = release_quiz.начать(run, создать_занятие(self.ученик_а, run.lesson, run=run.name))
		release_quiz.ответить(начало["attempt"], начало["question"]["id"], "V1", "слова ученика")

		frappe.set_user(self.менеджер)
		данные = manager.student_detail(self.ученик_а)["data"]

		[попытка] = данные["quiz_attempts"]
		закончена = frappe.db.get_value("Agent Quiz Attempt", начало["attempt"], "finished_at")
		self.assertEqual(
			попытка,
			{
				"lesson": run.lesson,
				"attempt": 1,
				"status": "Passed",
				"score": 1.0,
				"passed": True,
				"finished_at": закончена.isoformat(),
			},
		)
		выдано = json.dumps(данные, ensure_ascii=False, default=str)
		for закрытое in (начало["question"]["id"], "слова ученика", "V1"):
			self.assertNotIn(закрытое, выдано)


class IntegrationTestManagerRole(IntegrationTestCase):
	"""Роль руководителя должна работать сама по себе.

	`Why:` до этого отчёт работал лишь потому, что тестовый руководитель
	попутно получал роль ученика. Пользователь с одной ролью
	`Organization Manager` данных бы не увидел, и обнаружилось бы это на
	первом реальном руководителе.
	"""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.компания = создать_организацию(f"Компания {суффикс}")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = frappe.db.get_value(
			"Course Chapter", frappe.db.get_value("Course Lesson", self.урок, "chapter"), "course"
		)
		self.ученик = создать_ученика(f"emp-{суффикс}@example.com")
		добавить_в_организацию(self.ученик, self.компания)
		frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.компания,
				"course": self.курс,
				"deadline": "2026-12-31",
			}
		).insert(ignore_permissions=True)

		# Руководитель без роли ученика: только Organization Manager и членство.
		self.руководитель = f"mgr-only-{суффикс}@example.com"
		пользователь = frappe.get_doc(
			{
				"doctype": "User",
				"email": self.руководитель,
				"first_name": "Руководитель",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		пользователь.add_roles("Organization Manager")
		# Frappe Learning выдаёт роль ученика каждому новому пользователю
		# автоматически — снимаем её явно, иначе проверка ничего не проверит:
		# отчёт мог бы работать на правах ученика, как и было раньше.
		пользователь.remove_roles("LMS Student")
		frappe.get_doc(
			{
				"doctype": "Organization Membership",
				"user": self.руководитель,
				"organization": self.компания,
				"role": "Manager",
			}
		).insert(ignore_permissions=True)

	def test_руководитель_без_роли_ученика_видит_отчёт_и_подробности(self):
		frappe.set_user(self.руководитель)
		self.assertNotIn("LMS Student", frappe.get_roles(self.руководитель))

		строки = manager.org_report()["data"]["rows"]

		self.assertIn(self.ученик, {строка["user"] for строка in строки})

		ответ = manager.student_detail(self.ученик)

		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(ответ["data"]["user"], self.ученик)
