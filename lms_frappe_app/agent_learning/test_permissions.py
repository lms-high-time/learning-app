# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
import frappe.client
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_курс,
	создать_менеджера,
	создать_организацию,
	создать_пользователя,
	создать_урок,
	создать_ученика,
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

	def видно(self, кто: str, доктайп: str, **параметры) -> list:
		"""Список доктайпа глазами пользователя; Administrator возвращается и после ошибки."""
		frappe.set_user(кто)
		try:
			return frappe.get_list(доктайп, **параметры)
		finally:
			frappe.set_user("Administrator")

	def может_читать(self, кто: str, доктайп: str, имя: str) -> bool:
		frappe.set_user(кто)
		try:
			return bool(frappe.has_permission(доктайп, "read", doc=имя))
		finally:
			frappe.set_user("Administrator")

	def test_менеджер_не_видит_чужую_компанию(self):
		"""Занятия — ни в списке, ни по прямому обращению; журнал чужого занятия;
		назначения и членство чужой компании."""
		with self.subTest("занятия чужой компании в списке"):
			видимые = self.видно(self.менеджер_а, "Agent Learning Session", pluck="name")

			self.assertIn(self.занятие_а.name, видимые)
			self.assertNotIn(self.занятие_б.name, видимые)

		with self.subTest("чужое занятие по прямому обращению"):
			self.assertFalse(
				self.может_читать(self.менеджер_а, "Agent Learning Session", self.занятие_б.name)
			)

		своё = self.занятие_а.записать_событие("Directive Issued", "выдана директива")
		чужое = self.занятие_б.записать_событие("Directive Issued", "выдана директива")
		with self.subTest("журнал чужого занятия"):
			видимые = self.видно(self.менеджер_а, "Agent Session Event", pluck="name")

			self.assertIn(своё.name, видимые)
			self.assertNotIn(чужое.name, видимые)
			self.assertFalse(self.может_читать(self.менеджер_а, "Agent Session Event", чужое.name))

		своё = self._назначение(self.компания_а)
		чужое = self._назначение(self.компания_б)
		with self.subTest("назначения чужой компании"):
			видимые = self.видно(self.менеджер_а, "Course Allocation", pluck="name")

			self.assertIn(своё.name, видимые)
			self.assertNotIn(чужое.name, видимые)

		with self.subTest("членство чужой компании"):
			видимые = self.видно(self.менеджер_а, "Organization Membership", fields=["user", "organization"])
			организации = {строка.organization for строка in видимые}

			self.assertIn(self.компания_а, организации)
			self.assertNotIn(self.компания_б, организации)

	def test_видимость_без_руководства(self):
		"""Роль руководителя без членства ничего не открывает: роль даёт
		возможность смотреть отчёты, членство — по каким компаниям. Ученик без
		организаций не видит ни одного назначения; ученик видит только своё
		членство; служебная роль видит всё."""
		безродный = создать_пользователя(
			f"free-{frappe.generate_hash(length=6)}@example.com", "LMS Student", "Organization Manager"
		)
		with self.subTest("роль без членства ничего не открывает"):
			видимые = self.видно(безродный, "Agent Learning Session", pluck="name")

			self.assertNotIn(self.занятие_а.name, видимые)
			self.assertNotIn(self.занятие_б.name, видимые)

		# Пустое условие в Frappe означает «без ограничений»: ошибка здесь
		# открыла бы чужие назначения целиком.
		self._назначение(self.компания_а)
		одиночка = создать_ученика(f"solo-{frappe.generate_hash(length=6)}@example.com")
		with self.subTest("ученик без организаций не видит ни одного назначения"):
			self.assertEqual(self.видно(одиночка, "Course Allocation", pluck="name"), [])

		with self.subTest("ученик видит только своё членство"):
			видимые = self.видно(self.ученик_а, "Organization Membership", fields=["user"])

			self.assertEqual({строка.user for строка in видимые}, {self.ученик_а})

		служебный = создать_пользователя(
			f"svc-{frappe.generate_hash(length=6)}@example.com", "LMS Student", "Agent Service"
		)
		with self.subTest("служебная роль видит всё"):
			видимые = self.видно(служебный, "Agent Learning Session", pluck="name")

			self.assertIn(self.занятие_а.name, видимые)
			self.assertIn(self.занятие_б.name, видимые)

	def test_состав_назначения_виден_руководителю(self):
		"""Кому назначен курс, через `/api/resource` видит руководитель, а не
		участник: назначение ему видно, его адресаты — нет (learning-services#692).

		Два пути: документ назначения и его список с полем адресатов.
		"""
		назначение = frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.компания_а,
				"course": создать_курс(f"Курс {frappe.generate_hash(length=6)}"),
				"audience": "Selected Members",
				"members": [{"user": self.ученик_а}],
			}
		).insert(ignore_permissions=True)
		for кто, видит in ((self.ученик_а, False), (self.менеджер_а, True)):
			with self.subTest(кто):
				frappe.set_user(кто)
				try:
					документ = frappe.client.get("Course Allocation", назначение.name)
					строки = frappe.client.get_list(
						"Course Allocation Member",
						parent="Course Allocation",
						filters={"parent": назначение.name},
						fields=["name", "user"],
					)
				finally:
					frappe.set_user("Administrator")
				self.assertEqual(self.ученик_а in {с.get("user") for с in документ.get("members") or []}, видит)
				# Список дочерних строк Frappe проверяет правом `select` назначения —
				# у уровня 1 его нет ни у кого: адресатов список не отдаёт никому.
				self.assertNotIn(self.ученик_а, {с.get("user") for с in строки})
