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

from lms_frappe_app.agent_learning import spaces
from lms_frappe_app.agent_learning.constants import СОБЫТИЕ_ДИРЕКТИВА_ВЫДАНА
from lms_frappe_app.agent_learning.doctype.agent_learning_session.agent_learning_session import курс_урока
from lms_frappe_app.api import manager, student
from lms_frappe_app.patches.v0_1 import document_spaces
from lms_frappe_app.tests.sample_data import (
	раздел,
	схема_документа,
	добавить_в_организацию,
	зачислить,
	курс_из_релиза,
	создать_курс,
	создать_менеджера,
	создать_организацию,
	создать_урок,
	создать_ученика,
	создать_занятие,
	урок_релиза,
)


def завести_схему(курс: str) -> None:
	схема_документа(курс, "summary", "Резюме проекта", [раздел("goal", "Цель")])


def назначить(организация: str, курс: str) -> None:
	frappe.get_doc(
		{"doctype": "Course Allocation", "organization": организация, "course": курс}
	).insert(ignore_permissions=True)


def написать(ученик: str, курс: str, текст: str, space: str | None = None) -> str:
	"""Пишет блок документа от имени ученика, возвращает имя документа."""
	frappe.set_user(ученик)
	ответ = student.update_artifact(курс, "summary", "goal", fields={"goal": текст}, space=space)
	frappe.set_user("Administrator")
	assert ответ["ok"], ответ
	return frappe.db.get_value(
		"Agent Student Artifact",
		{"student": ученик, "course": курс, "artifact": "summary"},
		order_by="modified desc",
	)


def курс_релиза(организация: str) -> tuple[str, str]:
	"""Курс из релиза со схемой `summary`, назначенный организацией, и его первый урок:
	`start_lesson` открывает только курс из релиза (learning-services#506)."""
	курс, _ = курс_из_релиза()
	завести_схему(курс)
	документ = frappe.get_doc("Learning Organization", организация)
	if документ.allowed_courses:
		документ.append("allowed_courses", {"course": курс})
		документ.save(ignore_permissions=True)
	назначить(организация, курс)
	return курс, урок_релиза(курс, "l-1")


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

	def test_занятие_помнит_пространство(self):
		_, урок = курс_релиза(self.компания)
		frappe.set_user(self.ученик)

		занятие = student.start_lesson(урок)["data"]["session"]

		self.assertEqual(
			frappe.db.get_value("Agent Learning Session", занятие, "organization"), self.компания
		)

	def test_без_пространства_документ_выбранного(self):
		"""Курс назначен после личной работы — компании достаётся свой документ (#346)."""
		урок = создать_урок(f"Урок {frappe.generate_hash(length=6)}")
		курс = frappe.db.get_value("Course Chapter", frappe.db.get_value("Course Lesson", урок, "chapter"), "course")
		завести_схему(курс)
		зачислить(self.ученик, урок)
		личный = написать(self.ученик, курс, "Начал сам")
		self.assertFalse(frappe.db.get_value("Agent Student Artifact", личный, "organization"))

		назначить(self.компания, курс)
		компании = написать(self.ученик, курс, "Для компании")

		self.assertNotEqual(компании, личный)
		self.assertEqual(
			frappe.db.get_value("Agent Student Artifact", компании, "organization"), self.компания
		)

	def test_приостановленная_организация_пространством_не_выбирается(self):
		frappe.db.set_value("Learning Organization", self.компания, "status", "Suspended")
		frappe.set_user(self.ученик)

		self.assertEqual(student.my_spaces()["data"]["current"], "personal")
		self.assertEqual(student.set_space(self.компания)["error"]["code"], "space_not_available")


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
		self.курсы, self.уроки = {}, {}
		for пространство in ("икс", "игрек", "личное"):
			урок = создать_урок(f"Урок {пространство} {суффикс}")
			курс = зачислить(self.сотрудник, урок)
			завести_схему(курс)
			self.курсы[пространство], self.уроки[пространство] = курс, урок
		назначить(self.икс, self.курсы["икс"])
		назначить(self.игрек, self.курсы["игрек"])

		пространства = {"икс": self.икс, "игрек": self.игрек, "личное": "personal"}
		self.документы = {
			пространство: написать(self.сотрудник, курс, f"Текст {пространство}", пространства[пространство])
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

	def test_чтение_по_роли_и_пространству(self):
		"""Руководитель читает документы только своего пространства; автор читает
		все свои, но напрямую не правит."""
		случаи = {
			"руководитель икс → икс": (self.руководитель_икс, "икс", True),
			"руководитель икс → игрек": (self.руководитель_икс, "игрек", False),
			"руководитель икс → личное": (self.руководитель_икс, "личное", False),
			"руководитель игрек → игрек": (self.руководитель_игрек, "игрек", True),
			"руководитель игрек → икс": (self.руководитель_игрек, "икс", False),
			"автор → икс": (self.сотрудник, "икс", True),
			"автор → игрек": (self.сотрудник, "игрек", True),
			"автор → личное": (self.сотрудник, "личное", True),
		}
		for что, (кто, пространство, читает) in случаи.items():
			with self.subTest(что):
				self.assertEqual(self.читает(кто, пространство), читает)

		with self.subTest("автор не правит напрямую"):
			frappe.set_user(self.сотрудник)
			try:
				правит = frappe.has_permission("Agent Student Artifact", "write", doc=self.документы["икс"])
			finally:
				frappe.set_user("Administrator")
			self.assertFalse(правит)

	def test_участник_читает_документы_коллег_только_если_открыто_всем(self):
		"""Ушедший участник документов коллег не читает — последним, на той же
		открытой видимости."""
		with self.subTest("видимость по умолчанию"):
			self.assertFalse(self.читает(self.коллега, "икс"))

		self.видимость(self.икс, "All Members")
		with self.subTest("открыто всем"):
			self.assertTrue(self.читает(self.коллега, "икс"))
			self.assertFalse(self.читает(self.коллега, "личное"))
			self.assertFalse(self.читает(self.коллега, "игрек"))

		выйти(self.коллега, self.икс)
		with self.subTest("ушедший участник"):
			self.assertFalse(self.читает(self.коллега, "икс"))

	def test_документ_ушедшего_автора(self):
		"""Ушедший автор свой документ читает, организация — больше нет
		(learning-services#585)."""
		выйти(self.сотрудник, self.икс)

		with self.subTest("автор читает свой"):
			self.assertTrue(self.читает(self.сотрудник, "икс"))
		with self.subTest("руководитель документ ушедшего не читает"):
			self.assertFalse(self.читает(self.руководитель_икс, "икс"))

	def test_открытое_занятие_ушедшего_не_держит_пространство(self):
		"""Агент начал урок в организации, человек ушёл: без `space` работа идёт
		уже не туда — иначе новый документ попал бы в пространство организации."""
		frappe.db.set_value(
			"Agent Learning Session",
			создать_занятие(self.сотрудник, self.уроки["икс"]),
			"organization",
			self.икс,
		)
		self.assertEqual(spaces.пространство_курса(self.сотрудник, self.курсы["икс"]), self.икс)

		выйти(self.сотрудник, self.икс)

		self.assertIsNone(spaces.пространство_курса(self.сотрудник, self.курсы["икс"]))

	def test_ушедший_руководитель_теряет_доступ_сразу(self):
		выйти(self.руководитель_икс, self.икс)

		self.assertFalse(self.читает(self.руководитель_икс, "икс"))

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

	@classmethod
	def setUpClass(cls):
		"""Тесты класса записи только читают."""
		super().setUpClass()
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		cls.икс = создать_организацию(f"Икс {суффикс}")
		cls.игрек = создать_организацию(f"Игрек {суффикс}")
		cls.сотрудник = создать_ученика(f"ses-e-{суффикс}@example.com")
		добавить_в_организацию(cls.сотрудник, cls.икс)
		добавить_в_организацию(cls.сотрудник, cls.игрек)
		cls.руководитель = создать_менеджера(f"ses-m-{суффикс}@example.com", cls.икс)
		урок = создать_урок(f"Урок {суффикс}")
		зачислить(cls.сотрудник, урок)

		cls.занятия, cls.попытки, cls.события = {}, {}, {}
		for пространство, организация in (("икс", cls.икс), ("игрек", cls.игрек), ("личное", None)):
			занятие = frappe.get_doc(
				{
					"doctype": "Agent Learning Session",
					"student": cls.сотрудник,
					"lesson": урок,
					"organization": организация,
				}
			).insert(ignore_permissions=True)
			cls.занятия[пространство] = занятие.name
			cls.попытки[пространство] = (
				frappe.get_doc(
					{
						"doctype": "Agent Quiz Attempt",
						"session": занятие.name,
						"student": cls.сотрудник,
						"lesson": урок,
						"attempt_number": 1,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
			cls.события[пространство] = занятие.записать_событие(СОБЫТИЕ_ДИРЕКТИВА_ВЫДАНА, "тест").name

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")

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


class IntegrationTestSpaceChoice(IntegrationTestCase):
	"""Выбранное пространство и `space` в методах ученика (#346)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.компания = создать_организацию(f"Компания {суффикс}")
		self.чужая = создать_организацию(f"Чужая {суффикс}")
		self.ученик = создать_ученика(f"choice-{суффикс}@example.com")
		добавить_в_организацию(self.ученик, self.компания)

		self.урок_компании = создать_урок(f"Урок к {суффикс}")
		self.курс_компании = курс_урока(self.урок_компании)
		self.урок_свой = создать_урок(f"Урок с {суффикс}")
		self.курс_свой = зачислить(self.ученик, self.урок_свой)
		for курс in (self.курс_компании, self.курс_свой):
			завести_схему(курс)
			frappe.db.set_value("LMS Course", курс, "published", 1)
		frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.компания,
				"course": self.курс_компании,
				"deadline": "2030-01-01",
			}
		).insert(ignore_permissions=True)
		# Компания открывает только свой курс: каталог в её пространстве — один курс.
		организация = frappe.get_doc("Learning Organization", self.компания)
		организация.append("allowed_courses", {"course": self.курс_компании})
		организация.save(ignore_permissions=True)
		frappe.set_user(self.ученик)

	def курсы(self, space=None) -> set[str]:
		return {к["id"] for к in student.list_my_courses(space=space)["data"]["courses"]}

	def test_выбор_пространства(self):
		"""По умолчанию выбрана организация; выбор запоминается, а чужое
		отклоняется."""
		with self.subTest("по умолчанию выбрана организация"):
			ответ = student.my_spaces()["data"]

			self.assertEqual(ответ["current"], self.компания)
			self.assertEqual([п["id"] for п in ответ["spaces"]], ["personal", self.компания])
			self.assertEqual(
				[п["documents_visible_to"] for п in ответ["spaces"]], ["only_me", "managers"]
			)

		with self.subTest("выбор запоминается"):
			self.assertEqual(student.set_space("personal")["data"]["current"], "personal")
			self.assertEqual(student.my_spaces()["data"]["current"], "personal")
			self.assertEqual(student.whoami()["data"]["space"], "personal")

		with self.subTest("чужое отклоняется"):
			отказ = student.set_space(self.чужая)

			self.assertEqual(отказ["error"]["code"], "space_not_available")

	def test_личное_показывает_все_курсы_организация_свои(self):
		self.assertEqual(self.курсы("personal"), {self.курс_компании, self.курс_свой})
		self.assertEqual(self.курсы(self.компания), {self.курс_компании})
		self.assertEqual(self.курсы(), {self.курс_компании})

	def test_каталог_по_пространству(self):
		свободный = создать_курс(f"Свободный {frappe.generate_hash(length=6)}")
		frappe.db.set_value("LMS Course", свободный, "published", 1)

		личный = {к["id"] for к in student.list_catalog(space="personal")["data"]["courses"]}
		компании = {к["id"] for к in student.list_catalog(space=self.компания)["data"]["courses"]}

		self.assertIn(свободный, личный)
		self.assertNotIn(self.курс_свой, личный)
		self.assertEqual(компании, set())

	def test_запись_в_организации_её_назначение_по_выбору(self):
		frappe.set_user("Administrator")
		организация = frappe.get_doc("Learning Organization", self.компания)
		организация.append("allowed_courses", {"course": self.курс_свой})
		организация.save(ignore_permissions=True)
		frappe.set_user(self.ученик)

		ответ = student.enroll(self.курс_свой, space=self.компания)

		self.assertTrue(ответ["ok"], ответ)
		self.assertIn(self.курс_свой, self.курсы(self.компания))
		self.assertTrue(
			frappe.db.exists(
				"Course Allocation",
				{"organization": self.компания, "course": self.курс_свой, "chosen_by_member": 1},
			)
		)
		повтор = student.enroll(self.курс_свой, space=self.компания)
		self.assertEqual(повтор["error"]["code"], "already_enrolled")

	def test_один_курс_два_документа_общий_прогресс(self):
		личный = написать(self.ученик, self.курс_компании, "Для себя", "personal")
		компании = написать(self.ученик, self.курс_компании, "Для компании", self.компания)
		frappe.set_user(self.ученик)

		self.assertNotEqual(личный, компании)
		for space, текст in (("personal", "Для себя"), (self.компания, "Для компании")):
			блоки = student.artifact(self.курс_компании, "summary", space=space)["data"]["blocks"]
			self.assertEqual(блоки[0]["values"], {"goal": текст})
		self.assertEqual(
			frappe.db.count("LMS Enrollment", {"member": self.ученик, "course": self.курс_компании}), 1
		)

	def test_документ_пишется_в_пространство_открытого_занятия(self):
		frappe.set_user("Administrator")
		курс, урок = курс_релиза(self.компания)
		frappe.set_user(self.ученик)
		student.set_space("personal")
		занятие = student.start_lesson(урок, space=self.компания)["data"]

		документ = написать(self.ученик, курс, "По ходу урока")

		self.assertEqual(занятие["space"], self.компания)
		self.assertEqual(
			frappe.db.get_value("Agent Student Artifact", документ, "organization"), self.компания
		)

	def test_урок_в_двух_пространствах_два_занятия(self):
		frappe.set_user("Administrator")
		_, урок = курс_релиза(self.компания)
		frappe.set_user(self.ученик)
		первое = student.start_lesson(урок, space=self.компания)["data"]["session"]
		второе = student.start_lesson(урок, space="personal")["data"]["session"]
		повтор = student.start_lesson(урок, space=self.компания)["data"]["session"]

		self.assertNotEqual(первое, второе)
		self.assertEqual(повтор, первое)

	def test_курса_нет_в_пространстве_отказ(self):
		ответ = student.update_artifact(self.курс_свой, "summary", "goal", fields={"goal": "Текст"}, space=self.компания)

		self.assertEqual(ответ["error"]["code"], "course_not_in_space")

	def test_база_не_пускает_второй_личный_документ(self):
		frappe.set_user("Administrator")
		документ = {
			"doctype": "Agent Student Artifact",
			"student": self.ученик,
			"course": self.курс_свой,
			"artifact": "summary",
		}
		frappe.get_doc(документ).insert(ignore_permissions=True)

		with self.assertRaises(frappe.UniqueValidationError):
			frappe.get_doc(документ).insert(ignore_permissions=True)
