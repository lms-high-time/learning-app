# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Экран подтверждения доступа OAuth.

`Why:` перекрытие чужого шаблона держится на порядке загрузчика Frappe —
приложения перебираются в обратном порядке установки. Порядок задаётся не
нами, и смена его отняла бы у экрана учётную запись молча: страница осталась
бы рабочей, просто перестала бы отвечать на вопрос, ради которого её правили.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import создать_ученика

ШАБЛОН = "templates/includes/oauth_confirmation.html"

КОНТЕКСТ = {
	"client_id": "Агент ученика",
	"success_url": "/api/method/frappe.integrations.oauth2.approve",
	"failure_url": "https://example.com/callback?error=access_denied",
	"details": ["all"],
	"csrf_token": "csrf",
}


class IntegrationTestOAuthConfirmation(IntegrationTestCase):
	def test_экран_подтверждения(self):
		"""Экран называет учётную запись и остаётся рабочим: кнопки и защита от
		CSRF — из исходного шаблона, их легко потерять."""
		self.addCleanup(frappe.set_user, "Administrator")
		ученик = создать_ученика(f"oauth-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(ученик)

		страница = frappe.render_template(ШАБЛОН, КОНТЕКСТ)

		with self.subTest("называет учётную запись"):
			self.assertIn("Вы вошли как", страница)
			self.assertIn(ученик, страница)

		with self.subTest("остаётся рабочим"):
			self.assertIn(КОНТЕКСТ["success_url"], страница)
			self.assertIn(КОНТЕКСТ["failure_url"], страница)
			self.assertIn('name="csrf_token"', страница)
			# Перечень запрашиваемых прав — то, ради чего экран существует.
			self.assertIn("All", страница)
