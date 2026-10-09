# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Текст, который разбирается как JSON, чистится от HTML так же, как любой
другой (lms-high-time/learning-services#527).

`Why:` `sanitize_html` Frappe пропускает без очистки значение, которое
разбирается как JSON: строку в кавычках, массив, объект. Ученик, автор,
руководитель или проверяющий пишет через методы приложения
`"<img src=x onerror=…>"` в кавычках — и тег доходит до базы как есть в любом
текстовом поле без `ignore_xss_filter`. Desk выводит такие поля в расчёте на
очистку при записи: таблица формы показывает Markdown Editor без
экранирования, печать — всё, кроме Data и Code, Report view — всё. Скрипт
выполнился бы в браузере администратора.

Чинится ввод, а не вывод. Хук перед сохранением прогоняет JSON-текст через
тот же `sanitize_html` с `always_sanitize`, и поле без `ignore_xss_filter`
снова хранит очищенный HTML — на это рассчитан каждый вывод Desk. Закрытие
вывода флагами (`report_hide`, `print_hide`) не годится как общее средство:
название курса, ключ заметки, название организации нужны администратору в
Report view и заголовке записи, а новое поле открывалось бы, пока его не
скроют. Текст меняется ровно так, как Frappe поменял бы тот же текст без
кавычек.

Охват — доктайпы модуля приложения и доктайпы Learning, в тексты которых пишут
методы приложения (`ДОКТАЙПЫ_LEARNING`). Не чистится то, что не чистит и
Frappe (`ignore_xss_filter`, Code, вложения, почта), поля JSON и
`JSON_СТРОКОЙ`: там JSON — само значение, и очистка его испортила бы. Такие
поля хранятся как есть и закрыты от сырого вывода Desk
(`tests/test_unsanitized_fields`).

Чего не делает: не видит записи мимо сохранения документа —
`frappe.db.set_value` и `db_set`. Текстовые поля, куда так пишется, — в
`ТЕКСТ_МИМО_ОЧИСТКИ` того же теста.
"""

import frappe
from frappe.utils.html_utils import is_json, sanitize_html

МОДУЛЬ = "Agent Learning"
#: Доктайпы Learning, в тексты которых пишут методы приложения: карточку
#: курса — `create_course` и `update_course`, главы и уроки — публикация релиза.
ДОКТАЙПЫ_LEARNING = frozenset({"LMS Course", "Course Chapter", "Course Lesson"})
#: Текстовые поля, где код хранит JSON строкой: снимок релиза, состояние
#: разговора и сценария.
JSON_СТРОКОЙ = frozenset(
	{
		("Agent Course Release", "snapshot"),
		("Agent Chat State", "state"),
		("Agent Scenario State", "state"),
	}
)
#: Типы, значение которых Frappe не чистит (`BaseDocument._sanitize_content`), и JSON.
НЕ_ЧИСТЯТСЯ = frozenset({"Attach", "Attach Image", "Barcode", "Code", "JSON"})
ПОЧТОВЫЕ = frozenset({"Data", "Small Text", "Text"})


def охвачен(doctype: str) -> bool:
	return doctype in ДОКТАЙПЫ_LEARNING or frappe.get_meta(doctype).module == МОДУЛЬ


def чистится(doctype: str, поле) -> bool:
	"""Поле хранит очищенный HTML, даже если значение разбирается как JSON."""
	return охвачен(doctype) and not (
		поле.get("ignore_xss_filter")
		or поле.fieldtype in НЕ_ЧИСТЯТСЯ
		or (поле.fieldtype in ПОЧТОВЫЕ and поле.options == "Email")
		or (doctype, поле.fieldname) in JSON_СТРОКОЙ
	)


def очистить(doc, method=None) -> None:
	"""Хук `before_save` всех доктайпов: последний шаг перед очисткой Frappe,
	после `validate` и `before_save` контроллера, которые тоже пишут тексты."""
	if frappe.flags.in_install or not охвачен(doc.doctype):
		return
	for запись in (doc, *doc.get_all_children()):
		for поле in запись.meta.fields:
			значение = запись.get(поле.fieldname)
			if (
				isinstance(значение, str)
				and ("<" in значение or ">" in значение)
				and not поле.get("is_virtual")
				and чистится(запись.doctype, поле)
				and is_json(значение)
			):
				запись.set(
					поле.fieldname,
					sanitize_html(значение, linkify=поле.fieldtype == "Text Editor", always_sanitize=True),
				)
