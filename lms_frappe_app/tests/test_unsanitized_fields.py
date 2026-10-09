# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Поля без очистки HTML не выходят туда, где Desk выводит их сырыми
(lms-high-time/learning-services#521).

`Why:` поле с `ignore_xss_filter` хранит `<`, `>` и разметку как есть, а
Frappe выводит часть значений без экранирования: Report view — Data и
Small Text, печать — Small Text и Long Text, выпадающий список ссылки и
поиск — `title_field` и `search_fields`. Разметку, записанную автором,
исполнил бы браузер администратора. Правило — тестом по схемам, а не
вниманием: новое поле с флагом без скрытия ломает этот тест.
"""

import json
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase

КОРЕНЬ = Path(frappe.get_app_path("lms_frappe_app"))
#: Типы, которые печать выводит без экранирования.
СЫРЫЕ_В_ПЕЧАТИ = frozenset({"Small Text", "Long Text"})


def _схемы() -> list[dict]:
	return [
		схема
		for путь in sorted(КОРЕНЬ.glob("*/doctype/*/*.json"))
		if (схема := json.loads(путь.read_text(encoding="utf-8"))).get("doctype") == "DocType"
	]


def _поиск(title_field: str | None, search_fields: str | None) -> set[str]:
	return ({title_field} | {п.strip() for п in (search_fields or "").split(",")}) - {None, ""}


class IntegrationTestПоляБезОчистки(IntegrationTestCase):
	def нарушения(self, поле: dict, поиск: set[str]) -> list[str]:
		беды = []
		if not поле.get("report_hide"):
			беды.append("нет report_hide")
		if поле["fieldtype"] in СЫРЫЕ_В_ПЕЧАТИ and not поле.get("print_hide"):
			беды.append("нет print_hide")
		if поле["fieldname"] in поиск:
			беды.append("title_field или search_fields")
		return беды

	def test_поля_доктайпов_приложения(self):
		найдено = 0
		for схема in _схемы():
			поиск = _поиск(схема.get("title_field"), схема.get("search_fields"))
			for поле in схема["fields"]:
				if поле.get("ignore_xss_filter"):
					найдено += 1
					with self.subTest(f"{схема['name']}.{поле['fieldname']}"):
						self.assertEqual(self.нарушения(поле, поиск), [])
		self.assertTrue(найдено, "полей без очистки нет — проверять нечего")

	def test_поля_приложения_на_чужих_доктайпах(self):
		поля = json.loads((КОРЕНЬ / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))
		найдено = 0
		for поле in поля:
			if поле.get("ignore_xss_filter"):
				найдено += 1
				мета = frappe.get_meta(поле["dt"])
				with self.subTest(поле["name"]):
					self.assertEqual(self.нарушения(поле, _поиск(мета.title_field, мета.search_fields)), [])
		self.assertTrue(найдено, "полей без очистки нет — проверять нечего")

	def test_правило_ловит_нарушения(self):
		"""Проверка проверки: каждое нарушение по отдельности."""
		поле = {"fieldname": "text", "fieldtype": "Small Text", "report_hide": 1, "print_hide": 1}
		self.assertEqual(self.нарушения(поле, set()), [])
		self.assertEqual(self.нарушения({**поле, "report_hide": 0}, set()), ["нет report_hide"])
		self.assertEqual(self.нарушения({**поле, "print_hide": 0}, set()), ["нет print_hide"])
		self.assertEqual(self.нарушения({**поле, "fieldtype": "Data", "print_hide": 0}, set()), [])
		self.assertEqual(self.нарушения(поле, _поиск("text", None)), ["title_field или search_fields"])
		self.assertEqual(
			self.нарушения(поле, _поиск("title", "name, text")), ["title_field или search_fields"]
		)
