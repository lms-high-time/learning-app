# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Поля, которые Frappe хранит мимо очистки HTML, не выходят туда, где Desk
выводит значения сырыми (lms-high-time/learning-services#521), а остальные
текстовые поля чистятся и от значения, которое разбирается как JSON
(lms-high-time/learning-services#527).

Мимо очистки идут поля с `ignore_xss_filter`, поля JSON и текстовые поля из
`ТЕКСТ_МИМО_ОЧИСТКИ`: в них код пишет JSON строкой или пишет текст
`db.set_value`, мимо `validate`. Прочие текстовые поля доктайпов приложения
чистит Frappe, а значение, которое `sanitize_html` пропускает как JSON, даже
строку в кавычках с тегом, — хук `json_text.очистить`.

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
from bs4 import BeautifulSoup
from frappe.tests import IntegrationTestCase
from frappe.utils.formatters import format_value

from lms_frappe_app.agent_learning import json_text
from lms_frappe_app.api import student
from lms_frappe_app.tests.sample_data import зачислить, создать_урок, создать_ученика

КОРЕНЬ = Path(frappe.get_app_path("lms_frappe_app"))
#: Типы, которые печать выводит экранированными; остальные — сырыми или в обход.
ПЕЧАТЬ_ЭКРАНИРУЕТ = frozenset({"Data", "Code"})
#: Текстовые поля (доктайпа приложения или Custom Field), которые не чистятся,
#: хотя флага у них нет: в них пишется JSON строкой (`json_text.JSON_СТРОКОЙ`)
#: или текст `db.set_value` мимо `validate` — цели анонса (`announce_course`,
#: `api.authoring`).
ТЕКСТ_МИМО_ОЧИСТКИ = json_text.JSON_СТРОКОЙ | {("LMS Course", "announce_objectives")}
#: Типы полей, в которые пишется текст.
ТЕКСТОВЫЕ = frozenset(
	{
		"Data",
		"Small Text",
		"Text",
		"Long Text",
		"Text Editor",
		"Markdown Editor",
		"HTML Editor",
		"Code",
		"JSON",
	}
)


def _сырое(doctype: str, поле: dict) -> bool:
	return (
		поле["fieldtype"] == "JSON"
		or bool(поле.get("ignore_xss_filter"))
		or (doctype, поле["fieldname"]) in ТЕКСТ_МИМО_ОЧИСТКИ
	)


def _схемы() -> list[dict]:
	схемы = [
		json.loads(путь.read_text(encoding="utf-8")) for путь in sorted(КОРЕНЬ.glob("*/doctype/*/*.json"))
	]
	return [схема for схема in схемы if схема.get("doctype") == "DocType"]


def _поля_доктайпов(отбор=_сырое) -> list[tuple[str, str]]:
	return [(с["name"], п["fieldname"]) for с in _схемы() for п in с["fields"] if отбор(с["name"], п)]


def _поля_фикстуры(отбор=_сырое) -> list[tuple[str, str]]:
	поля = json.loads((КОРЕНЬ / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))
	return [(п["dt"], п["fieldname"]) for п in поля if отбор(п["dt"], п)]


def _текстовое(doctype: str, поле: dict) -> bool:
	return поле["fieldtype"] in ТЕКСТОВЫЕ


def нагрузка(doctype: str, поле: str) -> str:
	"""Строка JSON с тегом, исполнимым в браузере; метка в `onerror` — чьё поле."""
	return json.dumps(f"<img src=x onerror={frappe.scrub(doctype)}__{поле}()>")


def исполнимое(html) -> list[str]:
	"""Метки `onerror` из разметки: исполнимый тег, а не экранированный текст."""
	return [тег["onerror"] for тег in BeautifulSoup(str(html or ""), "html.parser").find_all(onerror=True)]


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

	def test_список_текста_мимо_очистки_жив(self):
		"""Каждое поле `ТЕКСТ_МИМО_ОЧИСТКИ` есть в схемах: переименованное поле
		выпало бы из проверки молча."""
		self.assertEqual(ТЕКСТ_МИМО_ОЧИСТКИ - set(_поля_доктайпов()) - set(_поля_фикстуры()), set())

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

	def test_текстовое_поле_чистится_или_хранится_как_есть(self):
		"""Текстовое поле приложения либо чистится и от значения-JSON
		(`json_text.чистится`), либо хранится как есть и скрыто проверками выше:
		третьего, где тег из JSON дошёл бы до Desk сырым, нет."""
		поля = _поля_доктайпов(_текстовое) + _поля_фикстуры(_текстовое)
		for doctype, имя in поля:
			поле = frappe.get_meta(doctype).get_field(имя)
			with self.subTest(f"{doctype}.{имя}"):
				self.assertTrue(json_text.чистится(doctype, поле) or _сырое(doctype, поле.as_dict()))

	def test_json_строка_с_тегом_не_исполнима_в_desk(self):
		"""Каждое текстовое поле охваченных доктайпов и их таблиц получает строку
		JSON с тегом. После очистки при записи тег не исполним ни в значении, ни
		в `format_value` — им Desk форматирует печать и выгрузку отчёта, — а
		печать не выводит его и из полей, что хранятся как есть."""
		доктайпы = [с["name"] for с in _схемы() if not с.get("istable")] + sorted(json_text.ДОКТАЙПЫ_LEARNING)
		for doctype in доктайпы:
			документ = _с_нагрузкой(doctype)
			json_text.очистить(документ)
			for запись in (документ, *документ.get_all_children()):
				запись._sanitize_content()
				for поле in запись.meta.fields:
					if поле.fieldtype not in ТЕКСТОВЫЕ or not json_text.чистится(запись.doctype, поле):
						continue
					with self.subTest(f"{запись.doctype}.{поле.fieldname}"):
						значение = запись.get(поле.fieldname)
						self.assertEqual(исполнимое(значение), [])
						self.assertEqual(исполнимое(format_value(значение, поле, запись)), [])
			with self.subTest(f"печать {doctype}"):
				self.assertEqual(исполнимое(frappe.get_print(doctype, doc=документ)), [])

	def test_markdown_не_в_таблице_формы(self):
		"""Таблица формы экранирует Data и простой текст, а Markdown Editor
		выводит запасным форматтером Data — без экранирования."""
		for схема in _схемы():
			if схема.get("istable"):
				for поле in frappe.get_meta(схема["name"]).fields:
					if поле.fieldtype == "Markdown Editor":
						with self.subTest(f"{схема['name']}.{поле.fieldname}"):
							self.assertFalse(поле.in_list_view)


def _заполнить(запись) -> None:
	"""Собственные поля Learning, которые хранятся как есть (содержание урока
	`content`), — дело Learning: методы приложения в них не пишут."""
	for поле in запись.meta.fields:
		if поле.fieldtype in ТЕКСТОВЫЕ and (
			запись.doctype not in json_text.ДОКТАЙПЫ_LEARNING
			or поле.get("is_custom_field")
			or json_text.чистится(запись.doctype, поле)
		):
			запись.set(поле.fieldname, нагрузка(запись.doctype, поле.fieldname))


def _с_нагрузкой(doctype: str):
	"""Несохранённая запись: строка JSON с тегом в каждом текстовом поле — и в
	строке каждой таблицы, которую чистит хук."""
	документ = frappe.new_doc(doctype)
	_заполнить(документ)
	for таблица in документ.meta.get_table_fields():
		if json_text.охвачен(таблица.options):
			_заполнить(документ.append(таблица.fieldname, {}))
	return документ


class IntegrationTestТекстУченикаКакJSON(IntegrationTestCase):
	"""Строка JSON с тегом через методы ученика хранится очищенной, и печать
	записи её не исполняет — путь целиком, с хуком на сохранении."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"json-text-{суффикс}@example.com")
		self.курс = зачислить(self.ученик, создать_урок(f"Урок {суффикс}"))
		frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"course": self.курс,
				"slug": "summary",
				"title": "Резюме проекта",
				"blocks": [{"block_key": "goal", "title": "Цель"}],
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)

	def проверить(self, doctype: str, name: str) -> None:
		frappe.set_user("Administrator")
		документ = frappe.get_doc(doctype, name)
		for запись in (документ, *документ.get_all_children()):
			for поле in запись.meta.fields:
				if поле.fieldtype in ТЕКСТОВЫЕ:
					self.assertEqual(
						исполнимое(запись.get(поле.fieldname)), [], f"{запись.doctype}.{поле.fieldname}"
					)
		self.assertEqual(исполнимое(frappe.get_print(doctype, name)), [])

	def test_блок_документа(self):
		текст = нагрузка("Agent Artifact Content", "content")
		self.assertTrue(student.update_artifact(self.курс, "summary", "goal", текст)["ok"])

		имя = frappe.db.get_value("Agent Student Artifact", {"student": self.ученик, "artifact": "summary"})
		self.assertTrue(frappe.db.get_value("Agent Artifact Content", {"parent": имя}, "content"))
		self.проверить("Agent Student Artifact", имя)

	def test_заметка(self):
		ключ, текст = нагрузка("Agent Student Note", "note_key"), нагрузка("Agent Student Note", "text")
		ответ = student.remember(kind="fact", key=ключ, text=текст)

		self.assertTrue(ответ["ok"], ответ)
		self.проверить(
			"Agent Student Note", frappe.db.get_value("Agent Student Note", {"student": self.ученик})
		)
