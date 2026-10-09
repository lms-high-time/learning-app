# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_курс,
	создать_менеджера,
	создать_организацию,
	создать_пользователя,
	создать_ученика,
	создать_урок,
)


class IntegrationTestOrganizationIsolation(IntegrationTestCase):
	"""Менеджер компании A не видит обучение компании B.

	Проверяется в трёх видах сразу: список, прямое обращение по имени записи и
	журнал занятий. Фильтра списка мало — чужую запись попробуют открыть по
	идентификатору.
	"""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)

		self.компания_а = создать_организацию(f"Компания А {суффикс}")
		self.компания_б = создать_организацию(f"Компания Б {суффикс}")

		self.ученик_а = создать_ученика(f"a-{суффикс}@example.com")
		self.ученик_б = создать_ученика(f"b-{суффикс}@example.com")
		добавить_в_организацию(self.ученик_а, self.компания_а)
		добавить_в_организацию(self.ученик_б, self.компания_б)

		self.менеджер_а = создать_менеджера(f"m-{суффикс}@example.com", self.компания_а)

		self.урок = создать_урок(f"Урок {суффикс}")
		self.занятие_а = self._занятие(self.ученик_а, self.компания_а)
		self.занятие_б = self._занятие(self.ученик_б, self.компания_б)

	def _занятие(self, student: str, организация: str):
		"""Занятие в пространстве компании: руководителю видно по нему (#344)."""
		return frappe.get_doc(
			{
				"doctype": "Agent Learning Session",
				"student": student,
				"lesson": self.урок,
				"organization": организация,
			}
		).insert(ignore_permissions=True)

	def _назначение(self, организация: str):
		return frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": организация,
				"course": создать_курс(f"Курс {frappe.generate_hash(length=6)}"),
			}
		).insert(ignore_permissions=True)

	# --- занятия ---

	def test_менеджер_не_видит_занятий_чужой_компании_в_списке(self):
		frappe.set_user(self.менеджер_а)
		видимые = frappe.get_list("Agent Learning Session", pluck="name")
		self.assertIn(self.занятие_а.name, видимые)
		self.assertNotIn(self.занятие_б.name, видимые)

	def test_чужое_занятие_недоступно_по_прямому_обращению(self):
		frappe.set_user(self.менеджер_а)
		self.assertFalse(
			frappe.has_permission("Agent Learning Session", "read", doc=self.занятие_б.name)
		)

	def test_роль_без_членства_ничего_не_открывает(self):
		"""Роль даёт возможность смотреть отчёты, членство — по каким компаниям."""
		безродный = создать_пользователя(
			f"free-{frappe.generate_hash(length=6)}@example.com", "LMS Student", "Organization Manager"
		)

		frappe.set_user(безродный)
		видимые = frappe.get_list("Agent Learning Session", pluck="name")

		self.assertNotIn(self.занятие_а.name, видимые)
		self.assertNotIn(self.занятие_б.name, видимые)

	# --- журнал ---

	def test_журнал_чужого_занятия_не_виден(self):
		своё = self.занятие_а.записать_событие("Directive Issued", "выдана директива")
		чужое = self.занятие_б.записать_событие("Directive Issued", "выдана директива")

		frappe.set_user(self.менеджер_а)
		видимые = frappe.get_list("Agent Session Event", pluck="name")

		self.assertIn(своё.name, видимые)
		self.assertNotIn(чужое.name, видимые)
		self.assertFalse(frappe.has_permission("Agent Session Event", "read", doc=чужое.name))

	# --- назначения ---

	def test_назначения_чужой_компании_не_видны(self):
		своё = self._назначение(self.компания_а)
		чужое = self._назначение(self.компания_б)

		frappe.set_user(self.менеджер_а)
		видимые = frappe.get_list("Course Allocation", pluck="name")

		self.assertIn(своё.name, видимые)
		self.assertNotIn(чужое.name, видимые)

	def test_ученик_без_организаций_не_видит_ни_одного_назначения(self):
		# Пустое условие в Frappe означает «без ограничений»: ошибка здесь
		# открыла бы чужие назначения целиком.
		self._назначение(self.компания_а)
		одиночка = создать_ученика(f"solo-{frappe.generate_hash(length=6)}@example.com")

		frappe.set_user(одиночка)

		self.assertEqual(frappe.get_list("Course Allocation", pluck="name"), [])

	# --- членство ---

	def test_членство_чужой_компании_не_видно(self):
		frappe.set_user(self.менеджер_а)
		видимые = frappe.get_list("Organization Membership", fields=["user", "organization"])
		организации = {строка.organization for строка in видимые}

		self.assertIn(self.компания_а, организации)
		self.assertNotIn(self.компания_б, организации)

	def test_ученик_видит_только_своё_членство(self):
		frappe.set_user(self.ученик_а)
		видимые = frappe.get_list("Organization Membership", fields=["user"])
		self.assertEqual({строка.user for строка in видимые}, {self.ученик_а})

	# --- служебная роль ---

	def test_служебная_роль_видит_всё(self):
		служебный = создать_пользователя(
			f"svc-{frappe.generate_hash(length=6)}@example.com", "LMS Student", "Agent Service"
		)

		frappe.set_user(служебный)
		видимые = frappe.get_list("Agent Learning Session", pluck="name")

		self.assertIn(self.занятие_а.name, видимые)
		self.assertIn(self.занятие_б.name, видимые)
