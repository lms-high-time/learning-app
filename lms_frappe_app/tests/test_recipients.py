# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Письмо о получателях данных при создании аккаунта (learning-services#747).

`Why:` ЗРУ-547, ст. 23 — о передаче данных третьему лицу субъекта письменно
уведомляют в течение трёх дней, а данные уходят получателям с первых минут
аккаунта.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import notices
from lms_frappe_app.agent_learning.recipients import письмо


class IntegrationTestRecipients(IntegrationTestCase):
	def setUp(self):
		self.письма = patch("frappe.sendmail").start()
		patch.object(notices, "почта_есть", return_value=True).start()
		self.addCleanup(patch.stopall)
		self.addCleanup(frappe.set_user, "Administrator")

	def завести(self, язык: str | None = None) -> str:
		адрес = f"recipients-{frappe.generate_hash(length=8)}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": адрес,
				"first_name": "Новый",
				"send_welcome_email": 0,
				**({"language": язык} if язык else {}),
			}
		).insert(ignore_permissions=True)
		return адрес

	def письма_на(self, адрес: str) -> list[dict]:
		return [в.kwargs for в in self.письма.call_args_list if в.kwargs.get("recipients") == [адрес]]

	def test_регистрация_получает_письмо_о_получателях(self):
		frappe.set_user("Guest")
		адрес = self.завести()

		(письмо_,) = self.письма_на(адрес)
		for получатель in ("Resend", "Cloudflare", "Google", "OpenRouter", "PostHog"):
			self.assertIn(получатель, письмо_["message"])
		self.assertIn("/privacy", письмо_["message"])

	def test_заведённому_сотрудником_письма_нет(self):
		"""Его заводят по просьбе человека — о данных говорят тогда же."""
		адрес = self.завести()

		self.assertEqual(self.письма_на(адрес), [])

	def test_без_почты_регистрация_не_падает(self):
		patch.object(notices, "почта_есть", return_value=False).start()
		frappe.set_user("Guest")
		адрес = self.завести()

		self.assertTrue(frappe.db.exists("User", адрес))
		self.assertEqual(self.письма_на(адрес), [])

	def test_язык_письма_язык_получателя(self):
		тема_ru, текст_ru = письмо("ru")
		тема_en, текст_en = письмо("en")

		with self.subTest("русский — перевод"):
			self.assertIn("Кто получает ваши данные", тема_ru)
			self.assertIn('/privacy"', текст_ru)
		with self.subTest("английский — исходник и английская политика"):
			self.assertIn("Who receives your data", тема_en)
			self.assertIn("/en/privacy", текст_en)
