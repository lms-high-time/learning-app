# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Сотрудники в «Команде»: приглашение ссылкой, роли, уход (#363)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import spaces
from lms_frappe_app.api import team
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_менеджера,
	создать_организацию,
	создать_пользователя,
	создать_ученика,
)


class IntegrationTestTeamMembers(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.компания = создать_организацию(f"Компания {суффикс}")
		self.руководитель = создать_менеджера(f"inv-m-{суффикс}@example.com", self.компания)
		# Роль сразу при создании: хук членства её увидит и не пересохранит User.
		self.админ = создать_пользователя(f"inv-a-{суффикс}@example.com", "LMS Student", "Organization Manager")
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

	def test_вступление_по_ссылке(self):
		"""Ушедший возвращается по ссылке; по отозванной не вступить; по ссылке
		вступают и попадают в пространство компании. Возврат — первым: новичок,
		вступивший раньше, занял бы место в организации."""
		self.от_имени(self.руководитель, team.remove_member, organization=self.компания, user=self.сотрудник)
		with self.subTest("ушедший возвращается по ссылке"):
			self.assertEqual(self.членство(self.сотрудник).status, "Left")

			self.от_имени(self.сотрудник, team.accept_invite, token=self.ссылка())

			self.assertEqual(self.членство(self.сотрудник).status, "Active")

		отозванная = self.ссылка()
		self.от_имени(self.руководитель, team.revoke_invite, token=отозванная)
		with self.subTest("отозванная ссылка не принимается"):
			ответ = self.от_имени(self.новичок, team.accept_invite, token=отозванная)

			self.assertEqual(ответ["error"]["code"], "invite_not_found")
			self.assertIsNone(self.членство(self.новичок))

		ключ = self.ссылка()
		with self.subTest("по ссылке вступают и попадают в пространство компании"):
			сведения = self.от_имени(self.новичок, team.invite_info, token=ключ)["data"]
			self.assertEqual((сведения["organization"], сведения["member"]), (self.компания, False))
			self.assertEqual(сведения["documents_visible_to"], "managers")

			self.assertTrue(self.от_имени(self.новичок, team.accept_invite, token=ключ)["ok"])
			self.assertEqual(self.членство(self.новичок), {"role": "Member", "status": "Active"})
			self.assertEqual(spaces.текущее(self.новичок), self.компания)

	def test_участник_выходит_сам(self):
		"""Выйти может любой участник, без права руководителя: согласие на то, что
		видит команда, отзывается уходом (learning-services#585)."""
		with patch("frappe.sendmail"):
			ответ = self.от_имени(
				self.сотрудник, team.remove_member, organization=self.компания, user=self.сотрудник
			)

		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(self.членство(self.сотрудник).status, "Left")
		повтор = self.от_имени(self.сотрудник, team.remove_member, organization=self.компания, user=self.сотрудник)
		self.assertEqual(повтор["error"]["code"], "not_a_member")

	def test_управление_командой_по_ролям(self):
		"""Признаки управления для интерфейса. Посторонний и участник командой не
		управляют; руководитель не меняет роли и не снимает коллегу-руководителя;
		последнего администратора не снять. Роль меняет администратор — последним."""
		with self.subTest("признаки управления для интерфейса"):
			руководителю = self.от_имени(self.руководитель, team.team, organization=self.компания)["data"]
			админу = self.от_имени(self.админ, team.team, organization=self.компания)["data"]

			self.assertEqual((руководителю["can_manage"], руководителю["can_change_roles"]), (True, False))
			self.assertEqual((админу["can_manage"], админу["can_change_roles"]), (True, True))

		коллега = создать_менеджера(f"inv-m2-{frappe.generate_hash(length=6)}@example.com", self.компания)
		орг = {"organization": self.компания}
		отказы = {
			"посторонний: ссылка": (self.посторонний, team.create_invite, орг, ("team_not_available", "not_allowed")),
			"участник: ссылка": (self.сотрудник, team.create_invite, орг, ("team_not_available", "not_allowed")),
			"руководитель: роль": (
				self.руководитель,
				team.set_member_role,
				{**орг, "user": self.сотрудник, "role": "Manager"},
				("not_allowed",),
			),
			"руководитель: снять коллегу-руководителя": (
				self.руководитель,
				team.remove_member,
				{**орг, "user": коллега},
				("not_allowed",),
			),
			"последний администратор: снять роль": (
				self.админ,
				team.set_member_role,
				{**орг, "user": self.админ, "role": "Member"},
				("last_org_admin",),
			),
			"участник: отметить уход другого": (
				self.сотрудник,
				team.remove_member,
				{**орг, "user": коллега},
				("team_not_available", "not_allowed"),
			),
			"посторонний: уйти из чужой команды": (
				self.посторонний,
				team.remove_member,
				{**орг, "user": self.посторонний},
				("not_a_member",),
			),
			"последний администратор: уйти": (
				self.админ,
				team.remove_member,
				{**орг, "user": self.админ},
				("last_org_admin",),
			),
		}
		for что, (кто, метод, аргументы, коды) in отказы.items():
			with self.subTest(что):
				ответ = self.от_имени(кто, метод, **аргументы)
				self.assertIn(ответ["error"]["code"], коды)

		with self.subTest("роль меняет администратор"):
			self.от_имени(
				self.админ, team.set_member_role, organization=self.компания, user=self.сотрудник, role="Manager"
			)

			self.assertEqual(self.членство(self.сотрудник).role, "Manager")
			self.assertIn("Organization Manager", frappe.get_roles(self.сотрудник))
