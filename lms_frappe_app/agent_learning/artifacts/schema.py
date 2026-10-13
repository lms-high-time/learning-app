# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Схема документа: раздел релиза — блок документа.

Раздел — `{key, title, description, lesson, rows, columns}`, та же форма,
что у раздела документа в релизе. `rows` — `one` (раздел заполняется один
раз, его колонки — поля документа) или `many` (строки). Колонка —
`{key, title, required}`: `required` — `true`, `false` или
`{"if_column": <колонка того же раздела>}` — обязательна, когда та непуста.
Значение клетки и поля — текст.
"""

from __future__ import annotations

import json
import re

from lms_frappe_app.agent_learning.artifacts.codes import НЕВЕРНАЯ_СХЕМА
from lms_frappe_app.agent_learning.errors import Отказ

ОДИН = "one"
МНОГО = "many"
ВИДЫ_РАЗДЕЛОВ = (ОДИН, МНОГО)

#: Ключ документа, раздела и колонки. `Why:` его же читает проверка релиза
#: (`releases.checks`) и компилятор курса — разбором этого модуля, поэтому
#: присваивание верхнего уровня и литерал.
КЛЮЧ = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def колонки(блок) -> list[dict]:
	"""Колонки блока схемы списком; в базе они JSON-строкой."""
	сырое = блок.get("columns") if isinstance(блок, dict) else getattr(блок, "columns", None)
	if isinstance(сырое, str):
		try:
			сырое = json.loads(сырое) if сырое.strip() else []
		except ValueError:
			return []
	return [dict(к) for к in сырое or [] if isinstance(к, dict)]


def раздел(блок) -> dict:
	"""Блок схемы (строка базы или словарь) — разделом."""
	поле = блок.get if isinstance(блок, dict) else lambda имя: getattr(блок, имя, None)
	return {
		"key": поле("block_key") or поле("key"),
		"title": поле("title"),
		"description": поле("description") or None,
		"lesson": поле("lesson") or None,
		"rows": поле("rows"),
		"columns": колонки(блок),
	}


def проверить_раздел(описание) -> dict:
	"""Раздел от публикации — в каноническом виде; неверный — отказ.

	Проверяется форма одного раздела. Что поля разделов `one` не столкнулись
	между собой и с колонками, — `проверить_документ`.
	"""
	if not isinstance(описание, dict):
		raise Отказ(НЕВЕРНАЯ_СХЕМА, "Раздел — объект")
	ключ = str(описание.get("key") or "").strip().lower()
	if not КЛЮЧ.match(ключ):
		raise Отказ(НЕВЕРНАЯ_СХЕМА, "Ключ раздела — латиница в нижнем регистре", key=ключ)
	if описание.get("rows") not in ВИДЫ_РАЗДЕЛОВ:
		raise Отказ(НЕВЕРНАЯ_СХЕМА, "rows раздела: " + ", ".join(ВИДЫ_РАЗДЕЛОВ), key=ключ)
	сырые = описание.get("columns") or []
	if isinstance(сырые, str):
		сырые = колонки({"columns": сырые})
	if not isinstance(сырые, list):
		raise Отказ(НЕВЕРНАЯ_СХЕМА, "Колонки раздела — список", key=ключ)
	колонки_раздела = [_колонка(к, ключ) for к in сырые]
	ключи = [к["key"] for к in колонки_раздела]
	for колонка in колонки_раздела:
		if ключи.count(колонка["key"]) > 1:
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Колонка с таким ключом уже есть", key=ключ, column=колонка["key"])
		условие = колонка["required"]
		if isinstance(условие, dict) and (
			условие["if_column"] not in ключи or условие["if_column"] == колонка["key"]
		):
			raise Отказ(
				НЕВЕРНАЯ_СХЕМА,
				"Условие обязательности — другая колонка того же раздела",
				key=ключ,
				column=колонка["key"],
			)
	return {
		"key": ключ,
		"title": str(описание.get("title") or "").strip() or ключ,
		"description": str(описание.get("description") or "").strip() or None,
		"lesson": описание.get("lesson") or None,
		"rows": описание["rows"],
		"columns": колонки_раздела,
	}


def _колонка(описание, ключ: str) -> dict:
	if not isinstance(описание, dict):
		raise Отказ(НЕВЕРНАЯ_СХЕМА, "Колонка — объект", key=ключ)
	имя = str(описание.get("key") or "")
	if not КЛЮЧ.match(имя) or имя == "id":
		raise Отказ(
			НЕВЕРНАЯ_СХЕМА, "Ключ колонки — латиница в нижнем регистре, не «id»", key=ключ, column=имя
		)
	обязательна = описание.get("required")
	if isinstance(обязательна, dict):
		условие = обязательна.get("if_column")
		if set(обязательна) != {"if_column"} or not isinstance(условие, str):
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "required — true, false или {if_column}", key=ключ, column=имя)
		обязательна = {"if_column": условие}
	elif обязательна in (None, False, True):
		обязательна = bool(обязательна)
	else:
		raise Отказ(НЕВЕРНАЯ_СХЕМА, "required — true, false или {if_column}", key=ключ, column=имя)
	return {"key": имя, "title": str(описание.get("title") or "").strip() or имя, "required": обязательна}


def проверить_документ(разделы: list[dict]) -> None:
	"""Схема документа целиком: ключи разделов не повторяются; ключ поля раздела
	`one` не совпадает с другим полем и с колонкой.

	`Why:` поля всех разделов `one` лежат в документе ученика одним словарем
	`fields` — два поля с одним ключом делили бы одно значение.
	"""
	видел: set[str] = set()
	for р in разделы:
		if р["key"] in видел:
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Раздел с таким ключом уже есть", key=р["key"])
		видел.add(р["key"])
	поля = [к["key"] for р in разделы if р["rows"] == ОДИН for к in р["columns"]]
	колонки_строк = {к["key"] for р in разделы if р["rows"] == МНОГО for к in р["columns"]}
	for р in разделы:
		if р["rows"] != ОДИН:
			continue
		for к in р["columns"]:
			if поля.count(к["key"]) > 1 or к["key"] in колонки_строк:
				raise Отказ(
					НЕВЕРНАЯ_СХЕМА,
					"Ключ поля занят другим полем или колонкой",
					key=р["key"],
					field=к["key"],
				)
