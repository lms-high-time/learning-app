# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Сотрудники в «Команде»: приглашение ссылкой, роли, уход (#363)."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import spaces
from lms_frappe_app.api import team
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
)


class IntegrationTestTeamMembers(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.компания = создать_организацию(f"Компания {суффикс}")
		self.руководитель = создать_менеджера(f"inv-m-{суффикс}@example.com", self.компания)
		self.админ = создать_ученика(f"inv-a-{суффикс}@example.com")
		добавить_в_организацию(self.админ, self.компания, "Org Admin")
		self.сотрудник = создать_ученика(f"inv-e-{суффикс}@example.com")
		добавить_в_организацию(self.сотрудник, self.компания)
		self.новичок = создать_ученика(f"inv-n-{суффикс}@example.com")
		self.посторонний = создать_ученика(f"inv-o-{суффикс}@example.com")

	def от_имени(self, кто: str, метод, **аргументы):
		frappe.set_user(кто)
		try:
			return метод(**аргументы)
		finally:
			frappe.set_user("Administrator")

	def ссылка(self) -> str:
		return self.от_имени(self.руководитель, team.create_invite, organization=self.компания)["data"]["token"]

	def членство(self, user: str):
		return frappe.db.get_value(
			"Organization Membership", {"user": user, "organization": self.компания}, ["role", "status"], as_dict=True
		)

	def test_по_ссылке_вступают_и_попадают_в_пространство_компании(self):
		ключ = self.ссылка()

		сведения = self.от_имени(self.новичок, team.invite_info, token=ключ)["data"]
		self.assertEqual((сведения["organization"], сведения["member"]), (self.компания, False))
		self.assertEqual(сведения["documents_visible_to"], "managers")

		self.assertTrue(self.от_имени(self.новичок, team.accept_invite, token=ключ)["ok"])
		self.assertEqual(self.членство(self.новичок), {"role": "Member", "status": "Active"})
		self.assertEqual(spaces.текущее(self.новичок), self.компания)

	def test_отозванная_ссылка_не_принимается(self):
		ключ = self.ссылка()
		self.от_имени(self.руководитель, team.revoke_invite, token=ключ)

		ответ = self.от_имени(self.новичок, team.accept_invite, token=ключ)

		self.assertEqual(ответ["error"]["code"], "invite_not_found")
		self.assertIsNone(self.членство(self.новичок))

	def test_ушедший_возвращается_по_ссылке(self):
		self.от_имени(self.руководитель, team.remove_member, organization=self.компания, user=self.сотрудник)
		self.assertEqual(self.членство(self.сотрудник).status, "Left")

		self.от_имени(self.сотрудник, team.accept_invite, token=self.ссылка())

		self.assertEqual(self.членство(self.сотрудник).status, "Active")

	def test_посторонний_и_участник_не_управляют_командой(self):
		for кто in (self.посторонний, self.сотрудник):
			with self.subTest(кто):
				ответ = self.от_имени(кто, team.create_invite, organization=self.компания)
				self.assertIn(ответ["error"]["code"], ("team_not_available", "not_allowed"))

	def test_роли_меняет_только_администратор(self):
		ответ = self.от_имени(
			self.руководитель, team.set_member_role, organization=self.компания, user=self.сотрудник, role="Manager"
		)
		self.assertEqual(ответ["error"]["code"], "not_allowed")

		self.от_имени(self.админ, team.set_member_role, organization=self.компания, user=self.сотрудник, role="Manager")

		self.assertEqual(self.членство(self.сотрудник).role, "Manager")
		self.assertIn("Organization Manager", frappe.get_roles(self.сотрудник))

	def test_последнего_администратора_не_снять(self):
		ответ = self.от_имени(
			self.админ, team.set_member_role, organization=self.компания, user=self.админ, role="Member"
		)
		self.assertEqual(ответ["error"]["code"], "last_org_admin")

		ответ = self.от_имени(self.админ, team.remove_member, organization=self.компания, user=self.админ)
		self.assertEqual(ответ["error"]["code"], "last_org_admin")

	def test_руководитель_не_снимает_коллегу_руководителя(self):
		коллега = создать_менеджера(f"inv-m2-{frappe.generate_hash(length=6)}@example.com", self.компания)

		ответ = self.от_имени(self.руководитель, team.remove_member, organization=self.компания, user=коллега)

		self.assertEqual(ответ["error"]["code"], "not_allowed")

	def test_признаки_управления_для_интерфейса(self):
		руководителю = self.от_имени(self.руководитель, team.team, organization=self.компания)["data"]
		админу = self.от_имени(self.админ, team.team, organization=self.компания)["data"]

		self.assertEqual((руководителю["can_manage"], руководителю["can_change_roles"]), (True, False))
		self.assertEqual((админу["can_manage"], админу["can_change_roles"]), (True, True))
