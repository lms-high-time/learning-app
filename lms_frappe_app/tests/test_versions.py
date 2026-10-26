# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import создать_куратора, создать_пользователя, создать_ученика

МЕТОД = "frappe.utils.change_log.get_versions"


def вызвать() -> dict:
	"""Метод, который отвечает на HTTP-имя `МЕТОД`, — как его находит
	`frappe.handler.execute_cmd`."""
	return frappe.get_attr(frappe.override_whitelisted_method(МЕТОД))()


class IntegrationTestVersions(IntegrationTestCase):
	"""Версии приложений по HTTP-имени метода Frappe — только пользователю Desk
	(learning-services#692)."""

	def test_версии_видит_только_пользователь_desk(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		кто = {
			"ученик": (создать_ученика(f"ver-{суффикс}@example.com"), False),
			"куратор без Desk": (создать_куратора(f"ver-cc-{суффикс}@example.com"), False),
			"администратор": (создать_пользователя(f"ver-sm-{суффикс}@example.com", "System Manager"), True),
		}
		for что, (пользователь, видит) in кто.items():
			with self.subTest(что):
				frappe.set_user(пользователь)
				if видит:
					self.assertIn("frappe", вызвать())
				else:
					with self.assertRaises(frappe.PermissionError):
						вызвать()
