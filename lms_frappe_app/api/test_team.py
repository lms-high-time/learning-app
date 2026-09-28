# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""«Команда»: кто видит участников, документы команды и отчёт (#355).

Страница — окно в документы пространства организации, поэтому и правила те
же: руководителю — всегда, участнику — если открыто всем, прочим — ничего.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import manager, student, team
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
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

		self.курс = зачислить(self.сотрудник, создать_урок(f"Урок {суффикс}"))
		frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"course": self.курс,
				"slug": "summary",
				"title": "Резюме проекта",
				"blocks": [
					{"block_key": "goal", "title": "Цель"},
					{"block_key": "sponsor", "title": "Спонсор"},
				],
			}
		).insert(ignore_permissions=True)
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
		ответ = student.update_artifact(self.курс, "summary", "goal", текст, space=space)
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

	def test_руководитель_видит_участников_курсы_и_отчёт(self):
		ответ = self.от_имени(self.руководитель, team.team, organization=self.компания)["data"]

		self.assertTrue(ответ["can_see_report"])
		self.assertEqual(
			{у["user"] for у in ответ["members"]}, {self.сотрудник, self.коллега, self.руководитель}
		)
		self.assertEqual([к["id"] for к in ответ["courses"]], [self.курс])
		self.assertEqual(ответ["courses"][0]["documents"][0]["artifact"], "summary")

	def test_сравнение_берёт_только_документы_пространства_организации(self):
		ответ = self.документы(self.руководитель)["data"]

		цель = next(б for б in ответ["blocks"] if б["key"] == "goal")
		тексты = {з["user"]: з["content"] for з in цель["entries"]}
		self.assertEqual(
			тексты, {self.сотрудник: "Кофейня для компании", self.коллега: "Склад"}
		)
		спонсор = next(б for б in ответ["blocks"] if б["key"] == "sponsor")
		self.assertFalse(any(з["filled"] for з in спонсор["entries"]))

	def test_участник_видит_команду_только_если_открыто_всем(self):
		отказ = self.от_имени(self.коллега, team.team, organization=self.компания)
		self.assertEqual(отказ["error"]["code"], "team_not_available")

		self.видимость("All Members")

		ответ = self.от_имени(self.коллега, team.team, organization=self.компания)["data"]
		self.assertFalse(ответ["can_see_report"])
		self.assertTrue(self.документы(self.коллега)["ok"])

	def test_посторонний_и_ушедший_не_видят(self):
		self.видимость("All Members")
		членство = frappe.get_doc(
			"Organization Membership", {"user": self.коллега, "organization": self.компания}
		)
		членство.status = "Left"
		членство.save(ignore_permissions=True)

		for кто in (self.посторонний, self.коллега):
			with self.subTest(кто):
				self.assertEqual(self.документы(кто)["error"]["code"], "team_not_available")

	def test_документ_ушедшего_остаётся_с_пометкой(self):
		членство = frappe.get_doc(
			"Organization Membership", {"user": self.коллега, "organization": self.компания}
		)
		членство.status = "Left"
		членство.save(ignore_permissions=True)

		авторы = {а["user"]: а for а in self.документы(self.руководитель)["data"]["authors"]}

		self.assertTrue(авторы[self.коллега]["left"])
		self.assertFalse(авторы[self.сотрудник]["left"])

	def test_курс_не_организации_отказ(self):
		чужой = зачислить(self.сотрудник, создать_урок(f"Чужой {frappe.generate_hash(length=6)}"))

		ответ = self.от_имени(
			self.руководитель, team.team_documents, organization=self.компания, course=чужой, artifact="summary"
		)

		self.assertEqual(ответ["error"]["code"], "course_not_in_organization")

	def test_отчёт_одной_организации(self):
		строки = self.от_имени(self.руководитель, manager.org_report, organization=self.компания)["data"]["rows"]
		чужие = self.от_имени(self.руководитель, manager.org_report, organization=self.другая)["data"]["rows"]

		self.assertEqual({с["organization"] for с in строки}, {self.компания})
		self.assertEqual(чужие, [])
