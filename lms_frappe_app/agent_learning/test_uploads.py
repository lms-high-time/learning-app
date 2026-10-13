# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Файлы учеников без базы: байты из base64 (`uploads.из_base64`)."""

import base64

from frappe.tests import UnitTestCase

from lms_frappe_app.agent_learning import uploads


class TestИзBase64(UnitTestCase):
	def test_байты_из_base64(self):
		"""Пустое и битое — пустые байты: отказ «файл пуст» ставит вызывающий."""
		abc = base64.b64encode(b"abc").decode()
		случаи = (
			("base64", abc, b"abc"),
			("пробелы и перевод строки по краям", f"  {abc}\n", b"abc"),
			("data-адрес браузера", f"data:text/csv;base64,{abc}", b"abc"),
			("data без запятой — не адрес", f"data:{abc}", b""),
			("None", None, b""),
			("пустая строка", "", b""),
			("только пробелы", "   ", b""),
			("битый base64", "не base64!", b""),
			("без выравнивания", abc[:-1], b""),
			("перевод строки внутри", f"{abc[:2]}\n{abc[2:]}", b""),
		)
		for что, вход, ожидание in случаи:
			with self.subTest(что):
				self.assertEqual(uploads.из_base64(вход), ожидание)
