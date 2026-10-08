# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Разбор материала урока Frappe Learning для кабинета автора: страница урока
по частям и `body_segments` в `course_draft`.

`Course Lesson` хранит материал в двух видах: `content` — блоки EditorJS в
JSON, `body` — markdown с макросами вида ``{{ YouTubeVideo(abcd) }}``. Разбор
даёт связный markdown, порезанный на части, и отдельный список медиа.

Разбор **не падает на неожиданном содержимом**: незнакомый блок пропускается,
битый JSON считается пустым контентом. Так же поступает и сам Frappe Learning —
урок, отредактированный из Desk, законно содержит не-JSON.

Места урока вне текста — квиз и упражнение — остаются **структурными
маркерами** вида ``[[lms:quiz]]`` и ``[[lms:exercise:Название]]``: вид места и
его положение в материале.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import unescape

#: Формат макросов Frappe Learning: {{ YouTubeVideo(abcd1234) }}
МАКРОС = re.compile(r"{{ *(\w+)\(([^{}]*)\) *}}")

#: Больше этого часть урока не растёт — запасное значение для настройки
#: `lesson_segment_limit`.
ПРЕДЕЛ_СЕГМЕНТА = 6000

ВИДЕО_РАСШИРЕНИЯ = {"mp4", "webm", "ogg", "mov"}
ВИДЕО_СЕРВИСЫ = {"youtube", "vimeo", "cloudflareStream", "bunnyStream"}

#: Вид места урока вне текста. Значение уходит в маркер: переименование
#: здесь ломает его разбор.
КВИЗ = "quiz"
УПРАЖНЕНИЕ = "exercise"

#: Что в названии маркера ломает его разбор: скобка закрывает маркер раньше
#: времени, перевод строки разрывает абзац.
НЕ_В_МАРКЕРЕ = re.compile(r"[\[\]\r\n]+")


@dataclass(frozen=True)
class МедиаВложение:
	"""Ссылка на медиа урока, вынесенное из текста."""

	kind: str  # video | image | file
	url: str
	title: str | None = None


@dataclass
class НормализованныйУрок:
	title: str
	segments: list[str] = field(default_factory=list)
	media: list[МедиаВложение] = field(default_factory=list)

	@property
	def total_segments(self) -> int:
		return len(self.segments)

	def сегмент(self, индекс: int) -> str:
		"""Сегмент по номеру, считая с единицы."""
		if not self.segments:
			return ""
		индекс = max(1, min(индекс, self.total_segments))
		return self.segments[индекс - 1]


def предел_сегмента() -> int:
	"""Размер сегмента из настроек платформы; при пустой настройке — запасной.

	Настройки читаются отложенным импортом: разбор материала от Frappe не
	зависит, и модуль остаётся пригодным к чтению без базы.
	"""
	from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
		настройка,
	)

	return настройка("lesson_segment_limit", ПРЕДЕЛ_СЕГМЕНТА)


def нормализовать_урок(lesson: str) -> НормализованныйУрок:
	"""Читает урок из базы и разбирает его (`нормализовать`)."""
	import frappe

	запись = frappe.db.get_value(
		"Course Lesson", lesson, ["title", "content", "body"], as_dict=True
	)
	if not запись:
		frappe.throw(frappe._("Урок не найден"), frappe.DoesNotExistError)
	return нормализовать(
		title=запись.title,
		content=запись.content,
		body=запись.body,
		предел=предел_сегмента(),
	)


def нормализовать(
	*, title: str, content: str | None = None, body: str | None = None,
	предел: int = ПРЕДЕЛ_СЕГМЕНТА,
) -> НормализованныйУрок:
	"""Разбирает материал урока: связный текст по частям и медиа."""
	блоки = _блоки_editorjs(content)
	if блоки:
		текст, медиа = _из_блоков(блоки)
	else:
		текст, медиа = _из_markdown(body or "")

	return НормализованныйУрок(
		title=title, segments=_сегментировать(текст, предел), media=медиа
	)


def _очистить(текст: object) -> str:
	"""Текст без html-разметки и лишних пробелов.

	EditorJS хранит абзацы с тегами (`<b>`, `<a href>`, `&nbsp;`). Агенту
	нужен читаемый текст: разметку он в лучшем случае зачитает вслух.
	"""
	if not текст:
		return ""
	без_тегов = re.sub(r"<[^>]+>", "", str(текст))
	return unescape(без_тегов).strip()


def маркер(вид: str, название: str = "") -> str:
	"""Отметка места урока вне текста: вид и название, а не готовая фраза.

	Название очищается от того, что ломает разбор маркера, — иначе название из
	урока разорвало бы его на части.
	"""
	чистое = НЕ_В_МАРКЕРЕ.sub(" ", название or "").strip()
	return f"[[lms:{вид}:{чистое}]]" if чистое else f"[[lms:{вид}]]"


# --- EditorJS ---


def _блоки_editorjs(content: str | None) -> list[dict]:
	"""Блоки из JSON, либо пустой список.

	Читать `content` можно только так: поле законно содержит не-JSON, и разбор
	не должен ронять выдачу урока.
	"""
	try:
		данные = json.loads(content or "")
	except (TypeError, ValueError):
		return []
	if not isinstance(данные, dict):
		return []
	блоки = данные.get("blocks")
	if not isinstance(блоки, list):
		return []
	return [б for б in блоки if isinstance(б, dict) and isinstance(б.get("data", {}), dict)]


def _из_блоков(блоки: list[dict]) -> tuple[str, list[МедиаВложение]]:
	куски: list[str] = []
	медиа: list[МедиаВложение] = []

	for блок in блоки:
		тип = блок.get("type")
		данные = блок.get("data") or {}

		if тип == "paragraph":
			if текст := _очистить(данные.get("text")):
				куски.append(текст)
		elif тип == "header":
			уровень = min(int(данные.get("level") or 2), 6)
			if текст := _очистить(данные.get("text")):
				куски.append(f"{'#' * уровень} {текст}")
		elif тип == "list":
			куски.append(_список(данные))
		elif тип == "code":
			куски.append(f"```\n{данные.get('code', '')}\n```")
		elif тип == "quote":
			if текст := _очистить(данные.get("text")):
				куски.append(f"> {текст}")
		elif тип in ("upload", "image"):
			вложение = _вложение(данные)
			if вложение:
				медиа.append(вложение)
				куски.append(_ссылка_на_медиа(вложение))
		elif тип == "embed":
			вложение = _встроенное(данные)
			if вложение:
				медиа.append(вложение)
				куски.append(_ссылка_на_медиа(вложение))
		elif тип == "quiz":
			# Ни идентификатора квиза, ни вопросов в маркере нет: только вид
			# места и его положение в материале.
			куски.append(маркер(КВИЗ))
		# Незнакомый блок пропускаем молча: состав блоков задаёт Frappe
		# Learning и меняет его без нашего ведома.

	return "\n\n".join(к for к in куски if к.strip()), медиа


def _список(данные: dict) -> str:
	пункты = данные.get("items") or []
	нумерованный = данные.get("style") == "ordered"
	строки = []
	for номер, пункт in enumerate(пункты, start=1):
		# EditorJS хранит пункт то строкой, то словарём с вложенностью.
		текст = пункт.get("content") if isinstance(пункт, dict) else пункт
		if текст := _очистить(текст):
			строки.append(f"{номер}. {текст}" if нумерованный else f"- {текст}")
	return "\n".join(строки)


def _вложение(данные: dict) -> МедиаВложение | None:
	файл = данные.get("file") or {}
	url = данные.get("file_url") or файл.get("url")
	if not url:
		return None
	расширение = (данные.get("file_type") or url.rsplit(".", 1)[-1] or "").lower()
	вид = "video" if расширение in ВИДЕО_РАСШИРЕНИЯ else "image" if расширение in {"png", "jpg", "jpeg", "gif", "webp"} else "file"
	return МедиаВложение(kind=вид, url=url, title=_очистить(данные.get("caption")))


def _встроенное(данные: dict) -> МедиаВложение | None:
	url = данные.get("source") or данные.get("embed")
	if not url:
		return None
	сервис = данные.get("service")
	вид = "video" if сервис in ВИДЕО_СЕРВИСЫ else "file"
	return МедиаВложение(kind=вид, url=url, title=_очистить(данные.get("caption")))


def _ссылка_на_медиа(вложение: МедиаВложение) -> str:
	название = вложение.title or {"video": "Видео", "image": "Изображение"}.get(
		вложение.kind, "Материал"
	)
	return f"[{название}]({вложение.url})"


# --- markdown с макросами ---


def _из_markdown(body: str) -> tuple[str, list[МедиаВложение]]:
	"""Вырезает макросы, превращая известные в медиа.

	Оставлять макрос в тексте нельзя: он ушёл бы на страницу как есть,
	``{{ YouTubeVideo(abcd) }}``.
	"""
	медиа: list[МедиаВложение] = []

	def заменить(совпадение: re.Match) -> str:
		имя, аргумент = совпадение.group(1), совпадение.group(2).strip().strip("\"'")
		if имя == "YouTubeVideo" and аргумент:
			вложение = МедиаВложение(
				kind="video", url=f"https://www.youtube.com/watch?v={аргумент}", title="Видео"
			)
			медиа.append(вложение)
			return _ссылка_на_медиа(вложение)
		if имя == "Quiz":
			return маркер(КВИЗ)
		if имя == "Exercise":
			return маркер(УПРАЖНЕНИЕ, аргумент)
		return ""

	return МАКРОС.sub(заменить, body).strip(), медиа


# --- сегментация ---


def _сегментировать(текст: str, предел: int) -> list[str]:
	"""Режет материал по заголовкам, не разрывая абзацы.

	Порядок важен: сначала пробуем резать по заголовкам — так сегмент остаётся
	осмысленной частью урока. Если между заголовками всё равно слишком много,
	добираем по абзацам.
	"""
	текст = текст.strip()
	if not текст:
		return []
	if len(текст) <= предел:
		return [текст]

	сегменты: list[str] = []
	текущий: list[str] = []
	длина = 0

	for абзац in текст.split("\n\n"):
		заголовок = абзац.lstrip().startswith("#")
		если_переполнен = длина + len(абзац) + 2 > предел
		if текущий and (заголовок and длина > предел // 2 or если_переполнен):
			сегменты.append("\n\n".join(текущий))
			текущий, длина = [], 0
		текущий.append(абзац)
		длина += len(абзац) + 2

	if текущий:
		сегменты.append("\n\n".join(текущий))
	return сегменты
