# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Заполненность блока с полями и колонками и чего ему не хватает.
"""

from __future__ import annotations

from lms_frappe_app.agent_learning.artifacts.data import вычисленные
from lms_frappe_app.agent_learning.artifacts.schema import спек, таблицы_схемы
from lms_frappe_app.agent_learning.artifacts.values import пусто


def _обязательна(колонка: dict, строка: dict) -> bool:
	условие = колонка.get("required")
	if условие is True:
		return True
	return bool(условие) and not пусто(строка.get(условие))


def заполнен(блок, д: dict, блоки: list, текст: str = "") -> bool | None:
	"""Заполнен ли блок с полями или колонками; у прочих блоков — `None`.

	Поля: заполнены обязательные (нет обязательных — хоть одно). Колонки: есть
	строки, и у каждой обязательные колонки блока непусты (без обязательных —
	хоть одна клетка блока). `Why:` прежний текст блока, записанный до таблиц,
	считается, пока у блока нет ни одного значения: иначе прогресс учеников,
	начавших курс на markdown, упал бы в день выкладки.
	"""
	с = спек(блок)
	if not с.get("fields") and not с.get("columns"):
		return None
	# Формулы — не ввод: блок заполняет ученик, а не сервер своим расчётом.
	поля = [п for п in с.get("fields", []) if п["type"] != "formula"]
	колонки = [к for к in с.get("columns", []) if к["type"] != "formula"]
	таблица = таблицы_схемы(блоки)[с["table"]] if колонки else None
	ряды = вычисленные(таблица, д, блоки) if таблица else []

	# Заготовка — не заполненность: шкалы из урока приходят готовыми, но
	# блок готов, когда ученик подогнал их под свой проект.
	тронута = таблица is not None and таблица["name"] in д["tables"]
	есть_значения = any(not пусто(д["fields"].get(п["key"])) for п in поля) or (
		тронута and any(not пусто(р.get(к["key"])) for р in ряды for к in колонки)
	)
	if not есть_значения:
		return bool((текст or "").strip())

	if поля:
		обязательные = [п for п in поля if п.get("required")] or поля
		проверка = all if any(п.get("required") for п in поля) else any
		if not проверка(not пусто(д["fields"].get(п["key"])) for п in обязательные):
			return False
	if колонки:
		if not ряды:
			return False
		обязательные = [к for к in колонки if к.get("required")]
		if обязательные:
			return all(
				пусто(р.get(к["key"])) is False for р in ряды for к in обязательные if _обязательна(к, р)
			)
		return any(not пусто(р.get(к["key"])) for р in ряды for к in колонки)
	return True


def пустые_клетки(блок, д: dict, блоки: list) -> list[dict]:
	"""Где блоку не хватает значений: строка и колонка. Для агента и страницы."""
	с = спек(блок)
	пустые = [
		{"field": п["key"]}
		for п in с.get("fields", [])
		if п.get("required") and пусто(д["fields"].get(п["key"]))
	]
	колонки = [к for к in с.get("columns", []) if к.get("required")]
	if колонки:
		таблица = таблицы_схемы(блоки)[с["table"]]
		for р in вычисленные(таблица, д, блоки):
			for к in колонки:
				if _обязательна(к, р) and пусто(р.get(к["key"])):
					пустые.append({"row": р["id"], "column": к["key"]})
	return пустые
