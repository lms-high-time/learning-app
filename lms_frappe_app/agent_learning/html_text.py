# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Текст с началом тега хранится без исполнимого: разметка чистится, а `<`,
с которого тег не сложился, экранируется (lms-high-time/learning-services#527).

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

Чинится ввод, а не вывод. Закрытие вывода флагами (`report_hide`,
`print_hide`) не годится как общее средство: название курса, ключ заметки,
название организации нужны администратору в Report view и заголовке записи,
а новое поле открывалось бы, пока его не скроют.

Хук перед сохранением берёт текст, где есть начало тега (`НАЧАЛО_ТЕГА`), —
JSON он или нет — и приводит его `очищенный`:
- `<`, с которого не начинается разметка, становится `&lt;`: незакрытый тег
  (`<img src=x onerror=a()//`), сравнение (`x<y`), тип (`List<String>`).
  Разметка — закрытый тег, который очистка Frappe оставляет или вырезает с
  содержимым (`ТЕГИ`), и комментарий. Экранирование — до очистки: на
  незакрытом теге `sanitize_html` отрезал бы весь хвост текста;
- если разметка есть, текст проходит `sanitize_html` с `always_sanitize`:
  опасные теги и атрибуты вырезаются, `&`, `<` и `>` вне тегов становятся
  сущностями.
После этого `<` в тексте начинает только тег, оставленный очисткой, а
`&lt;` тега не начинает: повторное сохранение текст не меняет. Текст без
начала тега — `a < b`, `5<6`, `{"rule": "score > 80 & x"}` — хук не трогает,
и Frappe пропускает его как есть. Ключ, по которому запись ищут, приводится
тем же `очищенный` — иначе поиск промахнулся бы мимо сохранённого.

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
from frappe.utils.html_utils import acceptable_elements, mathml_elements, sanitize_html, svg_elements

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
#: Закрытый тег — имя в группе — или комментарий.
РАЗМЕТКА = re.compile(r"<!--.*?-->|</?([A-Za-z][\w:-]*)(?=[\s/>])[^<>]*>", re.S)
#: Теги, которые `sanitize_html` оставляет, и те, что ammonia вырезает с
#: содержимым. Прочее имя — не разметка: `List<String>` остаётся текстом.
ТЕГИ = frozenset(
	acceptable_elements | svg_elements | mathml_elements | {"html", "head", "meta", "link", "body", "o:p"}
) | {"script", "style"}


def очищенный(текст: str, linkify: bool = False) -> str:
	"""Текст, каким его сохранит хук: без `<`, с которого не начинается
	разметка, и с очищенной разметкой."""
	части, разметка, место = [], False, 0
	while начало := НАЧАЛО_ТЕГА.search(текст, место):
		части.append(текст[место : начало.start()])
		тег = РАЗМЕТКА.match(текст, начало.start())
		if тег and (тег.group(1) is None or тег.group(1).lower() in ТЕГИ):
			части.append(тег.group())
			разметка, место = True, тег.end()
		else:
			части.append("&lt;")
			место = начало.start() + 1
	части.append(текст[место:])
	итог = "".join(части)
	return sanitize_html(итог, linkify=linkify, always_sanitize=True) if разметка else итог


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
				запись.set(поле.fieldname, очищенный(значение, linkify=поле.fieldtype == "Text Editor"))
