# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Документ ученика по схеме курса: данные, заполненность, блоки наружу.

Документ один на «ученик + курс + схема + пространство» и заводится при
первой записи; до неё чтение отдаёт пустые блоки схемы. Права — у
вызывающего: методы API проверяют доступ к курсу и пространство до чтения.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import get_system_timezone

from lms_frappe_app.agent_learning.artifacts import data, export, fill, schema
from lms_frappe_app.agent_learning.artifacts.course import _действующая_схема, _схемы_курса


def _с_поясом(момент: datetime) -> str:
	"""ISO 8601 со смещением пояса сайта — как время у квиза.

	`Why:` Frappe хранит время наивным, в поясе сайта; браузер прочёл бы
	строку без смещения в своём поясе, и «Мои документы» сдвигали время
	изменения на разницу поясов, а около полуночи — и день
	(learning-services#462).
	"""
	return момент.replace(tzinfo=ZoneInfo(get_system_timezone())).isoformat()


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


def _данные(экземпляр) -> dict:
	"""Строки и поля документа ученика; нет документа — пустые."""
	return data.данные(экземпляр.data if экземпляр else None)


def _блок_заполнен(блок, данные: dict) -> bool:
	return fill.заполнен(schema.раздел(блок), данные)


def _заполненность(схема, данные: dict | None = None) -> dict:
	"""Сколько блоков у схемы и сколько из них заполнено (`fill.заполнен`)."""
	данные = данные or data.данные(None)
	return {
		"blocks_total": len(схема.blocks),
		"blocks_filled": sum(1 for блок in схема.blocks if _блок_заполнен(блок, данные)),
	}


def кратко(схема, блок, данные: dict | None = None) -> dict:
	"""Краткий блок: документ, ключ, название и описание для ученика; с данными
	ученика — ещё `filled`."""
	итог = {
		"artifact": схема.slug,
		"key": блок.block_key,
		"title": блок.title,
		"description": блок.description or None,
	}
	if данные is not None:
		итог["filled"] = _блок_заполнен(блок, данные)
	return итог


def _блок(блок, данные: dict) -> dict:
	"""Блок схемы с данными ученика: колонки, строки (`items`, у `many`) или
	поля (`values`, у `one`), заполненность и чего не хватает."""
	раздел = schema.раздел(блок)
	итог = {
		"key": раздел["key"],
		"title": раздел["title"],
		"description": раздел["description"],
		"lesson": раздел["lesson"],
		"rows": раздел["rows"],
		"columns": раздел["columns"],
	}
	if раздел["rows"] == schema.МНОГО:
		итог["items"] = data.строки(раздел, данные)
	else:
		итог["values"] = data.поля(раздел, данные)
	итог["filled"] = fill.заполнен(раздел, данные)
	итог["empty_cells"] = fill.пустые_клетки(раздел, данные)
	return итог


def _данные_курса(ученик: str, course: str, пространство: str | None) -> dict[str, dict]:
	"""Данные всех документов ученика по курсу в пространстве — по ключу документа, одним запросом."""
	return {
		запись.artifact: data.данные(запись.data)
		for запись in frappe.get_all(
			"Agent Student Artifact",
			filters={
				"student": ученик,
				"course": course,
				"organization": пространство or ("is", "not set"),
			},
			fields=["artifact", "data"],
		)
	}


def _перечень_артефактов(ученик: str, course: str, пространство: str | None) -> list[dict]:
	записи = frappe.get_all(
		"Agent Student Artifact",
		filters={
			"student": ученик,
			"course": course,
			"organization": пространство or ("is", "not set"),
		},
		fields=["artifact", "data", "modified"],
	)
	данные = {з.artifact: data.данные(з.data) for з in записи}
	# Когда документ менялся — тот же `modified`, что у документа целиком;
	# «Мои документы» показывают его в строке (learning-services#462).
	изменён = {з.artifact: з.modified for з in записи}
	return [
		{
			"artifact": схема.slug,
			"title": схема.title,
			"purpose": схема.purpose or None,
			"modified": _с_поясом(изменён[схема.slug]) if схема.slug in изменён else None,
			**_заполненность(схема, данные.get(схема.slug)),
		}
		for схема in _схемы_курса(course)
	]


def _артефакт_целиком(ученик: str, course: str, пространство: str | None, artifact: str) -> dict:
	"""Документ с блоками в порядке схемы. Блок или колонка, убранные из схемы,
	не показываются, но данные остаются в базе — вернутся вместе с ними."""
	схема = _действующая_схема(course, artifact)
	экземпляр = _экземпляр(ученик, course, схема.slug, пространство)
	данные = _данные(экземпляр)
	return {
		"course": course,
		"artifact": схема.slug,
		"title": схема.title,
		"purpose": схема.purpose or None,
		# Когда документ менялся и сколько раз сохранялся — по журналу `Version`,
		# который Frappe ведёт у документа ученика (`track_changes`). История
		# наружу не выходит, только её длина (learning-services#342).
		"modified": _с_поясом(экземпляр.modified) if экземпляр else None,
		"version": frappe.db.count(
			"Version", {"ref_doctype": "Agent Student Artifact", "docname": экземпляр.name}
		)
		if экземпляр
		else 0,
		"blocks": [_блок(блок, данные) for блок in схема.blocks],
	}


def _блоки_урока(ученик: str, курс: str, lesson: str, пространство: str | None) -> list[dict]:
	"""Блоки документов курса, привязанные к уроку, — краткие, с заполненностью ученика."""
	по_схемам = [
		(схема, [блок for блок in схема.blocks if блок.lesson == lesson]) for схема in _схемы_курса(курс)
	]
	# Данные читаются, только если уроку вообще принадлежит хоть один блок.
	данные = _данные_курса(ученик, курс, пространство) if any(свои for _, свои in по_схемам) else {}
	return [
		кратко(схема, блок, данные.get(схема.slug) or data.данные(None))
		for схема, свои in по_схемам
		for блок in свои
	]


def _пустые_блоки_урока(ученик: str, курс: str, lesson: str, пространство: str | None) -> list[dict]:
	"""Блоки документа, которые собирают на этом уроке, а они не заполнены.

	Предупреждение, а не отказ: урок засчитывает квиз, блок часто доводят
	после занятия, а «непустой» ещё не значит «готов» — запрет давал бы
	обход, а не документ (решение владельца, learning-services#296).
	"""
	return [блок for блок in _блоки_урока(ученик, курс, lesson, пространство) if not блок["filled"]]


def выгрузка(
	ученик: str, course: str, пространство: str | None, artifact: str, формат: str
) -> tuple[str, bytes | str]:
	"""Документ файлом — имя и содержимое: книга Excel или markdown."""
	схема = _действующая_схема(course, artifact)
	данные = _данные(_экземпляр(ученик, course, схема.slug, пространство))
	разделы = [schema.раздел(блок) for блок in схема.blocks]
	if формат == "xlsx":
		return f"{схема.slug}.xlsx", export.книга_xlsx(разделы, данные)
	return f"{схема.slug}.md", export.собрать_markdown(схема.title, разделы, данные)
