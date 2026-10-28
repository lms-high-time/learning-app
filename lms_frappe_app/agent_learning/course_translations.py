# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Карточка курса на других языках — поле `LMS Course.course_translations`
(learning-services#738).

Форма — `{"<язык>": {"title", "short_introduction", "description",
"objectives": [str, ...]}}`, любого ключа может не быть. Русский — основные
поля курса (`title`, `short_introduction`, `description`, цели анонса), в
переводах он не читается. Пишут поле публикация релиза (`releases.service`,
из `course.translations` и `chapters[].translations` релиза) и авторские
методы с `lang` (`update_course`, `announce_course`) у курса без релиза; правку
мимо них отклоняет `releases.course_guard`.

Тексты переводов хранятся так же, как русские поля карточки: текст с началом
тега — очищенным (`html_text.очищенный`), прочий — как есть. `Why:` поле JSON
хук очистки не трогает (`html_text.НЕ_ЧИСТЯТСЯ`), и `announce_course` пишет
мимо сохранения документа, поэтому чистит запись, а не хук. `description` —
markdown, как у русского.
"""

import json

import frappe

from lms_frappe_app.agent_learning import html_text

ПОЛЕ = "course_translations"
#: Язык основных полей курса: перевода на него нет.
ОСНОВНОЙ = "ru"
#: Тексты карточки в переводе — поля курса Learning с тем же именем.
ТЕКСТЫ = ("title", "short_introduction", "description")
ЦЕЛИ = "objectives"


def разобрать(значение) -> dict:
	"""Переводы из значения поля: строка JSON или объект. Пусто или не объект — `{}`."""
	if isinstance(значение, str):
		try:
			значение = json.loads(значение) if значение.strip() else None
		except ValueError:
			return {}
	if not isinstance(значение, dict):
		return {}
	return {язык: тексты for язык, тексты in значение.items() if isinstance(тексты, dict)}


def в_json(переводы: dict) -> str:
	"""Значение поля: языки без текстов отброшены, тексты с началом тега очищены."""
	return json.dumps(
		{язык: _очищенные(тексты) for язык, тексты in переводы.items() if тексты},
		ensure_ascii=False,
	)


def _очищенные(тексты: dict) -> dict:
	def очистить(текст):
		if isinstance(текст, str) and html_text.НАЧАЛО_ТЕГА.search(текст):
			return html_text.очищенный(текст)
		return текст

	return {
		поле: [очистить(цель) for цель in значение] if isinstance(значение, list) else очистить(значение)
		for поле, значение in тексты.items()
	}


def перевод(значение, язык: str | None) -> dict:
	"""Тексты карточки на языке `язык` из значения поля; на основном — `{}`."""
	if not язык or язык == ОСНОВНОЙ:
		return {}
	return разобрать(значение).get(язык) or {}


def язык_запроса() -> str:
	"""Язык запроса Learning (`frappe.local.lang`: кука гостя или язык профиля)
	без варианта: `en-US` → `en`."""
	return (getattr(frappe.local, "lang", None) or ОСНОВНОЙ).split("-")[0].lower()
