# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""`forget` стирает заметку без следа (learning-services#578).

Обычное удаление Frappe кладёт копию записи в корзину (Deleted Document), где
полный текст заметки видит администратор, а прежние тексты остаются в истории
правок (Version). Политика конфиденциальности и CONTRACT.md обещают удаление.

Очередь в тесте выключена: Frappe убирает историю задачей после коммита, и
метод обязан стереть её сам, даже если воркер не работает. Историю правок в
тестах Frappe не пишет (`ignore_version` по умолчанию — `frappe.in_test`), и
правка заметки здесь сохраняется с ней явно.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import student
from lms_frappe_app.patches.v0_1 import forgotten_notes
from lms_frappe_app.tests.sample_data import создать_ученика

ЗАМЕТКА = "Agent Student Note"


def заметка_с_историей(ученик: str, ключ: str) -> str:
	"""Заметка ученика, правленная после создания: с версией, как на сайте."""
	frappe.set_user(ученик)
	student.remember(kind="fact", key=ключ, text=f"первый текст {ключ}")
	frappe.set_user("Administrator")
	имя = frappe.db.get_value(ЗАМЕТКА, {"student": ученик, "note_key": ключ})
	запись = frappe.get_doc(ЗАМЕТКА, имя)
	запись.text = f"второй текст {ключ}"
	запись.save(ignore_permissions=True, ignore_version=False)
	assert frappe.db.exists("Version", {"ref_doctype": ЗАМЕТКА, "docname": имя}), "правка оставила версию"
	return имя


class IntegrationTestForget(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.ученик = создать_ученика(f"forget-{frappe.generate_hash(length=6)}@example.com")

	def test_заметка_не_остаётся_ни_в_корзине_ни_в_истории(self):
		имя = заметка_с_историей(self.ученик, "role")

		frappe.set_user(self.ученик)
		with patch("frappe.enqueue") as очередь:
			self.assertTrue(student.forget(key="role")["ok"])
		очередь.assert_called()

		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists(ЗАМЕТКА, имя))
		self.assertFalse(
			frappe.db.exists("Deleted Document", {"deleted_doctype": ЗАМЕТКА, "deleted_name": имя})
		)
		self.assertFalse(frappe.db.exists("Version", {"ref_doctype": ЗАМЕТКА, "docname": имя}))


class IntegrationTestПатчЗабытыхЗаметок(IntegrationTestCase):
	"""Заметки, стёртые до правки: копия в корзине и версии без живой заметки.
	Версии живой заметки патч не трогает."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.ученик = создать_ученика(f"forgotten-{frappe.generate_hash(length=6)}@example.com")
		self.стёртая = заметка_с_историей(self.ученик, "role")
		self.живая = заметка_с_историей(self.ученик, "goal")
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
