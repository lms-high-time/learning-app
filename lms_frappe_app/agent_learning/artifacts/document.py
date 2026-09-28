# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Документ ученика по схеме курса: содержимое, заполненность, блоки наружу.

Документ один на «ученик + курс + схема + пространство» и заводится при
первой записи; до неё чтение отдаёт пустые блоки схемы с подсказками автора.
Права — у вызывающего: методы API проверяют доступ к курсу и пространство до
чтения.
"""

from __future__ import annotations

import frappe

from lms_frappe_app.agent_learning.artifacts import canvas, data, export, files, fill, schema
from lms_frappe_app.agent_learning.artifacts.course import _действующая_схема, _схемы_курса


def _экземпляр(ученик: str, course: str, artifact: str, пространство: str | None):
	"""Документ ученика по этой схеме в пространстве (`None` — личное), если он уже заполнялся."""
	имя = frappe.db.get_value(
		"Agent Student Artifact",
		{
			"student": ученик,
			"course": course,
			"artifact": artifact,
			"organization": пространство or ("is", "not set"),
		},
	)
	return frappe.get_doc("Agent Student Artifact", имя) if имя else None


def _содержимое(экземпляр) -> dict[str, str]:
	if not экземпляр:
		return {}
	return {строка.block_key: строка.content or "" for строка in экземпляр.blocks}


def _данные(экземпляр) -> dict:
	"""Строки таблиц и значения полей документа ученика (#330)."""
	return data.данные(экземпляр.data if экземпляр else None)


def _вложения(экземпляр) -> dict[str, dict]:
	"""Файл, ссылка и срез блоков документа ученика — по ключу блока."""
	if not экземпляр:
		return {}
	return {
		строка.block_key: {"file": строка.file, "url": строка.url, "preview": строка.preview}
		for строка in экземпляр.blocks
		if строка.file or строка.url
	}


def _заполненность(
	схема, содержимое: dict[str, str], вложения: dict | None = None, данные: dict | None = None
) -> dict:
	"""Заполнен блок, где есть текст, файл или ссылка; блок с полями и
	колонками — по их правилам (`fill.заполнен`)."""
	вложения = вложения or {}
	данные = данные or data.данные(None)
	return {
		"blocks_total": len(схема.blocks),
		"blocks_filled": sum(
			1 for блок in схема.blocks if _блок_заполнен(схема, блок, содержимое, вложения, данные)
		),
	}


def _блок_заполнен(схема, блок, содержимое: dict, вложения: dict, данные: dict) -> bool:
	текст = содержимое.get(блок.block_key, "")
	по_таблице = fill.заполнен(блок, данные, схема.blocks, текст)
	if по_таблице is not None:
		return по_таблице
	return bool(текст.strip() or блок.block_key in вложения)


def _блок(
	блок,
	содержимое: dict[str, str],
	вложения: dict | None = None,
	файлы: dict | None = None,
	схема=None,
	данные: dict | None = None,
) -> dict:
	"""Блок схемы с содержимым ученика; пустой — с подсказкой автора.

	`kind` — вид блока: `text`, `file` или `link`. У файла — сведения о нём и
	срез таблицы (`preview`), по которому агент сверяет готовность, не открывая
	файл (#315).
	"""
	вложение = (вложения or {}).get(блок.block_key) or {}
	итог = {
		"key": блок.block_key,
		"title": блок.title,
		# Строкой, а не null: пустую подсказку агент и страница проверяют
		# одинаково с непустой, без второй ветки на «нет значения».
		"hint": блок.hint or "",
		"lesson": блок.lesson or None,
		"span": блок.span or 1,
		"kind": files.вид(блок),
		"accept": files.допустимые(блок),
		"content": содержимое.get(блок.block_key, ""),
		"file": (файлы or {}).get(вложение.get("file")) if вложение.get("file") else None,
		"url": вложение.get("url") or None,
		"preview": вложение.get("preview") or None,
	}
	спек = schema.спек(блок)
	if спек and схема is not None:
		# Поля со значениями и колонки блока; строки таблицы — в `tables`
		# документа, у блока — только чего ему не хватает (#330). Формулы полей
		# — посчитанными: их значение не хранится (#351).
		данные = данные or data.данные(None)
		значения = data.поля_документа(схема.blocks, данные)
		итог["fields"] = [{**поле, "value": значения.get(поле["key"])} for поле in спек.get("fields", [])]
		итог["table"] = спек.get("table")
		итог["columns"] = спек.get("columns", [])
		итог["filled"] = _блок_заполнен(схема, блок, содержимое, вложения or {}, данные)
		итог["empty_cells"] = fill.пустые_клетки(блок, данные, схема.blocks)
	return итог


def _заполнен(блок: dict) -> bool:
	if "filled" in блок:
		return блок["filled"]
	return bool(блок["content"].strip() or блок["file"] or блок["url"])


def _содержимое_курса(
	ученик: str, course: str, пространство: str | None
) -> tuple[dict[str, dict[str, str]], dict[str, dict], dict[str, dict]]:
	"""Содержимое и вложения всех документов ученика по курсу — по ключу документа.

	`Why:` перечень и блоки урока спрашивали документ ученика отдельно на
	каждую схему, а каждый такой вопрос стоил трёх обходов базы.
	"""
	записи = frappe.get_all(
		"Agent Student Artifact",
		filters={
			"student": ученик,
			"course": course,
			"organization": пространство or ("is", "not set"),
		},
		fields=["name", "artifact", "data"],
	)
	экземпляры = {запись.name: запись.artifact for запись in записи}
	данные = {запись.artifact: data.данные(запись.data) for запись in записи}
	if not экземпляры:
		return {}, {}, {}

	содержимое: dict[str, dict[str, str]] = {}
	вложения: dict[str, dict] = {}
	for строка in frappe.get_all(
		"Agent Artifact Content",
		filters={"parent": ("in", list(экземпляры)), "parenttype": "Agent Student Artifact"},
		fields=["parent", "block_key", "content", "file", "url", "preview"],
	):
		документ = экземпляры[строка.parent]
		содержимое.setdefault(документ, {})[строка.block_key] = строка.content or ""
		if строка.file or строка.url:
			вложения.setdefault(документ, {})[строка.block_key] = {
				"file": строка.file,
				"url": строка.url,
				"preview": строка.preview,
			}
	return содержимое, вложения, данные


def _файлы(вложения: dict) -> dict[str, dict]:
	"""Сведения о файлах блоков — одним запросом на вызов."""
	return files.сведения_о_файлах(
		[в["file"] for в in вложения.values() if в.get("file")]
	)


def _перечень_артефактов(ученик: str, course: str, пространство: str | None) -> list[dict]:
	по_документам, вложения, данные = _содержимое_курса(ученик, course, пространство)
	перечень = []
	for схема in _схемы_курса(course):
		перечень.append(
			{
				"artifact": схема.slug,
				"title": схема.title,
				"layout": схема.layout,
				**_заполненность(
					схема,
					по_документам.get(схема.slug, {}),
					вложения.get(схема.slug, {}),
					данные.get(схема.slug),
				),
			}
		)
	return перечень


def _артефакт_целиком(ученик: str, course: str, пространство: str | None, artifact: str) -> dict:
	"""Блоки в порядке схемы. Блок, убранный из схемы, не показывается, но
	его содержимое остаётся в базе — вернётся вместе с блоком."""
	схема = _действующая_схема(course, artifact)
	экземпляр = _экземпляр(ученик, course, схема.slug, пространство)
	содержимое = _содержимое(экземпляр)
	вложения = _вложения(экземпляр)
	данные = _данные(экземпляр)
	файлы = _файлы(вложения)
	return {
		"course": course,
		"artifact": схема.slug,
		"title": схема.title,
		"layout": схема.layout,
		"blocks": [_блок(блок, содержимое, вложения, файлы, схема, данные) for блок in схема.blocks],
		# Таблицы документа целиком: колонки всех блоков, строки с формулами
		# и markdown для агента; значения полей по ключам (#330).
		"tables": export.таблицы_документа(схема.blocks, данные),
		# Когда документ менялся и сколько раз сохранялся — по журналу `Version`,
		# который Frappe ведёт у документа ученика (`track_changes`). История
		# наружу не выходит, только её длина (learning-services#342).
		"modified": экземпляр.modified.isoformat() if экземпляр else None,
		"version": frappe.db.count(
			"Version", {"ref_doctype": "Agent Student Artifact", "docname": экземпляр.name}
		)
		if экземпляр
		else 0,
		"fields": data.поля_документа(схема.blocks, данные),
		# Холст документа — сетка блоков, подписи, набросок и сводка ячеек;
		# у документа без холста — `null` (learning-services#351).
		"canvas": canvas.холст(схема.canvas),
	}


def _блоки_урока(ученик: str, курс: str, lesson: str, пространство: str | None) -> list[dict]:
	"""Блоки документов курса, привязанные к уроку, с содержимым ученика."""
	по_схемам = [
		(схема, [блок for блок in схема.blocks if блок.lesson == lesson])
		for схема in _схемы_курса(курс)
	]
	# Содержимое читается, только если уроку вообще принадлежит хоть один блок.
	по_документам, вложения, данные = (
		_содержимое_курса(ученик, курс, пространство)
		if any(свои for _, свои in по_схемам)
		else ({}, {}, {})
	)
	файлы = _файлы({f"{д}/{к}": в for д, блоки in вложения.items() for к, в in блоки.items()})
	блоки = []
	for схема, свои in по_схемам:
		содержимое = по_документам.get(схема.slug, {})
		данные_документа = данные.get(схема.slug) or data.данные(None)
		таблицы = export.таблицы_документа(схема.blocks, данные_документа) if свои else {}
		for блок in свои:
			описание = _блок(
				блок, содержимое, вложения.get(схема.slug, {}), файлы, схема, данные_документа
			)
			if описание.get("table"):
				# Агенту урока — таблица текстом: ID строк, по которым он
				# дописывает колонки блока, и что в них уже есть.
				описание["table_markdown"] = таблицы[описание["table"]]["markdown"]
			блоки.append({"artifact": схема.slug, "artifact_title": схема.title, **описание})
	return блоки


def _пустые_блоки_урока(ученик: str, курс: str, lesson: str, пространство: str | None) -> list[dict]:
	"""Блоки документа, которые собирают на этом уроке, а они пусты.

	Предупреждение, а не отказ: урок засчитывает квиз, блок часто доводят
	после занятия, а «непустой» ещё не значит «готов» — запрет давал бы
	обход, а не документ (решение владельца, learning-services#296).
	"""
	return [
		{"artifact": блок["artifact"], "key": блок["key"], "title": блок["title"]}
		for блок in _блоки_урока(ученик, курс, lesson, пространство)
		if not _заполнен(блок)
	]


def выгрузка(ученик: str, course: str, пространство: str | None, artifact: str, формат: str) -> tuple[str, bytes | str]:
	"""Документ файлом — имя и содержимое: книга Excel или markdown."""
	документ = _артефакт_целиком(ученик, course, пространство, artifact)
	if формат == "xlsx":
		схема = _действующая_схема(course, artifact)
		экземпляр = _экземпляр(ученик, course, схема.slug, пространство)
		return f"{документ['artifact']}.xlsx", export.книга_xlsx(
			документ["title"], схема.blocks, _данные(экземпляр)
		)
	return f"{документ['artifact']}.md", export.собрать_markdown(документ)
