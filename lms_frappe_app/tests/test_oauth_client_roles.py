# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import создать_куратора, создать_ученика

РОЛИ_ПЛАТФОРМЫ = {"LMS Student", "Course Creator"}


def клиент(**поля) -> "frappe.Document":
	return frappe.get_doc(
		{
			"doctype": "OAuth Client",
			"app_name": f"Агент {frappe.generate_hash(length=5)}",
			"redirect_uris": "https://agent.example.com/cb",
			"default_redirect_uri": "https://agent.example.com/cb",
			"grant_type": "Authorization Code",
			"response_type": "Code",
			"scopes": "all",
			**поля,
		}
	).insert(ignore_permissions=True)


class IntegrationTestOAuthClientRoles(IntegrationTestCase):
	"""Агент ученика авторизуется клиентом, который зарегистрировал сам.

	`Why:` Frappe даёт клиенту без ролей только `Desk User`, и ученик без
	desk-доступа получал «Invalid client_id parameter value» на странице
	авторизации — то есть не мог подключить агента вообще. Ловилось на
	живом стенде с ChatGPT (#26).
	"""

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_новый_клиент_получает_роли_платформы(self):
		роли = {р.role for р in клиент().allowed_roles}
		self.assertTrue(РОЛИ_ПЛАТФОРМЫ <= роли)
		# Роль Frappe по умолчанию не отнимается: desk-пользователи тоже агенты.
		self.assertIn("Desk User", роли)

	def test_ученик_проходит_проверку_роли_клиента(self):
		к = клиент()
		ученик = создать_ученика(f"uch-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(ученик)
		self.assertTrue(frappe.get_doc("OAuth Client", к.name).user_has_allowed_role())

	def test_куратор_без_desk_проходит_проверку(self):
		к = клиент()
		куратор = создать_куратора(f"kur-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(куратор)
		self.assertTrue(frappe.get_doc("OAuth Client", к.name).user_has_allowed_role())

	def test_явно_заданные_роли_не_дублируются(self):
		к = клиент(allowed_roles=[{"role": "LMS Student"}])
		роли = [р.role for р in к.allowed_roles]
		self.assertEqual(len(роли), len(set(роли)))
		self.assertTrue(РОЛИ_ПЛАТФОРМЫ <= set(роли))
