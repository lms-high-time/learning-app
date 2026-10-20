# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Схемы урока: HTML-страницы автора в пакете агента (learning-services#644).

Срез урока несёт `diagrams` — `{key, title, description, when?, html}`. Агент
показывает схему окном, модель объясняет по `description`: окно она не видит.
Проверки — те же, что у валидатора компилятора курса (`tools/diagrams.py`
course-design), с теми же кодами и пределами: автор узнаёт о беде до отправки.

`Why:` окно схемы закрывает сеть — внешний шрифт или скрипт не загрузится, и
схема сломается у ученика, а не при публикации. Проверка на сервере, а не только
у компилятора: релиз приходит снаружи, а схему отдаёт ученику этот сервер.
"""

import re

КЛЮЧ = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
#: Байт UTF-8: схема уходит в окно целиком при каждом показе.
ПРЕДЕЛ_HTML = 100_000
НАЗВАНИЕ = 140
#: Поля схемы, которые уходят агенту без HTML — в ответе `start_lesson`.
ПОЛЯ_СПИСКА = ("key", "title", "description", "when")

#: Адрес в сеть: `https://…` или `//…` в кавычках. Протокол-относительный без
#: кавычек не ловится — иначе `data = // комментарий` в JS был бы «ссылкой».
_АДРЕС = r"""(?:["']\s*(?:https?:)?//|https?://)"""
#: Тот же список — у валидатора компилятора (`tools/diagrams.py` course-design);
#: его тест сверяет оба на одних примерах.
_ВНЕШНЕЕ = [
	(
		re.compile(
			r"""(?<![\w-])(?:src|href|action|formaction|poster|data|background|ping|xlink:href)\s*=\s*"""
			+ _АДРЕС
			+ r"""|(?<![\w-])srcset\s*=\s*["'][^"']*(?:https?:)?//""",
			re.I,
		),
		"ссылка на внешний ресурс",
	),
	(re.compile(r"""(?:url|image-set)\(\s*["']?\s*(?:https?:)?//""", re.I), "внешний адрес в CSS"),
	(re.compile(r"@import\b", re.I), "@import в CSS"),
	(re.compile(r"<\s*(?:iframe|object|embed|base|frame)(?![\w-])", re.I), "встроенная страница или <base>"),
	(re.compile(r"""<\s*meta\b[^>]*http-equiv\s*=\s*["']?refresh""", re.I), "переход по <meta refresh>"),
	(
		re.compile(
			r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|Worker|SharedWorker)\s*\("
			r"|\bnew\s+(?:XMLHttpRequest|WebSocket|EventSource)\b"
		),
		"запрос в сеть из скрипта",
	),
	(
		re.compile(r"""\bimport\s*\(?\s*""" + _АДРЕС + r"""|\bfrom\s+""" + _АДРЕС, re.I),
		"импорт модуля по адресу",
	),
]


def внешнее(html: str) -> list[str]:
	"""Что в HTML тянет сеть — по строке на вид."""
	return [что for шаблон, что in _ВНЕШНЕЕ if шаблон.search(html)]


def проблемы(срезы: dict) -> list[dict]:
	"""Проблемы схем пакета агента — `{code, where, …}`, как у `releases.checks`.

	Пакет непрозрачен: схемы сверяются там, где срез — объект, а `diagrams` есть.
	Форма — тоже здесь: публичная схема релиза пакет не описывает, а отдавать
	схему ученику будет этот сервер.
	"""
	найдено: list[dict] = []
	for урок, срез in срезы.items():
		if not isinstance(срез, dict) or "diagrams" not in срез:
			continue
		где = f"agent.lessons.{урок}.diagrams"
		записи = срез["diagrams"]
		if not isinstance(записи, list):
			найдено.append({"code": "diagram_shape", "where": где})
			continue
		видел: set[str] = set()
		for н, схема in enumerate(записи):
			if (
				not isinstance(схема, dict)
				or not isinstance(схема.get("key"), str)
				or not КЛЮЧ.fullmatch(схема["key"])
			):
				найдено.append({"code": "diagram_shape", "where": f"{где}[{н}]"})
				continue
			ключ = схема["key"]
			if ключ in видел:
				найдено.append({"code": "duplicate_key", "where": где, "key": ключ})
			видел.add(ключ)
			найдено.extend({**п, "where": f"{где}[{ключ}]", "key": ключ} for п in _проблемы_схемы(схема))
	return найдено


def _проблемы_схемы(схема: dict) -> list[dict]:
	итог = []
	поля = {п: схема.get(п) for п in ("title", "description", "when", "html")}
	if any(з is not None and not isinstance(з, str) for з in поля.values()) or set(схема) - {"key", *поля}:
		return [{"code": "diagram_shape"}]
	if not (поля["title"] or "").strip():
		итог.append({"code": "diagram_no_title"})
	elif len(поля["title"]) > НАЗВАНИЕ:
		итог.append({"code": "text_too_long", "limit": НАЗВАНИЕ})
	if not (поля["description"] or "").strip():
		итог.append({"code": "diagram_no_description"})
	html = поля["html"] or ""
	if not html.strip():
		итог.append({"code": "diagram_empty"})
	if len(html.encode("utf-8")) > ПРЕДЕЛ_HTML:
		итог.append({"code": "diagram_too_large", "limit": ПРЕДЕЛ_HTML})
	for что in внешнее(html):
		итог.append({"code": "diagram_external", "found": что})
	return итог


def список(пакет: dict) -> list[dict]:
	"""Схемы среза урока без HTML — для старта урока: HTML 8–100 КБ на схему
	вытеснил бы из контекста модели разговор, отдаётся он по ключу."""
	return [{п: схема[п] for п in ПОЛЯ_СПИСКА if схема.get(п)} for схема in _схемы(пакет)]


def схема(пакет: dict, ключ: str) -> dict | None:
	"""Схема среза урока целиком, с HTML; нет — `None`."""
	return next((с for с in _схемы(пакет) if с["key"] == ключ), None)


def _схемы(пакет: dict) -> list[dict]:
	# Форму сверила публикация (`проблемы`); здесь — только то, что ей не грозит.
	записи = пакет.get("diagrams")
	return [с for с in записи if isinstance(с, dict) and с.get("key")] if isinstance(записи, list) else []
