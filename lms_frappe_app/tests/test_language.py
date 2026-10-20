# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Язык платформы на новом сайте.

`Why:` интерфейс LMS своего переключателя не показывает — язык он спрашивает
у сервера, а настройка живёт в базе и не переживает пересоздания сайта.
Ставится при установке, а не хуком миграции: на действующем сайте язык —
выбор администратора (#540).
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.translate import get_all_languages

from lms_frappe_app.install import ЯЗЫК, обеспечить_русский_язык, согласовать_умолчание_языка


class IntegrationTestLanguage(IntegrationTestCase):
	def test_установка_ставит_русский_и_включает_его(self):
		"""Так Frappe оставляет язык после своей установки: поле пустое, `ru`
		выключен, а в кэше — список языков без него."""
		прежний = frappe.db.get_single_value("System Settings", "language")
		self.addCleanup(frappe.clear_cache)
		self.addCleanup(frappe.db.set_single_value, "System Settings", "language", прежний or "")
		frappe.db.set_single_value("System Settings", "language", "")
		frappe.db.set_value("Language", ЯЗЫК, "enabled", 0)
		frappe.client_cache.delete_value("languages")
		self.assertNotIn(ЯЗЫК, get_all_languages())

		обеспечить_русский_язык()

		self.assertEqual(frappe.db.get_single_value("System Settings", "language"), ЯЗЫК)
		self.assertTrue(frappe.db.get_value("Language", ЯЗЫК, "enabled"))
		self.assertIn(ЯЗЫК, get_all_languages())
		self.assertEqual(frappe.db.get_default("language"), ЯЗЫК, "новые пользователи — на русском")

	def test_новый_пользователь_получает_язык_системных_настроек(self):
		"""Как на стенде до #633: в настройках `ru`, а умолчание осталось `en`
		от мастера настройки."""
		прежний = frappe.db.get_single_value("System Settings", "language")
		умолчание = frappe.db.get_default("language")
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(frappe.db.set_default, "language", умолчание)
		self.addCleanup(frappe.db.set_single_value, "System Settings", "language", прежний or "")
		frappe.db.set_single_value("System Settings", "language", ЯЗЫК)
		frappe.db.set_default("language", "en")
		self.assertEqual(новый_пользователь(), "en", "так было до правки")

		согласовать_умолчание_языка()

		self.assertEqual(новый_пользователь(), ЯЗЫК)
		self.assertEqual(новый_пользователь(язык="en"), "en", "выбранный язык не перетирается")


def новый_пользователь(язык: str | None = None) -> str:
	"""Язык пользователя, созданного как при регистрации: гостем, с обходом прав."""
	frappe.set_user("Guest")
	пользователь = frappe.new_doc("User")
	пользователь.update(
		{
			"email": f"language-{frappe.generate_hash(length=8)}@example.com",
			"first_name": "Ученик",
			"send_welcome_email": 0,
			"language": язык,
		}
	)
	пользователь.flags.ignore_permissions = True
	пользователь.insert()
	return пользователь.language
