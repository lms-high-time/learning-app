# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Поля, которые Frappe хранит мимо очистки HTML, не выходят туда, где Desk
выводит значения сырыми (lms-high-time/learning-services#521).

Мимо очистки идут поля с `ignore_xss_filter`, поля JSON — `sanitize_html`
пропускает значение, которое разбирается как JSON, даже строку в кавычках с
тегом, — и поля, которые код пишет `db.set_value`, мимо `validate`.

`Why:` Desk выводит часть значений без экранирования: Report view и колонки
списка, фильтры, печать — всё, кроме Data и Code, — выпадающий список ссылки
и поиск (`title_field`, `search_fields`). Разметку, записанную автором или
учеником, исполнил бы браузер администратора. Правило — тестом по схемам, а не
вниманием: новое такое поле без скрытия ломает этот тест. Флаги читаются через
`frappe.get_meta` — с Property Setter.
"""

import json
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase

КОРЕНЬ = Path(frappe.get_app_path("lms_frappe_app"))
#: Типы, которые печать выводит экранированными; остальные — сырыми или в обход.
ПЕЧАТЬ_ЭКРАНИРУЕТ = frozenset({"Data", "Code"})
#: Custom Field, которые код пишет `db.set_value`, мимо очистки:
#: `announce_course` (`api.authoring`).
МИМО_ОЧИСТКИ = frozenset({"LMS Course-announce_objectives"})


def _сырое(поле: dict) -> bool:
	return поле["fieldtype"] == "JSON" or bool(поле.get("ignore_xss_filter"))


def _поля_доктайпов() -> list[tuple[str, str]]:
	итог = []
	for путь in sorted(КОРЕНЬ.glob("*/doctype/*/*.json")):
		схема = json.loads(путь.read_text(encoding="utf-8"))
		if схема.get("doctype") == "DocType":
			итог += [(схема["name"], п["fieldname"]) for п in схема["fields"] if _сырое(п)]
	return итог


def _поля_фикстуры() -> list[tuple[str, str]]:
	поля = json.loads((КОРЕНЬ / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))
	return [(п["dt"], п["fieldname"]) for п in поля if _сырое(п) or п["name"] in МИМО_ОЧИСТКИ]


def нарушения(поле, title_field: str | None, search_fields: str | None) -> list[str]:
	"""Чем поле выходит в сырой вывод Desk; `поле` — описание поля из меты."""
	поиск = ({title_field} | {п.strip() for п in (search_fields or "").split(",")}) - {None, ""}
	беды = []
	if not поле.get("report_hide"):
		беды.append("нет report_hide")
	if поле.get("fieldtype") not in ПЕЧАТЬ_ЭКРАНИРУЕТ and not поле.get("print_hide"):
		беды.append("нет print_hide")
	if поле.get("in_list_view"):
		беды.append("in_list_view")
	if поле.get("in_standard_filter"):
		беды.append("in_standard_filter")
	if поле.get("fieldname") in поиск:
		беды.append("title_field или search_fields")
	return беды


class IntegrationTestПоляМимоОчистки(IntegrationTestCase):
	def проверить(self, поля: list[tuple[str, str]]) -> None:
		self.assertTrue(поля, "полей мимо очистки нет — проверять нечего")
		for doctype, имя in поля:
			мета = frappe.get_meta(doctype)
			with self.subTest(f"{doctype}.{имя}"):
				self.assertEqual(нарушения(мета.get_field(имя), мета.title_field, мета.search_fields), [])

	def test_поля_доктайпов_приложения(self):
		self.проверить(_поля_доктайпов())

	def test_поля_приложения_на_чужих_доктайпах(self):
		self.проверить(_поля_фикстуры())

	def test_правило_ловит_нарушения(self):
		"""Проверка проверки: каждое нарушение по отдельности."""
		поле = {"fieldname": "text", "fieldtype": "Small Text", "report_hide": 1, "print_hide": 1}
		self.assertEqual(нарушения(поле, None, None), [])
		self.assertEqual(нарушения({**поле, "report_hide": 0}, None, None), ["нет report_hide"])
		for тип in ("Small Text", "Long Text", "Text", "Text Editor", "Markdown Editor", "JSON"):
			self.assertEqual(
				нарушения({**поле, "fieldtype": тип, "print_hide": 0}, None, None), ["нет print_hide"], тип
			)
		for тип in ПЕЧАТЬ_ЭКРАНИРУЕТ:
			self.assertEqual(нарушения({**поле, "fieldtype": тип, "print_hide": 0}, None, None), [], тип)
		self.assertEqual(нарушения({**поле, "in_list_view": 1}, None, None), ["in_list_view"])
		self.assertEqual(нарушения({**поле, "in_standard_filter": 1}, None, None), ["in_standard_filter"])
		self.assertEqual(нарушения(поле, "text", None), ["title_field или search_fields"])
		self.assertEqual(нарушения(поле, "title", "name, text"), ["title_field или search_fields"])
