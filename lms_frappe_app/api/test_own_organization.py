# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Организацию создаёт сам пользователь (#366)."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import spaces
from lms_frappe_app.api import team
from lms_frappe_app.tests.sample_data import настроить_квиз, создать_организацию, создать_ученика


class IntegrationTestOwnOrganization(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.суффикс = frappe.generate_hash(length=6)
		self.создатель = создать_ученика(f"own-{self.суффикс}@example.com")
		настроить_квиз(own_org_member_limit=2, own_org_limit=1)
		self.addCleanup(настроить_квиз, own_org_member_limit=25, own_org_limit=3)

	def от_имени(self, кто: str, метод, **аргументы):
		frappe.set_user(кто)
		try:
			return метод(**аргументы)
		finally:
			frappe.set_user("Administrator")

	def создать(self, название: str) -> dict:
		return self.от_имени(self.создатель, team.create_organization, title=название)

	def test_создатель_администратор_в_её_пространстве(self):
		ответ = self.создать(f"  Моя   команда {self.суффикс} ")

		организация = ответ["data"]["organization"]
		self.assertEqual(ответ["data"]["title"], f"Моя команда {self.суффикс}")
		self.assertEqual(frappe.db.get_value("Learning Organization", организация, "verified"), 0)
		self.assertEqual(
			frappe.db.get_value(
				"Organization Membership", {"user": self.создатель, "organization": организация}, "role"
			),
			"Org Admin",
		)
		self.assertIn("Organization Manager", frappe.get_roles(self.создатель))
		self.assertEqual(spaces.текущее(self.создатель), организация)

	def test_лимит_своих_организаций_и_занятое_название(self):
		self.создать(f"Первая {self.суффикс}")

		self.assertEqual(self.создать(f"Вторая {self.суффикс}")["error"]["code"], "organization_limit")
		занятое = создать_организацию(f"Занятое {self.суффикс}")
		frappe.db.delete("Learning Organization", {"created_by": self.создатель})
		self.assertEqual(self.создать(занятое)["error"]["code"], "organization_name_taken")

	def test_неподтверждённая_не_принимает_сверх_лимита(self):
		организация = self.создать(f"Малая {self.суффикс}")["data"]["organization"]
		ключ = self.от_имени(self.создатель, team.create_invite, organization=организация)["data"]["token"]
		второй = создать_ученика(f"own-2-{self.суффикс}@example.com")
		третий = создать_ученика(f"own-3-{self.суффикс}@example.com")

		self.assertTrue(self.от_имени(второй, team.accept_invite, token=ключ)["ok"])
		сведения = self.от_имени(третий, team.invite_info, token=ключ)["data"]
		self.assertFalse(сведения["verified"])
		self.assertEqual(
			self.от_имени(третий, team.accept_invite, token=ключ)["error"]["code"], "organization_full"
		)

		frappe.db.set_value("Learning Organization", организация, "verified", 1)
		self.assertTrue(self.от_имени(третий, team.accept_invite, token=ключ)["ok"])

	def test_подключённая_нами_подтверждена(self):
		организация = создать_организацию(f"Наша {self.суффикс}")

		self.assertEqual(frappe.db.get_value("Learning Organization", организация, "verified"), 1)

	def test_условия_называют_числа_до_создания(self):
		"""Страница создания говорит лимиты числом, а не отказом (#379)."""
		условия = self.от_имени(self.создатель, team.organization_terms)["data"]
		self.assertEqual(условия, {"member_limit": 2, "organizations_left": 1})

		организация = self.создать(f"Числа {self.суффикс}")["data"]["organization"]
		условия = self.от_имени(self.создатель, team.organization_terms)["data"]
		self.assertEqual(условия["organizations_left"], 0)

		self.assertEqual(
			self.от_имени(self.создатель, team.team, organization=организация)["data"]["member_limit"], 2
		)
		frappe.db.set_value("Learning Organization", организация, "verified", 1)
		self.assertIsNone(
			self.от_имени(self.создатель, team.team, organization=организация)["data"]["member_limit"]
		)
