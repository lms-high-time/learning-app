# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Заполненность раздела и чего ему не хватает."""

from __future__ import annotations

from lms_frappe_app.agent_learning.artifacts.data import поля, строки
from lms_frappe_app.agent_learning.artifacts.schema import МНОГО
from lms_frappe_app.agent_learning.artifacts.values import пусто


def обязательна(колонка: dict, значения: dict) -> bool:
	"""Обязательна ли колонка у строки (или поле у раздела `one`) со значениями `значения`."""
	условие = колонка.get("required")
	if isinstance(условие, dict):
		return not пусто(значения.get(условие["if_column"]))
	return условие is True


def заполнен(раздел: dict, д: dict) -> bool:
	"""Заполнен ли раздел.

	`many` — в нём есть непустые строки, и у каждой непусты обязательные
	колонки. `one` — непусто хоть одно поле, и непусты обязательные поля.
	Обязательность — с учётом `if_column`.
	"""
	if раздел["rows"] == МНОГО:
		ряды = _непустые_строки(раздел, д)
		return bool(ряды) and not _пустые(раздел, ряды)
	значения = поля(раздел, д)
	return bool(значения) and not _пустые(раздел, [значения])


def пустые_клетки(раздел: dict, д: dict) -> list[dict]:
	"""Где разделу не хватает обязательных значений: `{row, column}` у `many`,
	`{field}` у `one`."""
	if раздел["rows"] == МНОГО:
		return [{"row": р["id"], "column": к} for р, к in _пустые(раздел, _непустые_строки(раздел, д))]
	return [{"field": к} for _, к in _пустые(раздел, [поля(раздел, д)])]


def _непустые_строки(раздел: dict, д: dict) -> list[dict]:
	"""Строки раздела, где есть хоть одна непустая клетка.

	`Why:` страница документа заводит пустую строку и потом заполняет её
	клетки по одной; заведённая, но ещё пустая строка — не запись, и ни
	заполненности, ни нехватки клеток она не даёт.
	"""
	return [р for р in строки(раздел, д) if len(р) > 1]


def _пустые(раздел: dict, ряды: list[dict]) -> list[tuple[dict, str]]:
	return [
		(р, к["key"]) for р in ряды for к in раздел["columns"] if обязательна(к, р) and пусто(р.get(к["key"]))
	]
