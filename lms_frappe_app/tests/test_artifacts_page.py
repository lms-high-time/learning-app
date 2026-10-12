# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestArtifactsPage(IntegrationTestCase):
	"""Страница «Мои документы»: старый адрес ведёт в SPA. Выгрузку проверяет
	`api/test_artifact_tables_api.py`."""

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
		for параметры, адрес in (
			({}, "/lms/documents"),
			({"course": "c 1"}, "/lms/documents?course=c%201"),
			({"course": "c1", "artifact": "summary"}, "/lms/documents/c1/summary"),
		):
			with self.subTest(f"learning-services#331: {параметры}"):
				self.assertEqual(self.переход(**параметры), адрес)
