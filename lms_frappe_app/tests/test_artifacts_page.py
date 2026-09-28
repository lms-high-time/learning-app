# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.artifacts import document, write
from lms_frappe_app.tests.sample_data import создать_ученика, создать_урок, зачислить
from lms_frappe_app.api import student


class IntegrationTestArtifactsPage(IntegrationTestCase):
	"""Страница «Мои документы»: кто и что на ней видит."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"page-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(self.ученик, self.урок)
		frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"course": self.курс,
				"slug": "summary",
				"title": "Резюме проекта",
				"blocks": [
					{"block_key": "goal", "title": "Цель", "hint": "Одной фразой"},
					{"block_key": "sponsor", "title": "Спонсор"},
				],
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)
		student.update_artifact(self.курс, "summary", "goal", "Открыть **седьмую** кофейню")
		frappe.set_user("Administrator")

	def документ(self, пользователь: str, artifact: str = "summary") -> dict:
		frappe.set_user(пользователь)
		return document._артефакт_целиком(пользователь, self.курс, None, artifact)

	def переход(self, **параметры) -> str:
		"""Куда `/artifacts` отправляет с этими параметрами."""
		from lms_frappe_app.www.artifacts import get_context

		frappe.local.form_dict = frappe._dict(параметры)
		frappe.local.flags.redirect_location = None
		with self.assertRaises(frappe.Redirect):
			get_context(frappe._dict())
		return frappe.local.flags.redirect_location

	def test_старый_адрес_ведёт_в_spa_с_курсом_и_документом(self):
		"""Ссылки из веб-чата, писем и закладок не ломаются (learning-services#331)."""
		self.assertEqual(self.переход(), "/lms/documents")
		self.assertEqual(self.переход(course="c 1"), "/lms/documents?course=c%201")
		self.assertEqual(self.переход(course="c1", artifact="summary"), "/lms/documents/c1/summary")

	def test_markdown_собирается_одним_файлом(self):
		from lms_frappe_app.agent_learning.artifacts.export import собрать_markdown

		текст = собрать_markdown(self.документ(self.ученик))

		self.assertTrue(текст.startswith("# Резюме проекта\n"))
		self.assertIn("## Цель\n\nОткрыть **седьмую** кофейню", текст)
		self.assertIn("## Спонсор\n\n_Не заполнено._", текст)


class IntegrationTestArtifactsPageFiles(IntegrationTestCase):
	"""Блок-файл и блок-ссылка на странице (learning-services#315)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"page-f-{суффикс}@example.com")
		урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(self.ученик, урок)
		frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"course": self.курс,
				"slug": "plan",
				"title": "План",
				"blocks": [
					{"block_key": "money", "title": "Финплан", "kind": "file", "accept": "xlsx,csv"},
					{"block_key": "crm", "title": "База клиентов", "kind": "link"},
				],
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)
		write._положить_файл(self.ученик, self.курс, None, "plan", "money", "plan.csv", "Месяц;Выручка\nЯнварь;100\n".encode())
		student.update_artifact(self.курс, "plan", "crm", url="https://crm.example.com")

	def test_markdown_называет_файл_и_ссылку(self):
		from lms_frappe_app.agent_learning.artifacts.export import собрать_markdown

		текст = собрать_markdown(document._артефакт_целиком(self.ученик, self.курс, None, "plan"))

		self.assertIn("Файл: plan.csv", текст)
		self.assertIn("| Месяц | Выручка |", текст)
		self.assertIn("Ссылка: https://crm.example.com", текст)
