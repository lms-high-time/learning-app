# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from lms_frappe_app.agent_learning.doctype.agent_learning_session.agent_learning_session import (
	закрыть_брошенные_занятия,
)
from lms_frappe_app.tests.sample_data import создать_ученика, создать_урок

DOCTYPE = "Agent Learning Session"
ПЕРВЫЙ = "uchenik-odin@example.com"
ВТОРОЙ = "uchenik-dva@example.com"


class IntegrationTestAgentLearningSession(IntegrationTestCase):
	"""Занятие: изоляция по ученику, переходы статусов, закрытие брошенных."""

	def setUp(self):
		self.lesson = создать_урок()
		создать_ученика(ПЕРВЫЙ)
		создать_ученика(ВТОРОЙ)
		self.addCleanup(frappe.set_user, "Administrator")

	def занятие(self, student=ПЕРВЫЙ, **поля):
		return frappe.get_doc(
			{"doctype": DOCTYPE, "student": student, "lesson": self.lesson, **поля}
		).insert(ignore_permissions=True)

	# --- изоляция ---

	def test_ученик_видит_в_списке_только_свои_занятия(self):
		своё = self.занятие(student=ПЕРВЫЙ)
		чужое = self.занятие(student=ВТОРОЙ)

		frappe.set_user(ПЕРВЫЙ)
		видимые = frappe.get_list(DOCTYPE, pluck="name")

		self.assertIn(своё.name, видимые)
		self.assertNotIn(чужое.name, видимые)

	def test_чужое_занятие_недоступно_по_прямому_обращению(self):
		"""Фильтр списка сам по себе не защищает: чужую запись попробуют
		открыть по имени, а не искать в списке."""
		своё = self.занятие(student=ПЕРВЫЙ)
		чужое = self.занятие(student=ВТОРОЙ)

		frappe.set_user(ПЕРВЫЙ)
		self.assertTrue(frappe.has_permission(DOCTYPE, "read", doc=своё.name))
		self.assertFalse(frappe.has_permission(DOCTYPE, "read", doc=чужое.name))

	# --- переходы ---

	def test_курс_проставляется_по_уроку(self):
		ожидаемый = frappe.db.get_value(
			"Course Chapter", frappe.db.get_value("Course Lesson", self.lesson, "chapter"), "course"
		)
		self.assertEqual(self.занятие().course, ожидаемый)

	def test_недопустимый_переход_отклоняется(self):
		занятие = self.занятие()
		занятие.status = "Completed"
		занятие.save(ignore_permissions=True)

		занятие.status = "In Progress"
		with self.assertRaises(frappe.ValidationError):
			занятие.save(ignore_permissions=True)

	def test_завершение_проставляет_время(self):
		занятие = self.занятие()
		занятие.status = "Completed"
		занятие.save(ignore_permissions=True)
		self.assertTrue(занятие.finished_at)

	# --- журнал ---

	def test_событие_двигает_отметку_активности(self):
		занятие = self.занятие()
		frappe.db.set_value(
			DOCTYPE, занятие.name, "last_activity_at", add_to_date(now_datetime(), hours=-3)
		)

		занятие.записать_событие("Directive Issued", "выдана директива")

		обновлённая = frappe.db.get_value(DOCTYPE, занятие.name, "last_activity_at")
		self.assertGreater(обновлённая, add_to_date(now_datetime(), minutes=-1))

	def test_запись_журнала_не_изменяется(self):
		занятие = self.занятие()
		событие = занятие.записать_событие("Directive Issued", "выдан пакет урока")

		событие.note = "переписано"
		with self.assertRaises(frappe.ValidationError):
			событие.save(ignore_permissions=True)

	# --- брошенные занятия ---

	def test_брошенное_занятие_закрывается(self):
		занятие = self.занятие()
		frappe.db.set_value(
			DOCTYPE, занятие.name, "last_activity_at", add_to_date(now_datetime(), hours=-24)
		)

		закрыть_брошенные_занятия()
		занятие.reload()

		# Проверяется конкретное занятие, а не счётчик: задача сканирует всю
		# базу, и счётчик зависел бы от данных соседних тестов.
		self.assertEqual(занятие.status, "Abandoned")
		self.assertTrue(
			frappe.db.exists(
				"Agent Session Event", {"session": занятие.name, "kind": "Session Abandoned"}
			)
		)

	def test_свежее_занятие_не_трогается(self):
		занятие = self.занятие()
		закрыть_брошенные_занятия()
		занятие.reload()
		self.assertEqual(занятие.status, "In Progress")

	def test_завершённое_занятие_не_переоткрывается(self):
		занятие = self.занятие()
		занятие.status = "Completed"
		занятие.save(ignore_permissions=True)
		frappe.db.set_value(
			DOCTYPE, занятие.name, "last_activity_at", add_to_date(now_datetime(), hours=-24)
		)

		закрыть_брошенные_занятия()
		занятие.reload()

		self.assertEqual(занятие.status, "Completed")


class IntegrationTestDropObjectiveOutcomes(IntegrationTestCase):
	"""Патч убирает отметки целей по занятию: доктайп, таблицу, колонку и события.

	Состояние старого сайта собирается без сохранения документов: схема, из
	которой они ушли, их уже не примет. Событие — с выдуманным занятием:
	патч меняет схему, это фиксирует транзакцию, и настоящее занятие осталось
	бы в базе после теста.
	"""

	ОТМЕТКИ = "Agent Objective Outcome"

	def test_патч_убирает_доктайп_таблицу_колонку_и_события(self):
		from lms_frappe_app.patches.v0_1.drop_objective_outcomes import execute

		начало = now_datetime()
		frappe.db.sql_ddl(
			f"CREATE TABLE IF NOT EXISTS `tab{self.ОТМЕТКИ}` (`name` varchar(140) PRIMARY KEY, `parent` varchar(140))"
		)
		frappe.db.sql_ddl(
			f"ALTER TABLE `tab{DOCTYPE}` ADD COLUMN IF NOT EXISTS `brief_start` int(1) NOT NULL DEFAULT 0"
		)
		frappe.client_cache.delete_value(f"table_columns::tab{DOCTYPE}")
		if not frappe.db.exists("DocType", self.ОТМЕТКИ):
			доктайп = frappe.get_doc(
				{
					"doctype": "DocType",
					"name": self.ОТМЕТКИ,
					"module": "Agent Learning",
					"istable": 1,
					"fields": [{"fieldname": "objective", "fieldtype": "Data", "label": "Цель"}],
				}
			)
			доктайп.db_insert()
			доктайп.fields[0].db_insert()
		событие = frappe.get_doc(
			{"doctype": "Agent Session Event", "session": "нет-такого-занятия", "kind": "Material Issued"}
		)
		событие.db_insert()
		self.assertTrue(frappe.db.table_exists(self.ОТМЕТКИ, cached=False))
		self.assertTrue(frappe.db.has_column(DOCTYPE, "brief_start"))

		execute()
		execute()  # повторный запуск не падает

		self.assertFalse(frappe.db.table_exists(self.ОТМЕТКИ, cached=False))
		self.assertFalse(frappe.db.exists("DocType", self.ОТМЕТКИ))
		self.assertFalse(frappe.db.exists("DocField", {"parent": self.ОТМЕТКИ}))
		self.assertFalse(
			frappe.db.exists(
				"Deleted Document",
				{"deleted_doctype": "DocType", "deleted_name": self.ОТМЕТКИ, "creation": (">=", начало)},
			),
			"доктайп удалён насовсем, без копии в корзине",
		)
		self.assertFalse(frappe.db.has_column(DOCTYPE, "brief_start"))
		self.assertFalse(frappe.db.exists("Agent Session Event", событие.name))
