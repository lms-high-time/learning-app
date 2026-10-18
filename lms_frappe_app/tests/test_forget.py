# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""`forget` стирает заметку без следа (learning-services#578).

Обычное удаление Frappe кладёт копию записи в корзину (Deleted Document), где
полный текст заметки видит администратор, а прежние тексты остаются в истории
правок (Version). Политика конфиденциальности и CONTRACT.md обещают удаление.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import student
from lms_frappe_app.tests.sample_data import создать_ученика


class IntegrationTestForget(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.ученик = создать_ученика(f"forget-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(self.ученик)

	def test_заметка_не_остаётся_ни_в_корзине_ни_в_истории(self):
		student.remember(kind="fact", key="role", text="первый текст о роли")
		student.remember(kind="fact", key="role", text="второй текст о роли")
		имя = frappe.db.get_value("Agent Student Note", {"student": self.ученик, "note_key": "role"})
		frappe.set_user("Administrator")
		self.assertTrue(
			frappe.db.exists("Version", {"ref_doctype": "Agent Student Note", "docname": имя}),
			"предусловие: правка заметки оставила версию",
		)

		frappe.set_user(self.ученик)
		self.assertTrue(student.forget(key="role")["ok"])

		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("Agent Student Note", имя))
		self.assertFalse(
			frappe.db.exists(
				"Deleted Document", {"deleted_doctype": "Agent Student Note", "deleted_name": имя}
			)
		)
		self.assertFalse(frappe.db.exists("Version", {"ref_doctype": "Agent Student Note", "docname": имя}))
