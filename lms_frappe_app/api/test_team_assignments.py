# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Назначения руководителем и письма о назначении и сроке (#365)."""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from lms_frappe_app.agent_learning import notices
from lms_frappe_app.api import authoring, team
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	привязать_урок,
	создать_домашку,
	создать_курс,
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
	создать_урок,
)


class IntegrationTestTeamAssignments(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.письма = patch("frappe.sendmail").start()
		patch.object(notices, "почта_есть", return_value=True).start()
		self.addCleanup(patch.stopall)
		суффикс = frappe.generate_hash(length=6)
		self.компания = создать_организацию(f"Компания {суффикс}")
		self.руководитель = создать_менеджера(f"as-m-{суффикс}@example.com", self.компания)
		self.сотрудник = создать_ученика(f"as-e-{суффикс}@example.com")
		self.коллега = создать_ученика(f"as-c-{суффикс}@example.com")
		for человек in (self.сотрудник, self.коллега):
			добавить_в_организацию(человек, self.компания)
		self.курс = создать_курс(f"Курс {суффикс}")
		frappe.db.set_value("LMS Course", self.курс, "published", 1)

	def от_имени(self, кто: str, метод, **аргументы):
		frappe.set_user(кто)
		try:
			return метод(**аргументы)
		finally:
			frappe.set_user("Administrator")

	def назначить(self, **аргументы) -> str:
		ответ = self.от_имени(
			self.руководитель, team.assign_course, organization=self.компания, course=self.курс, **аргументы
		)
		assert ответ["ok"], ответ
		return ответ["data"]["id"]

	def письма_о(self, вид: str) -> set[str]:
		return set(frappe.get_all("Allocation Notice", filters={"kind": вид}, pluck="user"))

	def зачислен(self, кто: str) -> bool:
		return bool(frappe.db.exists("LMS Enrollment", {"member": кто, "course": self.курс}))

	def test_курс_на_всю_команду_зачисляет_и_пишет_каждому_один_раз(self):
		назначение = self.назначить(deadline="2030-01-01", mandatory=1)

		self.assertTrue(self.зачислен(self.сотрудник) and self.зачислен(self.коллега))
		self.assertTrue({self.сотрудник, self.коллега} <= self.письма_о("assigned"))
		отправлено = self.письма.call_count

		self.от_имени(self.руководитель, team.update_allocation, allocation=назначение, deadline="2030-02-01")

		self.assertEqual(self.письма.call_count, отправлено)

	def test_выбранным_и_дописанному_потом(self):
		назначение = self.назначить(members=[self.сотрудник])
		self.assertFalse(self.зачислен(self.коллега))

		self.от_имени(
			self.руководитель, team.update_allocation, allocation=назначение, members=[self.сотрудник, self.коллега]
		)

		self.assertTrue(self.зачислен(self.коллега))
		self.assertIn(self.коллега, self.письма_о("assigned"))

	def test_новичок_получает_письмо_о_назначенном_раньше(self):
		self.назначить()
		новичок = создать_ученика(f"as-n-{frappe.generate_hash(length=6)}@example.com")

		добавить_в_организацию(новичок, self.компания)

		self.assertIn(новичок, self.письма_о("assigned"))

	def test_без_исходящей_почты_назначение_сохраняется_письмо_ждёт(self):
		with patch.object(notices, "почта_есть", return_value=False):
			назначение = self.назначить()

		self.assertTrue(frappe.db.exists("Course Allocation", назначение))
		self.assertEqual(self.письма_о("assigned"), set())

	def test_курс_взятый_самим_без_писем(self):
		frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.компания,
				"course": self.курс,
				"audience": "Selected Members",
				"members": [{"user": self.сотрудник}],
				"chosen_by_member": 1,
			}
		).insert(ignore_permissions=True)

		self.assertEqual(self.письма.call_count, 0)

	def test_напоминание_о_сроке_непрошедшим_один_раз(self):
		self.назначить(deadline=add_days(nowdate(), 2))
		frappe.db.set_value(
			"LMS Enrollment", {"member": self.коллега, "course": self.курс}, "progress", 100
		)

		notices.напомнить_о_сроках()
		notices.напомнить_о_сроках()

		# Руководитель — тоже участник: курс всей команде назначен и ему.
		напоминания = frappe.get_all("Allocation Notice", filters={"kind": "deadline"}, pluck="user")
		self.assertEqual(sorted(напоминания), sorted([self.сотрудник, self.руководитель]))

	def test_далёкий_и_прошедший_срок_без_напоминания(self):
		self.назначить(deadline=add_days(nowdate(), 30))
		self.назначить(deadline=add_days(nowdate(), -1))

		notices.напомнить_о_сроках()

		self.assertEqual(self.письма_о("deadline"), set())

	def test_снятие_оставляет_зачисление(self):
		назначение = self.назначить()

		self.от_имени(self.руководитель, team.remove_allocation, allocation=назначение)

		self.assertFalse(frappe.db.exists("Course Allocation", назначение))
		self.assertTrue(self.зачислен(self.сотрудник))

	def test_участник_не_назначает_а_закрытый_курс_не_назначить(self):
		ответ = self.от_имени(self.сотрудник, team.assign_course, organization=self.компания, course=self.курс)
		self.assertIn(ответ["error"]["code"], ("team_not_available", "not_allowed"))

		организация = frappe.get_doc("Learning Organization", self.компания)
		организация.append("allowed_courses", {"course": создать_курс(f"Другой {frappe.generate_hash(length=6)}")})
		организация.save(ignore_permissions=True)
		ответ = self.от_имени(
			self.руководитель, team.assign_course, organization=self.компания, course=self.курс
		)
		self.assertEqual(ответ["error"]["code"], "course_not_allowed")

	def test_перечень_назначений_и_доступных_курсов(self):
		self.назначить(members=[self.сотрудник], deadline="2030-01-01")

		ответ = self.от_имени(self.руководитель, team.allocations, organization=self.компания)["data"]

		self.assertEqual(ответ["allocations"][0]["members"], [self.сотрудник])
		self.assertFalse(ответ["allocations"][0]["whole_team"])
		self.assertIn(self.курс, {к["id"] for к in ответ["courses"]})


class IntegrationTestAllocationHomeworkDue(IntegrationTestCase):
	"""Сроки домашек в назначении курса (learning-services#452)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		patch.object(notices, "почта_есть", return_value=False).start()
		self.addCleanup(patch.stopall)
		с = frappe.generate_hash(length=6)
		self.компания = создать_организацию(f"Компания {с}")
		self.руководитель = создать_менеджера(f"hd-m-{с}@example.com", self.компания)
		self.первый = создать_урок(f"Урок 1 {с}")
		глава = frappe.db.get_value("Course Lesson", self.первый, "chapter")
		self.курс = frappe.db.get_value("Course Chapter", глава, "course")
		frappe.db.set_value("LMS Course", self.курс, "published", 1)
		self.без_задания = self._урок(глава, f"Урок 2 {с}")
		self.третий = self._урок(глава, f"Урок 3 {с}")
		# Задание третьего урока заводится раньше первого: порядок в ответе —
		# по урокам курса, а не по созданию заданий.
		self.задание_3 = создать_домашку(self.третий, title="Третье", due_mode="absolute", due_date="2030-05-01")
		self.задание_1 = создать_домашку(self.первый, title="Первое", due_mode="relative", due_days=5)
		ответ = self.от_имени(team.assign_course, organization=self.компания, course=self.курс)
		self.assertTrue(ответ["ok"], ответ)
		self.назначение = ответ["data"]["id"]

	def _урок(self, глава: str, название: str) -> str:
		урок = frappe.get_doc({"doctype": "Course Lesson", "title": название, "chapter": глава}).insert(
			ignore_permissions=True
		)
		привязать_урок(глава, урок.name)
		return урок.name

	def от_имени(self, метод, кто=None, **аргументы):
		frappe.set_user(кто or self.руководитель)
		try:
			return метод(**аргументы)
		finally:
			frappe.set_user("Administrator")

	def задать(self, homework_due, **аргументы) -> dict:
		return self.от_имени(
			team.update_allocation, allocation=self.назначение, homework_due=homework_due, **аргументы
		)

	def сроки(self) -> list[dict]:
		ответ = self.от_имени(team.allocations, organization=self.компания)["data"]
		[назначение] = [н for н in ответ["allocations"] if н["id"] == self.назначение]
		return назначение["homework"]

	def код(self, ответ) -> str:
		self.assertFalse(ответ["ok"], ответ)
		return ответ["error"]["code"]

	def test_перечень_заданий_по_порядку_уроков(self):
		сроки = self.сроки()
		self.assertEqual([с["homework"] for с in сроки], [self.задание_1.name, self.задание_3.name])
		первое = сроки[0]
		self.assertEqual(
			(первое["lesson"], первое["title"], первое["lesson_title"]),
			(self.первый, "Первое", frappe.db.get_value("Course Lesson", self.первый, "title")),
		)
		self.assertEqual(первое["author_due"], {"mode": "relative", "days": 5, "date": None})
		self.assertIsNone(первое["due"])

	def test_правило_назначения_и_снятие(self):
		ответ = self.задать(
			[
				{"homework": self.задание_1.name, "due_mode": "absolute", "due_date": "2030-02-01", "due_days": 9},
				{"homework": self.задание_3.name, "due_mode": "relative", "due_days": 2},
			]
		)
		self.assertTrue(ответ["ok"], ответ)
		первое, третье = self.сроки()
		self.assertEqual(первое["due"], {"mode": "absolute", "days": None, "date": "2030-02-01"})
		self.assertEqual(третье["due"], {"mode": "relative", "days": 2, "date": None})

		# Не передан — не трогать.
		self.assertTrue(self.от_имени(team.update_allocation, allocation=self.назначение, mandatory=0)["ok"])
		self.assertIsNotNone(self.сроки()[0]["due"])
		self.assertTrue(self.задать("null")["ok"])
		self.assertIsNotNone(self.сроки()[0]["due"])

		# Список заменяет таблицу целиком; пустой снимает все правила.
		self.assertTrue(self.задать(json.dumps([{"homework": self.задание_3.name, "due_mode": "relative", "due_days": 1}]))["ok"])
		self.assertEqual([с["due"] is None for с in self.сроки()], [True, False])
		self.assertTrue(self.задать([])["ok"])
		self.assertEqual([с["due"] for с in self.сроки()], [None, None])

	def test_неверные_сроки(self):
		for неверное in (
			[{"homework": self.задание_1.name, "due_mode": "none"}],
			[{"homework": self.задание_1.name, "due_mode": "relative"}],
			[{"homework": self.задание_1.name, "due_mode": "relative", "due_days": 0}],
			[{"homework": self.задание_1.name, "due_mode": "absolute"}],
			[
				{"homework": self.задание_1.name, "due_mode": "relative", "due_days": 1},
				{"homework": self.задание_1.name, "due_mode": "relative", "due_days": 2},
			],
			"{не json",
			[self.задание_1.name],
			{"homework": self.задание_1.name},
		):
			self.assertEqual(self.код(self.задать(неверное)), "invalid_due", неверное)
		self.assertEqual([с["due"] for с in self.сроки()], [None, None])

	def test_задание_чужого_курса(self):
		чужое = создать_домашку(создать_урок(f"Чужой {frappe.generate_hash(length=6)}"))
		for задание in (чужое.name, "нет-такого"):
			ответ = self.задать([{"homework": задание, "due_mode": "relative", "due_days": 1}])
			self.assertEqual(self.код(ответ), "homework_not_in_course")

	def test_после_переноса_урока_срок_назначения_правится(self):
		self.задать([{"homework": self.задание_1.name, "due_mode": "relative", "due_days": 1}])
		другой = создать_урок(f"Другой {frappe.generate_hash(length=6)}")
		куда = frappe.db.get_value("Course Lesson", другой, "chapter")
		модератор = создать_куратора(f"hd-mod-{frappe.generate_hash(length=6)}@example.com", роль="Moderator")
		self.assertTrue(self.от_имени(authoring.move_lesson, кто=модератор, lesson=self.первый, chapter=куда)["ok"])

		ответ = self.от_имени(team.update_allocation, allocation=self.назначение, deadline="2030-12-31")

		self.assertTrue(ответ["ok"], ответ)
		self.assertFalse(frappe.db.exists("Course Allocation Homework Due", {"parent": self.назначение}))

	def test_удаление_задания_и_урока_снимает_правила(self):
		self.задать(
			[
				{"homework": self.задание_1.name, "due_mode": "relative", "due_days": 1},
				{"homework": self.задание_3.name, "due_mode": "relative", "due_days": 1},
			]
		)
		модератор = создать_куратора(f"hd-mod-{frappe.generate_hash(length=6)}@example.com", роль="Moderator")
		self.assertTrue(self.от_имени(authoring.remove_homework, кто=модератор, lesson=self.первый)["ok"])
		self.assertTrue(self.от_имени(authoring.remove_lesson, кто=модератор, lesson=self.третий)["ok"])
		self.assertFalse(frappe.db.exists("Course Allocation Homework Due", {"parent": self.назначение}))
