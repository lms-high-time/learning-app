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
	"client_id": "Claude",
	"success_url": "/api/method/frappe.integrations.oauth2.approve",
	"failure_url": "https://example.com/callback?error=access_denied",
	"details": ["all"],
	"csrf_token": "csrf",
}


class IntegrationTestOAuthConfirmation(IntegrationTestCase):
	def test_экран_подтверждения(self):
		"""Экран называет агента и учётную запись и остаётся рабочим: кнопки и
		защита от CSRF — из исходного шаблона, их легко потерять."""
		self.addCleanup(frappe.set_user, "Administrator")
		ученик = создать_ученика(f"oauth-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(ученик)

		def экран(**поправки):
			return frappe.render_template(ШАБЛОН, {**КОНТЕКСТ, **поправки})

		страница = экран()

		with self.subTest("называет агента и учётную запись"):
			self.assertIn("Подключить Claude к&nbsp;Cenda?", страница)
			self.assertIn("Вы вошли как", страница)
			self.assertIn(ученик, страница)

		with self.subTest("остаётся рабочим"):
			self.assertIn(f'action="{КОНТЕКСТ["success_url"]}"', страница)
			self.assertIn(f'href="{КОНТЕКСТ["failure_url"]}"', страница)
			self.assertEqual(страница.count('name="csrf_token" value="csrf"'), 2)

		with self.subTest("сменить аккаунт — выход формой"):
			self.assertIn('action="/api/method/web_logout"', страница)

		# Обычный запрос прав — `all`, он сказан фразой; перечень — когда агент
		# просит что-то сверх него, и тогда без `all`.
		with self.subTest("права «all» не перечисляются"):
			self.assertNotIn("Запрошено:", страница)
		with self.subTest("права сверх «all» перечисляются"):
			перечень = экран(details=["all", "Email"])
			self.assertIn("Запрошено:", перечень)
			self.assertNotIn("all", перечень.split("Запрошено:", 1)[1].split("</p>", 1)[0])

		# Имя агента и права задаёт сторонний клиент, а шаблоны Frappe сами не
		# экранируют: разметка в имени перекрыла бы учётную запись на экране.
		with self.subTest("имя и права агента экранируются"):
			чужой = экран(client_id="<b>X</b>", details=["<i>Y</i>"])
			self.assertIn("&lt;b&gt;X", чужой)
			self.assertNotIn("<b>X", чужой)
			self.assertNotIn("<i>Y", чужой)

		with self.subTest("агент без имени"):
			self.assertIn("Подключить агента к&nbsp;Cenda?", экран(client_id=None))

		# Frappe 16 эту ветку не зовёт (ошибки уходят JSON); держим её рабочей
		# на случай, если начнёт.
		with self.subTest("ошибка подключения"):
			ошибка = экран(error="invalid_request")
			self.assertIn("Не удалось подключить Claude", ошибка)
			self.assertIn("invalid_request", ошибка)
			self.assertNotIn(КОНТЕКСТ["success_url"], ошибка)

	def test_экран_называет_хост_возврата_и_сайт_клиента(self):
		"""Имя агента клиент пишет о себе сам; хост адреса возврата и сайт
		документа клиента — нет (learning-services#661)."""
		self.addCleanup(lambda: frappe.flags.pop("oauth_credentials", None))
		claude_code = "https://claude.ai/oauth/claude-code-client-metadata"
		for что, данные, есть, нет in (
			(
				"сайт",
				{"client_id": "abc123", "redirect_uri": "https://claude.ai/api/mcp/auth_callback"},
				"Доступ получит сайт <b>claude.ai</b>",
				"Сведения об",
			),
			(
				"этот компьютер, клиент по адресу",
				{"client_id": claude_code, "redirect_uri": "http://localhost:54321/callback"},
				"программа на&nbsp;этом компьютере (localhost)",
				"сайт <b>localhost",
			),
			("разметка в хосте", {"redirect_uri": "https://<b>x/cb"}, "&lt;b&gt;x", "<b>x"),
		):
			with self.subTest(что):
				frappe.flags.oauth_credentials = данные
				страница = frappe.render_template(ШАБЛОН, КОНТЕКСТ)
				self.assertIn(есть, страница)
				self.assertNotIn(нет, страница)
		with self.subTest("сайт документа клиента"):
			frappe.flags.oauth_credentials = {"client_id": claude_code, "redirect_uri": "http://localhost/cb"}
			self.assertIn(
				"опубликованы на&nbsp;сайте <b>claude.ai</b>", frappe.render_template(ШАБЛОН, КОНТЕКСТ)
			)
