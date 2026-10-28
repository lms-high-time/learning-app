# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Ссылки в письмах (learning-services#693)."""

from frappe.tests import UnitTestCase

from lms_frappe_app.agent_learning.notices import ссылка


class UnitTestEmailLink(UnitTestCase):
	def test_адрес_ссылки_экранирован(self):
		"""Адрес собирается из имён документов и выводится в атрибут экранированным."""
		self.assertEqual(
			ссылка('https://cenda.app/lms/courses/a"b<c>&d', "Открыть курс"),
			'<a href="https://cenda.app/lms/courses/a&quot;b&lt;c&gt;&amp;d">Открыть курс</a>',
		)
