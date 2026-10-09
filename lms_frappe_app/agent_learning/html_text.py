# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Текст с началом тега чистится от HTML всегда, а не когда Frappe сочтёт его
разметкой (lms-high-time/learning-services#527).

`Why:` `sanitize_html` Frappe пропускает без очистки два вида текста с тегом:
- значение, которое разбирается как JSON, — `"<img src=x onerror=…>"` в
  кавычках, массив, объект;
- значение, в котором `html.parser` не находит тега, — незакрытый тег вроде
  `<img src=x onerror=alert(1)//` и хвост за ним. Браузер разбирает его
  иначе: в печати, где перевод строки становится `<br>`, это `img` с
  `onerror`.
Ученик, автор, руководитель или проверяющий пишет такой текст через методы
приложения, и тег доходит до базы как есть в любом текстовом поле без
`ignore_xss_filter`. Desk выводит такие поля в расчёте на очистку при записи:
таблица формы показывает Markdown Editor без экранирования, печать — всё,
кроме Data и Code, Report view — всё. Скрипт выполнился бы в браузере
администратора.

Чинится ввод, а не вывод. Хук перед сохранением прогоняет через
`sanitize_html` с `always_sanitize` каждый текст, где есть начало тега
(`НАЧАЛО_ТЕГА`), — JSON он или нет. Закрытие вывода флагами (`report_hide`,
`print_hide`) не годится как общее средство: название курса, ключ заметки,
название организации нужны администратору в Report view и заголовке записи,
а новое поле открывалось бы, пока его не скроют.

Цена — текст с началом тега меняется: опасные теги и атрибуты вырезаются,
незакрытый тег пропадает вместе с хвостом (`x<y и дальше` → `x`), `&`, `<` и
`>` вне тегов становятся сущностями. Текст без начала тега — `a < b`,
`{"rule": "score > 80 & x"}` — хук не трогает, и Frappe пропускает его как
есть.

Охват — доктайпы модуля приложения и доктайпы Learning, в тексты которых пишут
методы приложения (`ДОКТАЙПЫ_LEARNING`). Не чистится то, что не чистит и
Frappe (`ignore_xss_filter`, Code, вложения, почта), поля JSON и
`JSON_СТРОКОЙ`: там JSON — само значение, и очистка его испортила бы. Такие
поля хранятся как есть и закрыты от сырого вывода Desk
(`tests/test_unsanitized_fields`).

Чего не делает:
- не видит записи мимо сохранения документа — `frappe.db.set_value` и
  `db_set`. Текстовые поля, куда так пишется, — в `ТЕКСТ_МИМО_ОЧИСТКИ` того же
  теста;
- не срабатывает при `flags.ignore_validate`: Frappe тогда пропускает и
  `before_save`. Код приложения этот флаг не ставит.
"""

import re

import frappe
from frappe.utils.html_utils import sanitize_html

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
#: Начало тега, комментария или объявления — то, с чего браузер начинает разметку.
НАЧАЛО_ТЕГА = re.compile(r"<[A-Za-z!/?]")


def охвачен(мета) -> bool:
	return мета.name in ДОКТАЙПЫ_LEARNING or мета.module == МОДУЛЬ


def чистится(мета, поле) -> bool:
	"""Текст поля с началом тега хранится очищенным."""
	return охвачен(мета) and not (
		поле.get("ignore_xss_filter")
		or поле.get("is_virtual")
		or поле.fieldtype in НЕ_ЧИСТЯТСЯ
		or (поле.fieldtype in ПОЧТОВЫЕ and поле.options == "Email")
		or (мета.name, поле.fieldname) in JSON_СТРОКОЙ
	)


def очистить(doc, method=None) -> None:
	"""Хук `before_save` всех доктайпов: последний шаг перед очисткой Frappe,
	после `validate` и `before_save` контроллера, которые тоже пишут тексты."""
	if frappe.flags.in_install or not охвачен(doc.meta):
		return
	for запись in (doc, *doc.get_all_children()):
		мета = запись.meta
		if not охвачен(мета):
			continue
		for поле in мета.fields:
			значение = запись.get(поле.fieldname)
			if isinstance(значение, str) and НАЧАЛО_ТЕГА.search(значение) and чистится(мета, поле):
				запись.set(
					поле.fieldname,
					sanitize_html(значение, linkify=поле.fieldtype == "Text Editor", always_sanitize=True),
				)
