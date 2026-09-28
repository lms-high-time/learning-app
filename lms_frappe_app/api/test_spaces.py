# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Пространства: документ принадлежит личному пространству или организации.

Доступ к документу решает его пространство, а не то, что руководитель и
ученик где-то состоят вместе (learning-services#341). Проверяется тем же
путём, каким ходят настоящие запросы: методами API, списком и прямым
обращением к записи.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.constants import СОБЫТИЕ_ДИРЕКТИВА_ВЫДАНА
from lms_frappe_app.api import manager, student
from lms_frappe_app.patches.v0_1 import document_spaces
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	создать_вопрос,
	создать_квиз,
	создать_менеджера,
	создать_организацию,
	создать_урок,
	создать_ученика,
)


def завести_схему(курс: str) -> None:
	frappe.get_doc(
		{
			"doctype": "Agent Course Artifact",
			"course": курс,
			"slug": "summary",
			"title": "Резюме проекта",
			"blocks": [{"block_key": "goal", "title": "Цель"}],
		}
	).insert(ignore_permissions=True)


def назначить(организация: str, курс: str) -> None:
	frappe.get_doc(
		{"doctype": "Course Allocation", "organization": организация, "course": курс}
	).insert(ignore_permissions=True)


def написать(ученик: str, курс: str, текст: str) -> str:
	"""Пишет блок документа от имени ученика, возвращает имя документа."""
	frappe.set_user(ученик)
	student.update_artifact(курс, "summary", "goal", текст)
	frappe.set_user("Administrator")
	return frappe.db.get_value(
		"Agent Student Artifact",
		{"student": ученик, "course": курс, "artifact": "summary"},
		order_by="modified desc",
	)


def выйти(user: str, организация: str) -> None:
	членство = frappe.get_doc("Organization Membership", {"user": user, "organization": организация})
	членство.status = "Left"
	членство.save(ignore_permissions=True)


class IntegrationTestDocumentSpace(IntegrationTestCase):
	"""В какое пространство попадает документ и занятие."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.компания = создать_организацию(f"Компания {суффикс}")
		self.ученик = создать_ученика(f"space-{суффикс}@example.com")
		добавить_в_организацию(self.ученик, self.компания)
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = frappe.db.get_value(
			"Course Chapter", frappe.db.get_value("Course Lesson", self.урок, "chapter"), "course"
		)
		завести_схему(self.курс)

	def test_курс_от_организации_пишется_в_её_пространство(self):
		назначить(self.компания, self.курс)

		документ = написать(self.ученик, self.курс, "Кофейня")

		self.assertEqual(
			frappe.db.get_value("Agent Student Artifact", документ, "organization"), self.компания
		)

	def test_самозапись_остаётся_личной(self):
		зачислить(self.ученик, self.урок)

		документ = написать(self.ученик, self.курс, "Для себя")

		self.assertFalse(frappe.db.get_value("Agent Student Artifact", документ, "organization"))

	def test_занятие_помнит_пространство(self):
		назначить(self.компания, self.курс)
		frappe.set_user(self.ученик)

		занятие = student.start_lesson(self.урок)["data"]["session"]

		self.assertEqual(
			frappe.db.get_value("Agent Learning Session", занятие, "organization"), self.компания
		)

	def test_начатый_документ_не_переезжает_после_назначения(self):
		"""Пока пространство не выбирают явно, работа продолжается там, где начата."""
		зачислить(self.ученик, self.урок)
		личный = написать(self.ученик, self.курс, "Начал сам")

		назначить(self.компания, self.курс)
		продолжение = написать(self.ученик, self.курс, "Продолжил")

		self.assertEqual(продолжение, личный)
		self.assertEqual(
			frappe.db.count(
				"Agent Student Artifact", {"student": self.ученик, "course": self.курс}
			),
			1,
		)

	def test_приостановленная_организация_пространство_не_даёт(self):
		назначить(self.компания, self.курс)
		frappe.db.set_value("Learning Organization", self.компания, "status", "Suspended")
		зачислить(self.ученик, self.урок)

		документ = написать(self.ученик, self.курс, "Сам по себе")

		self.assertFalse(frappe.db.get_value("Agent Student Artifact", документ, "organization"))


class IntegrationTestDocumentAccess(IntegrationTestCase):
	"""Кто × какой документ × настройка видимости."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.икс = создать_организацию(f"Икс {суффикс}")
		self.игрек = создать_организацию(f"Игрек {суффикс}")

		self.сотрудник = создать_ученика(f"acc-e-{суффикс}@example.com")
		self.коллега = создать_ученика(f"acc-c-{суффикс}@example.com")
		for человек in (self.сотрудник, self.коллега):
			добавить_в_организацию(человек, self.икс)
		добавить_в_организацию(self.сотрудник, self.игрек)
		self.руководитель_икс = создать_менеджера(f"acc-mx-{суффикс}@example.com", self.икс)
		self.руководитель_игрек = создать_менеджера(f"acc-my-{суффикс}@example.com", self.игрек)

		# Три курса — три пространства одного и того же человека.
		self.курсы = {}
		for пространство in ("икс", "игрек", "личное"):
			урок = создать_урок(f"Урок {пространство} {суффикс}")
			курс = зачислить(self.сотрудник, урок)
			завести_схему(курс)
			self.курсы[пространство] = курс
		назначить(self.икс, self.курсы["икс"])
		назначить(self.игрек, self.курсы["игрек"])

		self.документы = {
			пространство: написать(self.сотрудник, курс, f"Текст {пространство}")
			for пространство, курс in self.курсы.items()
		}

	def читает(self, user: str, пространство: str) -> bool:
		документ = self.документы[пространство]
		frappe.set_user(user)
		try:
			напрямую = frappe.has_permission("Agent Student Artifact", "read", doc=документ)
			списком = bool(
				frappe.get_list("Agent Student Artifact", filters={"name": документ}, limit=1)
			)
		finally:
			frappe.set_user("Administrator")
		# Список и прямое обращение обязаны сходиться: расхождение — это
		# утечка через тот канал, который проверили хуже.
		self.assertEqual(напрямую, списком, f"{user} / {пространство}")
		return напрямую

	def видимость(self, организация: str, значение: str) -> None:
		frappe.db.set_value("Learning Organization", организация, "artifact_visibility", значение)

	def test_документы_размечены_по_пространствам(self):
		self.assertEqual(
			frappe.db.get_value("Agent Student Artifact", self.документы["икс"], "organization"),
			self.икс,
		)
		self.assertEqual(
			frappe.db.get_value("Agent Student Artifact", self.документы["игрек"], "organization"),
			self.игрек,
		)

	def test_руководитель_читает_документы_только_своего_пространства(self):
		self.assertTrue(self.читает(self.руководитель_икс, "икс"))
		self.assertFalse(self.читает(self.руководитель_икс, "игрек"))
		self.assertFalse(self.читает(self.руководитель_икс, "личное"))
		self.assertTrue(self.читает(self.руководитель_игрек, "игрек"))
		self.assertFalse(self.читает(self.руководитель_игрек, "икс"))

	def test_автор_читает_все_свои_но_не_правит_напрямую(self):
		for пространство in self.документы:
			self.assertTrue(self.читает(self.сотрудник, пространство))
		frappe.set_user(self.сотрудник)
		self.assertFalse(
			frappe.has_permission("Agent Student Artifact", "write", doc=self.документы["икс"])
		)

	def test_участник_читает_документы_коллег_только_если_открыто_всем(self):
		self.assertFalse(self.читает(self.коллега, "икс"))

		self.видимость(self.икс, "All Members")

		self.assertTrue(self.читает(self.коллега, "икс"))
		self.assertFalse(self.читает(self.коллега, "личное"))
		self.assertFalse(self.читает(self.коллега, "игрек"))

	def test_ушедший_автор_читает_свой_документ(self):
		выйти(self.сотрудник, self.икс)

		self.assertTrue(self.читает(self.сотрудник, "икс"))

	def test_документ_ушедшего_остаётся_у_организации(self):
		выйти(self.сотрудник, self.икс)

		self.assertTrue(self.читает(self.руководитель_икс, "икс"))

	def test_ушедший_руководитель_теряет_доступ_сразу(self):
		выйти(self.руководитель_икс, self.икс)

		self.assertFalse(self.читает(self.руководитель_икс, "икс"))

	def test_ушедший_участник_не_читает_документы_коллег(self):
		self.видимость(self.икс, "All Members")
		выйти(self.коллега, self.икс)

		self.assertFalse(self.читает(self.коллега, "икс"))

	def test_отчёт_считает_документ_пространства_организации(self):
		"""Личный документ по тому же курсу в отчёт организации не попадает."""
		назначить(self.икс, self.курсы["личное"])
		frappe.set_user(self.руководитель_икс)

		строки = manager.org_report()["data"]["rows"]

		по_курсам = {
			с["course"]: с["document"]["blocks_filled"]
			for с in строки
			if с["user"] == self.сотрудник
		}
		self.assertEqual(по_курсам[self.курсы["икс"]], 1)
		self.assertEqual(по_курсам[self.курсы["личное"]], 0)


class IntegrationTestDocumentSpacesPatch(IntegrationTestCase):
	"""Перенос документов, заведённых до пространств."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.компания = создать_организацию(f"Компания {суффикс}")
		self.ученик = создать_ученика(f"patch-{суффикс}@example.com")
		добавить_в_организацию(self.ученик, self.компания)
		self.курс_компании = зачислить(self.ученик, создать_урок(f"Урок к {суффикс}"))
		self.свой_курс = зачислить(self.ученик, создать_урок(f"Урок с {суффикс}"))
		назначить(self.компания, self.курс_компании)

	def старый_документ(self, курс: str) -> str:
		return frappe.get_doc(
			{
				"doctype": "Agent Student Artifact",
				"student": self.ученик,
				"course": курс,
				"artifact": "summary",
			}
		).insert(ignore_permissions=True).name

	def test_документ_назначенного_курса_уходит_организации(self):
		компании = self.старый_документ(self.курс_компании)
		свой = self.старый_документ(self.свой_курс)

		document_spaces.execute()

		self.assertEqual(
			frappe.db.get_value("Agent Student Artifact", компании, "organization"), self.компания
		)
		self.assertFalse(frappe.db.get_value("Agent Student Artifact", свой, "organization"))


class IntegrationTestSessionAccess(IntegrationTestCase):
	"""Занятия, события и попытки видны руководителю по пространству (#344)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.икс = создать_организацию(f"Икс {суффикс}")
		self.игрек = создать_организацию(f"Игрек {суффикс}")
		self.сотрудник = создать_ученика(f"ses-e-{суффикс}@example.com")
		добавить_в_организацию(self.сотрудник, self.икс)
		добавить_в_организацию(self.сотрудник, self.игрек)
		self.руководитель = создать_менеджера(f"ses-m-{суффикс}@example.com", self.икс)
		урок = создать_урок(f"Урок {суффикс}")
		зачислить(self.сотрудник, урок)
		квиз = создать_квиз(урок, [создать_вопрос("Столица?", варианты=[("Москва", True), ("Тула", False)])])

		self.занятия, self.попытки, self.события = {}, {}, {}
		for пространство, организация in (("икс", self.икс), ("игрек", self.игрек), ("личное", None)):
			занятие = frappe.get_doc(
				{
					"doctype": "Agent Learning Session",
					"student": self.сотрудник,
					"lesson": урок,
					"organization": организация,
				}
			).insert(ignore_permissions=True)
			self.занятия[пространство] = занятие.name
			self.попытки[пространство] = (
				frappe.get_doc(
					{
						"doctype": "Agent Quiz Attempt",
						"session": занятие.name,
						"student": self.сотрудник,
						"lesson": урок,
						"quiz": квиз,
						"attempt_number": 1,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
			self.события[пространство] = занятие.записать_событие(СОБЫТИЕ_ДИРЕКТИВА_ВЫДАНА, "тест").name

	def видит(self, doctype: str, имя: str) -> bool:
		frappe.set_user(self.руководитель)
		try:
			напрямую = frappe.has_permission(doctype, "read", doc=имя)
			списком = bool(frappe.get_list(doctype, filters={"name": имя}, limit=1))
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(напрямую, списком, f"{doctype} {имя}")
		return напрямую

	def test_занятия_события_и_попытки_только_своего_пространства(self):
		for doctype, записи in (
			("Agent Learning Session", self.занятия),
			("Agent Quiz Attempt", self.попытки),
			("Agent Session Event", self.события),
		):
			with self.subTest(doctype):
				self.assertTrue(self.видит(doctype, записи["икс"]))
				self.assertFalse(self.видит(doctype, записи["игрек"]))
				self.assertFalse(self.видит(doctype, записи["личное"]))

	def test_подробности_по_сотруднику_без_чужих_пространств(self):
		frappe.set_user(self.руководитель)

		ответ = manager.student_detail(self.сотрудник)["data"]

		self.assertEqual(len(ответ["sessions"]), 1)
		self.assertEqual(len(ответ["quiz_attempts"]), 1)
