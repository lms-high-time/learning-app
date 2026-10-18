# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""`forget` стирает заметку без следа (learning-services#578).

Обычное удаление Frappe кладёт копию записи в корзину (Deleted Document), где
полный текст заметки видит администратор, а прежние тексты остаются в истории
правок (Version). Политика конфиденциальности и CONTRACT.md обещают удаление.

Очередь в тесте выключена: Frappe убирает историю задачей после коммита, и
метод обязан стереть её сам, даже если воркер не работает.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import student
from lms_frappe_app.patches.v0_1 import forgotten_notes
from lms_frappe_app.tests.sample_data import создать_ученика

ЗАМЕТКА = "Agent Student Note"


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
		with patch("frappe.enqueue") as очередь:
			self.assertTrue(student.forget(key="role")["ok"])
		очередь.assert_called()

		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("Agent Student Note", имя))
		self.assertFalse(
			frappe.db.exists(
				"Deleted Document", {"deleted_doctype": "Agent Student Note", "deleted_name": имя}
			)
		)
		self.assertFalse(frappe.db.exists("Version", {"ref_doctype": "Agent Student Note", "docname": имя}))


class IntegrationTestПатчЗабытыхЗаметок(IntegrationTestCase):
	"""Заметки, стёртые до правки: копия в корзине и версии без живой заметки.
	Версии живой заметки патч не трогает."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.ученик = создать_ученика(f"forgotten-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(self.ученик)
		for ключ in ("role", "goal"):
			student.remember(kind="fact", key=ключ, text=f"первый текст {ключ}")
			student.remember(kind="fact", key=ключ, text=f"второй текст {ключ}")
		frappe.set_user("Administrator")
		self.стёртая = frappe.db.get_value(ЗАМЕТКА, {"student": self.ученик, "note_key": "role"})
		self.живая = frappe.db.get_value(ЗАМЕТКА, {"student": self.ученик, "note_key": "goal"})
		# Удаление прежним кодом: копия в корзине, версии — за упавшим воркером.
		with patch("frappe.enqueue"):
			frappe.delete_doc(ЗАМЕТКА, self.стёртая, ignore_permissions=True)

	def test_удаляет_копии_и_версии_стёртых_заметок(self):
		self.assertTrue(
			frappe.db.exists("Deleted Document", {"deleted_doctype": ЗАМЕТКА, "deleted_name": self.стёртая})
		)
		self.assertTrue(frappe.db.exists("Version", {"ref_doctype": ЗАМЕТКА, "docname": self.стёртая}))

		forgotten_notes.execute()
		forgotten_notes.execute()

		self.assertFalse(frappe.db.exists("Deleted Document", {"deleted_doctype": ЗАМЕТКА}))
		self.assertFalse(frappe.db.exists("Version", {"ref_doctype": ЗАМЕТКА, "docname": self.стёртая}))
		self.assertTrue(frappe.db.exists("Version", {"ref_doctype": ЗАМЕТКА, "docname": self.живая}))
