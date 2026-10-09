# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Поля, которые Frappe хранит мимо очистки HTML, не выходят туда, где Desk
выводит значения сырыми (lms-high-time/learning-services#521), а в остальных
текстовых полях текст с тегом хранится очищенным
(lms-high-time/learning-services#527).

Мимо очистки идут поля с `ignore_xss_filter`, поля JSON и текстовые поля из
`ТЕКСТ_МИМО_ОЧИСТКИ`: в них код пишет JSON строкой или пишет текст
`db.set_value`, мимо `validate`. Прочие текстовые поля доктайпов приложения
чистит хук `html_text.очистить` — и тот текст с тегом, что `sanitize_html`
Frappe пропускает: JSON и незакрытый тег.

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

from lms_frappe_app.agent_learning import html_text
from lms_frappe_app.api import student
from lms_frappe_app.tests.sample_data import зачислить, создать_урок, создать_ученика

КОРЕНЬ = Path(frappe.get_app_path("lms_frappe_app"))
#: Типы, которые печать выводит экранированными; остальные — сырыми или в обход.
ПЕЧАТЬ_ЭКРАНИРУЕТ = frozenset({"Data", "Code"})
#: Текстовые поля (доктайпа приложения или Custom Field), которые не чистятся,
#: хотя флага у них нет: в них пишется JSON строкой (`html_text.JSON_СТРОКОЙ`)
#: или текст `db.set_value` мимо `validate` — цели анонса (`announce_course`,
#: `api.authoring`).
ТЕКСТ_МИМО_ОЧИСТКИ = html_text.JSON_СТРОКОЙ | {("LMS Course", "announce_objectives")}
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


#: Текст с тегом, который `sanitize_html` Frappe пропускает без очистки:
#: JSON — закрытый и незакрытый тег — и незакрытый тег с хвостом на новой строке.
НАГРУЗКИ = {
	"json": lambda метка: json.dumps(f"<img src=x onerror={метка}()>"),
	"json, незакрытый": lambda метка: json.dumps(f"<img src=x onerror={метка}()//"),
	"незакрытый": lambda метка: f"<img src=x onerror={метка}()//\nx",
}


def нагрузка(doctype: str, поле: str, вид: str = "json") -> str:
	"""Текст с тегом, исполнимым в браузере; метка в `onerror` — чьё поле."""
	return НАГРУЗКИ[вид](f"{frappe.scrub(doctype)}__{поле}")


def исполнимое(html) -> list[str]:
	"""Метки `onerror` из разметки, разобранной как браузер разбирает ячейку
	таблицы, — `html5lib`: `html.parser` незакрытого тега не видит."""
	разметка = f"<table><tr><td>{html or ''}</td></tr></table>"
	return [тег["onerror"] for тег in BeautifulSoup(разметка, "html5lib").find_all(onerror=True)]


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
		"""Текстовое поле приложения либо хранит текст с тегом очищенным
		(`html_text.чистится`), либо хранит как есть и скрыто проверками выше:
		третьего, где тег дошёл бы до Desk сырым, нет."""
		поля = _поля_доктайпов(_текстовое) + _поля_фикстуры(_текстовое)
		for doctype, имя in поля:
			мета = frappe.get_meta(doctype)
			поле = мета.get_field(имя)
			with self.subTest(f"{doctype}.{имя}"):
				self.assertTrue(html_text.чистится(мета, поле) or _сырое(doctype, поле.as_dict()))

	def test_json_строка_с_тегом_не_исполнима_в_desk(self):
		"""Каждое текстовое поле охваченных доктайпов и их таблиц получает текст
		с тегом каждого вида из `НАГРУЗКИ`. После очистки при записи тег не
		исполним ни в значении, ни в `format_value` — им Desk форматирует печать
		и выгрузку отчёта, — а печать не выводит его и из полей, что хранятся
		как есть."""
		доктайпы = [с["name"] for с in _схемы() if not с.get("istable")] + sorted(html_text.ДОКТАЙПЫ_LEARNING)
		for вид in НАГРУЗКИ:
			for doctype in доктайпы:
				документ = _с_нагрузкой(doctype, вид)
				html_text.очистить(документ)
				for запись in (документ, *документ.get_all_children()):
					запись._sanitize_content()
					for поле in запись.meta.fields:
						if поле.fieldtype not in ТЕКСТОВЫЕ or not html_text.чистится(запись.meta, поле):
							continue
						with self.subTest(f"{вид}: {запись.doctype}.{поле.fieldname}"):
							значение = запись.get(поле.fieldname)
							self.assertEqual(исполнимое(значение), [])
							self.assertEqual(исполнимое(format_value(значение, поле, запись)), [])
				with self.subTest(f"{вид}: печать {doctype}"):
					self.assertEqual(исполнимое(frappe.get_print(doctype, doc=документ)), [])

	def test_текст_без_тега_не_меняется(self):
		"""Угловые скобки и `&` без начала тега — сравнение, правило в JSON —
		хранятся как написаны: Frappe их не трогает, и хук тоже."""
		for значение in ('"a < b"', '{"rule":"score > 80 & x"}', "a < b & c > d", "5<6"):
			документ = frappe.new_doc("Agent Student Note")
			документ.text = значение
			html_text.очистить(документ)
			документ._sanitize_content()
			self.assertEqual(документ.text, значение)

	def test_скобка_без_тега_экранируется_хвост_цел(self):
		"""`<`, с которого тег не сложился, становится `&lt;` — в обычном тексте
		и в JSON; слова после него и переводы строк на месте. Разметка
		чистится, повторное сохранение значения не меняет."""
		for значение, ожидаемое in (
			("если x<y, то z", "если x&lt;y, то z"),
			("List<String> items", "List&lt;String> items"),
			("<img src=x onerror=a()//\nx", "&lt;img src=x onerror=a()//\nx"),
			('"x<y и дальше"', '"x&lt;y и дальше"'),
			('["List<String> items"]', '["List&lt;String> items"]'),
			('"<img src=x onerror=a()>"', '"<img src="x">"'),
			("<b>жирно</b> и x<y & z", "<b>жирно</b> и x&lt;y &amp; z"),
			("<img src=x onerror=a()//<br>хвост", "&lt;img src=x onerror=a()//<br>хвост"),
		):
			with self.subTest(значение):
				документ = frappe.new_doc("Agent Student Note")
				документ.text = значение
				for _ in range(2):
					html_text.очистить(документ)
					документ._sanitize_content()
					self.assertEqual(документ.text, ожидаемое)
				self.assertEqual(html_text.очищенный(значение), ожидаемое)

	def test_markdown_не_в_таблице_формы(self):
		"""Таблица формы экранирует Data и простой текст, а Markdown Editor
		выводит запасным форматтером Data — без экранирования."""
		for схема in _схемы():
			if схема.get("istable"):
				for поле in frappe.get_meta(схема["name"]).fields:
					if поле.fieldtype == "Markdown Editor":
						with self.subTest(f"{схема['name']}.{поле.fieldname}"):
							self.assertFalse(поле.in_list_view)


def _заполнить(запись, вид: str) -> None:
	"""Собственные поля Learning, которые хранятся как есть (содержание урока
	`content`), — дело Learning: методы приложения в них не пишут."""
	for поле in запись.meta.fields:
		if поле.fieldtype in ТЕКСТОВЫЕ and (
			запись.doctype not in html_text.ДОКТАЙПЫ_LEARNING
			or поле.get("is_custom_field")
			or html_text.чистится(запись.meta, поле)
		):
			запись.set(поле.fieldname, нагрузка(запись.doctype, поле.fieldname, вид))


def _с_нагрузкой(doctype: str, вид: str):
	"""Несохранённая запись: текст с тегом в каждом текстовом поле — и в
	строке каждой таблицы, которую чистит хук."""
	документ = frappe.new_doc(doctype)
	_заполнить(документ, вид)
	for таблица in документ.meta.get_table_fields():
		if html_text.охвачен(frappe.get_meta(таблица.options)):
			_заполнить(документ.append(таблица.fieldname, {}), вид)
	return документ


class IntegrationTestТекстУченикаКакJSON(IntegrationTestCase):
	"""Текст с тегом через методы ученика хранится очищенным, и печать записи
	его не исполняет — путь целиком, с хуком на сохранении."""

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
		текст = нагрузка("Agent Artifact Content", "content", "незакрытый")
		self.assertTrue(student.update_artifact(self.курс, "summary", "goal", текст)["ok"])

		имя = frappe.db.get_value("Agent Student Artifact", {"student": self.ученик, "artifact": "summary"})
		сохранено = frappe.db.get_value("Agent Artifact Content", {"parent": имя}, "content")
		self.assertEqual(сохранено, "&lt;" + текст[1:])
		self.проверить("Agent Student Artifact", имя)

	def test_заметка(self):
		ключ = нагрузка("Agent Student Note", "note_key")
		текст = нагрузка("Agent Student Note", "text", "незакрытый")
		ответ = student.remember(kind="fact", key=ключ, text=текст)

		self.assertTrue(ответ["ok"], ответ)
		имя = frappe.db.get_value("Agent Student Note", {"student": self.ученик})
		self.assertEqual(frappe.db.get_value("Agent Student Note", имя, "text"), "&lt;" + текст[1:])
		self.проверить("Agent Student Note", имя)

	def test_ключ_заметки_со_скобкой_находится(self):
		"""Повторная запись по ключу со скобкой замещает заметку, а `forget` её
		находит: ключ запроса приводится так же, как сохранённый."""
		# `<` с кириллицей тега не начинает — и в браузере тоже: имя тега латинское.
		for ключ, сохранён in (("цена < 100", "цена < 100"), ("цена<сто", "цена<сто"), ("x<y", "x&lt;y")):
			with self.subTest(ключ):
				frappe.set_user(self.ученик)
				student.remember(kind="fact", key=ключ, text="первая")
				student.remember(kind="fact", key=ключ, text="вторая")

				записи = frappe.get_all("Agent Student Note", {"student": self.ученик}, ["note_key", "text"])
				self.assertEqual([(з.note_key, з.text) for з in записи], [(сохранён, "вторая")])
				self.assertTrue(student.forget(key=ключ)["ok"])
				self.assertFalse(frappe.db.exists("Agent Student Note", {"student": self.ученик}))
