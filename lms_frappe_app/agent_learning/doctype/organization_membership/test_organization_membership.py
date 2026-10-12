# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from lms_frappe_app.agent_learning.doctype.course_allocation import course_allocation
from lms_frappe_app.agent_learning.doctype.course_allocation.course_allocation import (
	сверить_зачисления,
)
from lms_frappe_app.agent_learning.doctype.learning_organization.learning_organization import (
	организации_пользователя,
)
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_курс,
	создать_организацию,
	создать_ученика,
)

НОВИЧОК = "novichok@example.com"
СТАРОЖИЛ = "starozhil@example.com"


class IntegrationTestOrganizationMembership(IntegrationTestCase):
	"""Доприём: курсы, назначенные до прихода сотрудника."""

	@classmethod
	def setUpClass(cls):
		"""Пользователей тесты только читают: членство — в организации своего теста."""
		super().setUpClass()
		frappe.set_user("Administrator")
		создать_ученика(НОВИЧОК)
		создать_ученика(СТАРОЖИЛ)

	def заново(self) -> None:
		"""Своя организация со старожилом и свой курс — у теста или у строки таблицы."""
		self.организация = создать_организацию(f"Компания {frappe.generate_hash(length=6)}")
		self.курс = создать_курс(f"Курс {frappe.generate_hash(length=6)}")
		добавить_в_организацию(СТАРОЖИЛ, self.организация)

	def назначить(self, **поля):
		return frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.организация,
				"course": self.курс,
				**поля,
			}
		).insert(ignore_permissions=True)

	def записан(self, user: str, course: str | None = None) -> bool:
		return bool(
			frappe.db.exists("LMS Enrollment", {"member": user, "course": course or self.курс})
		)

	def test_новый_участник_и_назначенный_ранее_курс(self):
		"""Новый участник получает курс, назначенный до его прихода. Поимённое
		назначение ему не достаётся: у поимённого адресат задан явно, и человека,
		которого в списке нет, курс не касается. Участник приостановленной
		организации курс не получает. У каждого случая своя организация."""
		случаи = {
			"назначенный ранее курс достаётся": ({}, False, True),
			"поимённое назначение новичку не достаётся": (
				{"audience": "Selected Members", "members": [{"user": СТАРОЖИЛ}]},
				False,
				False,
			),
			"участник приостановленной организации не получает": ({}, True, False),
		}
		for что, (поля, приостановить, записан) in случаи.items():
			with self.subTest(что):
				self.заново()
				self.назначить(**поля)
				if приостановить:
					frappe.db.set_value("Learning Organization", self.организация, "status", "Suspended")
				self.assertFalse(self.записан(НОВИЧОК))

				добавить_в_организацию(НОВИЧОК, self.организация)

				self.assertEqual(self.записан(НОВИЧОК), записан)

	def test_сверка_догоняет_членство_созданное_в_обход_хука(self):
		self.заново()
		self.назначить()
		# Имитируем импорт: строка появляется прямо в базе, хуки не выполняются.
		frappe.db.sql(
			"""insert into `tabOrganization Membership`
			(name, user, organization, role, creation, modified, owner, modified_by, docstatus, idx)
			values (%s, %s, %s, 'Member', now(), now(), 'Administrator', 'Administrator', 0, 0)""",
			(frappe.generate_hash(length=10), НОВИЧОК, self.организация),
		)
		self.assertFalse(self.записан(НОВИЧОК))

		сверить_зачисления()

		self.assertTrue(self.записан(НОВИЧОК))

	def test_сбой_одного_зачисления_не_останавливает_сверку(self):
		self.заново()
		self.назначить()
		третий = создать_ученика(f"tretiy-{frappe.generate_hash(length=6)}@example.com")
		# Сбойный участник — новее: сверка идёт по составу от новых к старым,
		# и третий обрабатывается после сбоя.
		for участник, часов in ((НОВИЧОК, 0), (третий, -1)):
			frappe.db.sql(
				"""insert into `tabOrganization Membership`
				(name, user, organization, role, creation, modified, owner, modified_by, docstatus, idx)
				values (%s, %s, %s, 'Member', %s, %s, 'Administrator', 'Administrator', 0, 0)""",
				(
					frappe.generate_hash(length=10),
					участник,
					self.организация,
					add_to_date(now_datetime(), hours=часов),
					add_to_date(now_datetime(), hours=часов),
				),
			)
		записать = course_allocation.записать_зачисление

		def записать_и_упасть(участник, курс):
			записать(участник, курс)
			if участник == НОВИЧОК:
				raise frappe.ValidationError("сбой после записи")

		with patch.object(course_allocation, "записать_зачисление", записать_и_упасть):
			сверить_зачисления()

		self.assertTrue(self.записан(третий))
		# Зачисление сбойного откатано до точки сохранения.
		self.assertFalse(self.записан(НОВИЧОК))
		назначения = frappe.get_all(
			"Course Allocation", filters={"organization": self.организация}, pluck="name"
		)
		self.assertTrue(
			frappe.db.exists(
				"Error Log", {"reference_doctype": "Course Allocation", "reference_name": ("in", назначения)}
			)
		)

	def test_повторная_сверка_не_плодит_зачисления(self):
		self.заново()
		self.назначить()
		добавить_в_организацию(НОВИЧОК, self.организация)

		сверить_зачисления()
		сверить_зачисления()

		записи = frappe.get_all(
			"LMS Enrollment", filters={"member": НОВИЧОК, "course": self.курс}
		)
		self.assertEqual(len(записи), 1)


class IntegrationTestMembershipStatus(IntegrationTestCase):
	"""Членство закрывается, а не удаляется; роль Frappe следует за ним."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.организация = создать_организацию(f"Компания {суффикс}")
		self.курс = создать_курс(f"Курс {суффикс}")
		self.человек = создать_ученика(f"status-{суффикс}@example.com")

	def членство(self, role: str = "Member"):
		return frappe.get_doc(
			"Organization Membership", добавить_в_организацию(self.человек, self.организация, role)
		)

	def роли(self) -> set[str]:
		return set(frappe.get_roles(self.человек))

	def test_выход_и_возвращение(self):
		"""Выход ставит дату, и ушедший не числится в организации; вернувшийся
		получает курс, назначенный без него, а дата выхода снимается."""
		членство = self.членство()
		членство.status = "Left"
		членство.save(ignore_permissions=True)
		with self.subTest("выход ставит дату"):
			self.assertEqual(str(членство.left_on), frappe.utils.nowdate())
		with self.subTest("ушедший не числится в организации"):
			self.assertNotIn(self.организация, организации_пользователя(self.человек))

		frappe.get_doc(
			{"doctype": "Course Allocation", "organization": self.организация, "course": self.курс}
		).insert(ignore_permissions=True)
		with self.subTest("назначенное без него ушедшему не достаётся"):
			self.assertFalse(
				frappe.db.exists("LMS Enrollment", {"member": self.человек, "course": self.курс})
			)

		членство.status = "Active"
		членство.save(ignore_permissions=True)
		with self.subTest("возвращение снимает дату"):
			self.assertFalse(членство.left_on)
		with self.subTest("вернувшийся получает назначенное без него"):
			self.assertTrue(frappe.db.exists("LMS Enrollment", {"member": self.человек, "course": self.курс}))

	def test_роль_руководителя_выдаётся_по_членству(self):
		self.assertNotIn("Organization Manager", self.роли())

		self.членство("Manager")

		self.assertIn("Organization Manager", self.роли())

	def test_роль_снимается_при_выходе_понижении_и_удалении(self):
		for как_снять in ("выход", "понижение", "удаление"):
			with self.subTest(как_снять):
				членство = self.членство("Org Admin")
				self.assertIn("Organization Manager", self.роли())

				if как_снять == "выход":
					членство.status = "Left"
					членство.save(ignore_permissions=True)
				elif как_снять == "понижение":
					членство.role = "Member"
					членство.save(ignore_permissions=True)
				else:
					членство.delete(ignore_permissions=True)

				self.assertNotIn("Organization Manager", self.роли())
				# Следующий случай заводит членство заново: повтор отвергается.
				if frappe.db.exists("Organization Membership", членство.name):
					frappe.delete_doc("Organization Membership", членство.name, ignore_permissions=True)

	def test_роль_остаётся_пока_руководит_хоть_одной_организацией(self):
		другая = создать_организацию(f"Другая {frappe.generate_hash(length=6)}")
		добавить_в_организацию(self.человек, другая, "Manager")
		членство = self.членство("Manager")

		членство.status = "Left"
		членство.save(ignore_permissions=True)

		self.assertIn("Organization Manager", self.роли())


class IntegrationTestSelectedMembers(IntegrationTestCase):
	"""Поимённое назначение — только действующим участникам (#345)."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.организация = создать_организацию(f"Компания {суффикс}")
		self.курс = создать_курс(f"Курс {суффикс}")
		self.действующий = создать_ученика(f"sel-a-{суффикс}@example.com")
		self.ушедший = создать_ученика(f"sel-l-{суффикс}@example.com")
		self.посторонний = создать_ученика(f"sel-o-{суффикс}@example.com")
		добавить_в_организацию(self.действующий, self.организация)
		членство = frappe.get_doc(
			"Organization Membership", добавить_в_организацию(self.ушедший, self.организация)
		)
		членство.status = "Left"
		членство.save(ignore_permissions=True)

	def записан(self, user: str) -> bool:
		return bool(frappe.db.exists("LMS Enrollment", {"member": user, "course": self.курс}))

	def test_курс_получает_только_действующий_участник(self):
		frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.организация,
				"course": self.курс,
				"audience": "Selected Members",
				"members": [{"user": u} for u in (self.действующий, self.ушедший, self.посторонний)],
			}
		).insert(ignore_permissions=True)
		сверить_зачисления()

		self.assertTrue(self.записан(self.действующий))
		self.assertFalse(self.записан(self.ушедший))
		self.assertFalse(self.записан(self.посторонний))
