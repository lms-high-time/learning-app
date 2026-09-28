# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Запись в документ ученика: текст, ссылка, файл, таблица агента, строки и
поля, очистка блока.

Права — у вызывающего: методы API проверяют доступ к курсу и пространство до
записи.
"""

from __future__ import annotations

import json

import frappe

from lms_frappe_app.agent_learning.artifacts import data, files, fill, schema
from lms_frappe_app.agent_learning.artifacts.codes import ВИД_НЕ_ТОТ, ОЧИСТКА_С_ТЕКСТОМ, ПУСТОЙ_БЛОК
from lms_frappe_app.agent_learning.artifacts.course import _блок_схемы
from lms_frappe_app.agent_learning.artifacts.document import (
	_вложения,
	_данные,
	_заполненность,
	_содержимое,
	_экземпляр,
)
from lms_frappe_app.agent_learning.errors import Отказ


def записать_блок(
	ученик: str,
	course: str,
	пространство: str | None,
	artifact: str,
	key: str,
	content: str | None = None,
	clear: bool = False,
	url: str | None = None,
	table=None,
	file_name: str | None = None,
	rows=None,
	delete_rows=None,
	fields=None,
) -> dict:
	"""Блок документа ученика — записью, файлом, строками и полями или очисткой.

	Контракт — у `api.student.update_artifact`; доступ к курсу и пространство
	проверяет он.
	"""
	схема, блок = _блок_схемы(course, artifact, key)
	ключ = блок.block_key
	# Флаг приходит и булевым из JSON, и строкой из формы.
	if clear in (True, 1, "1", "true"):
		if (content or "").strip() or (url or "").strip() or table or rows or delete_rows or fields:
			raise Отказ(
				ОЧИСТКА_С_ТЕКСТОМ,
				"Очистка блока не принимает текст: либо content, либо clear",
				artifact=схема.slug,
				key=ключ,
			)
		return _очистить_блок(ученик, course, пространство, схема, ключ)

	адрес = None
	if (url or "").strip():
		if files.вид(блок) != files.ССЫЛКА:
			raise Отказ(
				ВИД_НЕ_ТОТ,
				"Ссылку принимает только блок-ссылка",
				artifact=схема.slug,
				key=ключ,
				kind=files.вид(блок),
			)
		адрес = files.проверить_ссылку(url)
	if table:
		return _положить_таблицу(ученик, course, пространство, схема, блок, table, file_name, content)
	if rows or delete_rows or fields:
		return _записать_данные(ученик, course, пространство, схема, ключ, content, rows, delete_rows, fields)
	if not (content or "").strip() and not адрес:
		raise Отказ(ПУСТОЙ_БЛОК, "Блок записывается непустым", artifact=схема.slug, key=ключ)

	документ, строка = _строка_блока(ученик, course, пространство, схема, ключ)
	if (content or "").strip():
		строка.content = content
	if адрес:
		строка.url = адрес
	документ.schema_version = схема.name
	документ.save(ignore_permissions=True)
	return _ответ_записи(схема, ключ, документ)


def _ответ_записи(схема, ключ: str, документ, созданы: list[str] | None = None) -> dict:
	"""Ответ записи блока: заполненность документа и чего не хватает блоку.

	`empty_cells` — где блоку с полями и колонками не хватает обязательных
	значений: `{row, column}` или `{field}`; у прочих блоков пусто. `created` —
	ID строк, которые завела эта запись: по ним агент дописывает колонки.
	"""
	данные = _данные(документ)
	блок = next(б for б in схема.blocks if б.block_key == ключ)
	return {
		"artifact": схема.slug,
		"key": ключ,
		**_заполненность(схема, _содержимое(документ), _вложения(документ), данные),
		"empty_cells": fill.пустые_клетки(блок, данные, схема.blocks),
		"created": созданы or [],
	}


def _записать_данные(ученик: str, course: str, пространство: str | None, схема, ключ: str, content, rows, delete_rows, fields) -> dict:
	"""Строки и поля блока — в JSON документа; заметка — в текст блока."""
	if (content or "").strip():
		документ, строка = _строка_блока(ученик, course, пространство, схема, ключ)
		строка.content = content
	else:
		# Без заметки строка блока не нужна: данные таблиц общие для блоков
		# и живут в документе, а пустой текст блока doctype не примет.
		документ = _документ_ученика(ученик, course, пространство, схема)
	прежние = _данные(документ)
	было = {с["id"] for т in прежние["tables"].values() for с in т}
	новые = data.записать(
		схема.blocks, ключ, прежние, rows=rows, delete_rows=delete_rows, fields=fields
	)
	документ.data = json.dumps(новые, ensure_ascii=False)
	документ.schema_version = схема.name
	документ.save(ignore_permissions=True)
	стало = [с["id"] for т in новые["tables"].values() for с in т]
	return _ответ_записи(схема, ключ, документ, [i for i in стало if i not in было])


def _положить_файл(ученик: str, course: str, пространство: str | None, artifact: str, key: str, имя: str, данные: bytes) -> dict:
	схема, блок = _блок_схемы(course, artifact, key)
	ключ = блок.block_key
	тип = files.проверить_файл(блок, имя, данные, artifact=схема.slug, key=ключ)

	документ, строка = _строка_блока(ученик, course, пространство, схема, ключ)
	if not документ.name:
		# Файл привязывается к документу по имени — оно появляется при записи.
		# `Why:` не `is_new()`: у документа, собранного `frappe.get_doc({...})`,
		# нет `__islocal`, и проверка отвечала «не новый» — файл вставлялся без
		# имени документа, и Frappe снимал привязку (#343).
		документ.schema_version = схема.name
		документ.insert(ignore_permissions=True)
		строка = next(с for с in документ.blocks if с.block_key == ключ)
	прежний = строка.file
	новый = files.сохранить_файл(документ, имя, данные)
	строка.file = новый.name
	строка.preview = files.срез(данные, тип)
	документ.schema_version = схема.name
	документ.save(ignore_permissions=True)
	files.удалить_файл(прежний)

	заполнено = _заполненность(схема, _содержимое(документ), _вложения(документ), _данные(документ))
	return {
		"artifact": схема.slug,
		"key": ключ,
		"file": files.сведения_о_файлах([новый.name])[новый.name],
		"preview": строка.preview or None,
		**заполнено,
	}


def _положить_таблицу(
	ученик: str,
	course: str,
	пространство: str | None,
	схема,
	блок,
	table,
	file_name: str | None,
	content: str | None,
) -> dict:
	"""Таблица агента — файлом в блок-файл; текст рядом, если передан."""
	ключ = блок.block_key
	if files.вид(блок) != files.ФАЙЛ:
		raise Отказ(
			ВИД_НЕ_ТОТ,
			"Таблицу файлом принимает только блок-файл",
			artifact=схема.slug,
			key=ключ,
			kind=files.вид(блок),
		)
	тип = files.тип_для_таблицы(блок)
	строки = files.строки_таблицы(table)
	имя = (file_name or "").strip() or ключ
	if files.расширение(имя) != тип:
		имя = f"{имя}.{тип}"
	_положить_файл(
		ученик, course, пространство, схема.slug, ключ, имя, files.собрать_таблицу(строки, тип)
	)
	документ, строка = _строка_блока(ученик, course, пространство, схема, ключ)
	if (content or "").strip():
		строка.content = content
		документ.schema_version = схема.name
		документ.save(ignore_permissions=True)
	return _ответ_записи(схема, ключ, документ)


def _строка_блока(ученик: str, course: str, пространство: str | None, схема, ключ: str):
	"""Документ ученика и строка блока в нём; чего нет — заводится, не записываясь."""
	документ = _документ_ученика(ученик, course, пространство, схема)
	строка = next((с for с in документ.blocks if с.block_key == ключ), None)
	if строка is None:
		строка = документ.append("blocks", {"block_key": ключ})
	return документ, строка


def _документ_ученика(ученик: str, course: str, пространство: str | None, схема):
	"""Документ ученика по схеме в его пространстве; нет — заводится, не записываясь."""
	return _экземпляр(ученик, course, схема.slug, пространство) or _новый_документ(
		ученик, course, схема, пространство
	)


def _новый_документ(ученик: str, course: str, схема, пространство: str | None):
	return frappe.get_doc(
		{
			"doctype": "Agent Student Artifact",
			"student": ученик,
			"course": course,
			"artifact": схема.slug,
			"organization": пространство,
		}
	)


def _очистить_блок(ученик: str, course: str, пространство: str | None, схема, ключ: str) -> dict:
	"""Удаляет строку блока из документа ученика — с файлом; ответ — как у записи.

	Блока нет или документ ещё не заводился — очищать нечего, и это не отказ:
	результат тот же, что после удаления.
	"""
	документ = _экземпляр(ученик, course, схема.slug, пространство)
	строка = next((с for с in документ.blocks if с.block_key == ключ), None) if документ else None
	блок = next(б for б in схема.blocks if б.block_key == ключ)
	файл = None
	if строка:
		файл = строка.file
		документ.remove(строка)
	if документ and schema.спек(блок):
		документ.data = json.dumps(
			data.очистить(схема.blocks, ключ, документ.data), ensure_ascii=False
		)
	if документ and (строка or schema.спек(блок)):
		документ.schema_version = схема.name
		документ.save(ignore_permissions=True)
		files.удалить_файл(файл)
	if документ:
		return _ответ_записи(схема, ключ, документ)
	return {
		"artifact": схема.slug,
		"key": ключ,
		**_заполненность(схема, {}, {}),
		"empty_cells": [],
		"created": [],
	}
