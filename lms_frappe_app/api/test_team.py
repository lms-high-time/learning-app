# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""«Команда»: кто видит участников, документы команды и отчёт (#355).

Страница — окно в документы пространства организации, поэтому и правила те
же: руководителю — всегда, участнику — если открыто всем, прочим — ничего.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import permissions
from lms_frappe_app.api import manager, review, student, team
from lms_frappe_app.tests.sample_data import (
	раздел,
	схема_документа,
	добавить_в_организацию,
	создать_домашку,
	создать_занятие,
	зачислить,
	создать_менеджера,
	создать_организацию,
	создать_урок,
	создать_ученика,
)


class IntegrationTestTeam(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.компания = создать_организацию(f"Компания {суффикс}")
		self.другая = создать_организацию(f"Другая {суффикс}")
		self.сотрудник = создать_ученика(f"team-e-{суффикс}@example.com")
		self.коллега = создать_ученика(f"team-c-{суффикс}@example.com")
		self.посторонний = создать_ученика(f"team-o-{суффикс}@example.com")
		for человек in (self.сотрудник, self.коллега):
			добавить_в_организацию(человек, self.компания)
		добавить_в_организацию(self.сотрудник, self.другая)
		self.руководитель = создать_менеджера(f"team-m-{суффикс}@example.com", self.компания)

		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(self.сотрудник, self.урок)
		схема_документа(
			self.курс, "summary", "Резюме проекта", [раздел("goal", "Цель"), раздел("sponsor", "Спонсор")]
		)
		for организация in (self.компания, self.другая):
			frappe.get_doc(
				{"doctype": "Course Allocation", "organization": организация, "course": self.курс}
			).insert(ignore_permissions=True)

		self.написать(self.сотрудник, "Кофейня для компании", self.компания)
		self.написать(self.сотрудник, "Своё дело", "personal")
		self.написать(self.сотрудник, "Для другой", self.другая)
		self.написать(self.коллега, "Склад", self.компания)

	def написать(self, кто: str, текст: str, space: str) -> None:
		frappe.set_user(кто)
		ответ = student.update_artifact(self.курс, "summary", "goal", fields={"goal": текст}, space=space)
		frappe.set_user("Administrator")
		assert ответ["ok"], ответ

	def от_имени(self, кто: str, метод, **аргументы):
		frappe.set_user(кто)
		try:
			return метод(**аргументы)
		finally:
			frappe.set_user("Administrator")

	def документы(self, кто: str) -> dict:
		return self.от_имени(
			кто, team.team_documents, organization=self.компания, course=self.курс, artifact="summary"
		)

	def видимость(self, значение: str) -> None:
		frappe.db.set_value("Learning Organization", self.компания, "artifact_visibility", значение)

	def уйти(self, кто: str):
		"""Уход из компании; возвращает подменённую отправку писем."""
		членство = frappe.get_doc("Organization Membership", {"user": кто, "organization": self.компания})
		членство.status = "Left"
		with patch("frappe.sendmail") as письма:
			членство.save(ignore_permissions=True)
		return письма

	def test_руководитель_видит_команду_документы_и_отчёт(self):
		"""Руководителю — участники, курсы, сравнение документов пространства
		организации и отчёт одной организации. Курс не организации — отказ,
		последним: он зачисляет сотрудника на ещё один курс."""
		with self.subTest("участники, курсы и отчёт"):
			ответ = self.от_имени(self.руководитель, team.team, organization=self.компания)["data"]

			self.assertTrue(ответ["can_see_report"])
			self.assertEqual(
				{у["user"] for у in ответ["members"]}, {self.сотрудник, self.коллега, self.руководитель}
			)
			self.assertEqual([к["id"] for к in ответ["courses"]], [self.курс])
			self.assertEqual(ответ["courses"][0]["documents"][0]["artifact"], "summary")

		with self.subTest("сравнение берёт только документы пространства организации"):
			ответ = self.документы(self.руководитель)["data"]

			цель = next(б for б in ответ["blocks"] if б["key"] == "goal")
			тексты = {з["user"]: з["markdown"] for з in цель["entries"]}
			self.assertEqual(
				тексты, {self.сотрудник: "- goal: Кофейня для компании", self.коллега: "- goal: Склад"}
			)
			спонсор = next(б for б in ответ["blocks"] if б["key"] == "sponsor")
			self.assertFalse(any(з["filled"] for з in спонсор["entries"]))

		with self.subTest("отчёт одной организации"):
			строки = self.от_имени(self.руководитель, manager.org_report, organization=self.компания)["data"][
				"rows"
			]
			чужие = self.от_имени(self.руководитель, manager.org_report, organization=self.другая)["data"]["rows"]

			self.assertEqual({с["organization"] for с in строки}, {self.компания})
			self.assertEqual(чужие, [])

		чужой = зачислить(self.сотрудник, создать_урок(f"Чужой {frappe.generate_hash(length=6)}"))
		with self.subTest("курс не организации"):
			ответ = self.от_имени(
				self.руководитель,
				team.team_documents,
				organization=self.компания,
				course=чужой,
				artifact="summary",
			)

			self.assertEqual(ответ["error"]["code"], "course_not_in_organization")

	def test_участник_посторонний_и_ушедший(self):
		"""Участник видит команду, только если открыто всем; посторонний и
		ушедший — нет, и при открытой видимости. Документ ушедшего руководителю
		больше не виден, сам ушедший остаётся в списке с пометкой (#585)."""
		with self.subTest("участник: закрыто"):
			отказ = self.от_имени(self.коллега, team.team, organization=self.компания)
			self.assertEqual(отказ["error"]["code"], "team_not_available")

		прежняя = frappe.db.get_value("Learning Organization", self.компания, "artifact_visibility")
		self.видимость("All Members")
		try:
			with self.subTest("участник: открыто всем"):
				ответ = self.от_имени(self.коллега, team.team, organization=self.компания)["data"]
				self.assertFalse(ответ["can_see_report"])
				self.assertTrue(self.документы(self.коллега)["ok"])

			self.уйти(self.коллега)
			for кто in (self.посторонний, self.коллега):
				with self.subTest("посторонний и ушедший не видят", кто=кто):
					self.assertEqual(self.документы(кто)["error"]["code"], "team_not_available")
		finally:
			self.видимость(прежняя)

		with self.subTest("документ ушедшего команде не виден, сам он — с пометкой"):
			авторы = {а["user"] for а in self.документы(self.руководитель)["data"]["authors"]}
			команда = self.от_имени(self.руководитель, team.team, organization=self.компания)["data"]
			участники = {у["user"]: у for у in команда["members"]}

			self.assertEqual(авторы, {self.сотрудник})
			self.assertTrue(участники[self.коллега]["left"])

	def test_работа_ушедшего_команде_не_видна_до_возвращения(self):
		"""Команда видит документы, сдачи и занятия только действующих
		участников: у ушедшего они остаются в базе, но команде не видны, а
		вернулся — видны снова. Его личная работа не трогается (#585)."""
		создать_домашку(self.урок)
		frappe.set_user(self.коллега)
		сдача = student.submit_homework(lesson=self.урок, answer="сделал", space=self.компания)
		frappe.set_user("Administrator")
		self.assertTrue(сдача["ok"], сдача)
		сдача = сдача["data"]["submission"]["id"]
		self.написать(self.коллега, "Своё у коллеги", "personal")
		занятие = frappe.get_doc("Agent Learning Session", создать_занятие(self.коллега, self.урок))
		frappe.db.set_value("Agent Learning Session", занятие.name, "organization", self.компания)
		занятие.reload()
		документ = frappe.get_doc(
			"Agent Student Artifact", {"student": self.коллега, "organization": self.компания}
		)

		def видно() -> dict[str, bool]:
			очередь = self.от_имени(self.руководитель, review.queue)["data"]["items"]
			авторы = {а["user"] for а in self.документы(self.руководитель)["data"]["authors"]}
			занятия = frappe.db.sql(
				"select name from `tabAgent Learning Session` where name = %s and "
				+ permissions.условие_занятия(self.руководитель),
				занятие.name,
			)
			return {
				"документ в сравнении": self.коллега in авторы,
				"документ напрямую": permissions.доступен_артефакт(документ, "read", self.руководитель),
				"сдача в очереди": сдача in {п["id"] for п in очередь},
				"занятие напрямую": permissions.доступно_занятие(занятие, "read", self.руководитель),
				"занятие списком": bool(занятия),
			}

		self.assertEqual(set(видно().values()), {True}, "предусловие: работа участника команде видна")

		self.уйти(self.коллега)

		with self.subTest("ушедший — не видно"):
			self.assertEqual(видно(), dict.fromkeys(видно(), False))
		with self.subTest("в базе всё на месте, личное не тронуто"):
			self.assertEqual(
				frappe.db.get_value("Agent Student Artifact", документ.name, "student"), self.коллега
			)
			self.assertEqual(frappe.db.get_value("Agent Homework Submission", сдача, "member"), self.коллега)
			self.assertTrue(
				frappe.db.exists(
					"Agent Student Artifact", {"student": self.коллега, "organization": ("is", "not set")}
				)
			)

		членство = frappe.get_doc(
			"Organization Membership", {"user": self.коллега, "organization": self.компания}
		)
		членство.status = "Active"
		членство.save(ignore_permissions=True)
		with self.subTest("вернулся — видно снова"):
			self.assertEqual(set(видно().values()), {True})

	def test_удалённое_членство_как_уход(self):
		"""Членство удалили в Desk — работа бывшего участника команде не видна."""
		frappe.delete_doc(
			"Organization Membership",
			frappe.db.get_value(
				"Organization Membership", {"user": self.коллега, "organization": self.компания}
			),
			ignore_permissions=True,
		)

		авторы = {а["user"] for а in self.документы(self.руководитель)["data"]["authors"]}
		self.assertNotIn(self.коллега, авторы)
		документ = frappe.get_doc(
			"Agent Student Artifact", {"student": self.коллега, "organization": self.компания}
		)
		self.assertFalse(permissions.доступен_артефакт(документ, "read", self.руководитель))
