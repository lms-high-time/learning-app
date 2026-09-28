# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Холст — раскладка документа сеткой, как у Lean Canvas: ячейка — блок
документа, область блока — прямоугольник из ячеек. `Why:` раскладка
`canvas` ставила блоки подряд по ширине `span`, и девять ячеек холста
ложились не туда, где их ждёт ученик, знающий холст по книге
(learning-services#351).

    {"grid": ["problem solution uvp", "problem metrics uvp"],
     "labels": {"uvp": "Обещание"},
     "sketch": "first_sketch",
     "summary": {"problem": ["problem", "alternative"]}}

`grid` — строки сетки, ключи блоков через пробел; «.» — пустая ячейка, как в
CSS grid-template-areas, куда сетка и уходит. `labels` — короткие подписи
ячеек. `sketch` — блок, поля которого держат первый набросок: поле с ключом
ячейки — «как было». `summary` — какие поля и колонки таблицы блока
показать в ячейке.
"""

from __future__ import annotations

import json

from lms_frappe_app.agent_learning.artifacts.codes import НЕВЕРНАЯ_СХЕМА
from lms_frappe_app.agent_learning.artifacts.schema import ключ_блока, поля_схемы, спек, таблицы_схемы
from lms_frappe_app.agent_learning.errors import Отказ

ПУСТАЯ_ЯЧЕЙКА = "."


def проверить_холст(холст, блоки: list) -> dict | None:
	"""Холст от автора — в каноническом виде; пустой — `None`; неверный — отказ."""
	if not холст:
		return None
	if isinstance(холст, str):
		try:
			холст = json.loads(холст)
		except ValueError as ошибка:
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Холст — JSON", key="canvas") from ошибка
	if not isinstance(холст, dict):
		raise Отказ(НЕВЕРНАЯ_СХЕМА, "Холст — объект", key="canvas")
	по_ключам = {(ключ_блока(б) or "").strip().lower(): б for б in блоки}

	набросок = str(холст.get("sketch") or "").strip().lower() or None
	if набросок is not None:
		if набросок not in по_ключам:
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Набросок — блок этого документа", key=набросок)
		if not спек(по_ключам[набросок]).get("fields"):
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Набросок держат поля блока, а их нет", key=набросок)

	сетка = _сетка(холст.get("grid"), по_ключам, набросок)
	ключи = {к for ряд in сетка for к in ряд} - {ПУСТАЯ_ЯЧЕЙКА}
	if набросок is not None and not ключи & {п["key"] for п in спек(по_ключам[набросок])["fields"]}:
		raise Отказ(НЕВЕРНАЯ_СХЕМА, "У наброска нет ни одного поля с ключом ячейки холста", key=набросок)

	подписи = _словарь_холста(холст.get("labels"), "labels", ключи)
	for ключ, подпись in подписи.items():
		подписи[ключ] = str(подпись or "").strip()
		if not подписи[ключ]:
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Подпись ячейки — непустой текст", key=ключ)

	сводка = _словарь_холста(холст.get("summary"), "summary", ключи)
	поля = поля_схемы(блоки)
	таблицы = таблицы_схемы(блоки)
	for ключ, показать in сводка.items():
		if not isinstance(показать, list) or not показать:
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Сводка ячейки — список ключей", key=ключ)
		таблица = таблицы.get(спек(по_ключам[ключ]).get("table") or "")
		свои = (set(поля) | {к["key"] for к in таблица["columns"]}) if таблица else set(поля)
		сводка[ключ] = [str(имя).strip() for имя in показать]
		неизвестные = [имя for имя in сводка[ключ] if имя not in свои]
		if неизвестные:
			raise Отказ(
				НЕВЕРНАЯ_СХЕМА,
				"Сводка — поля документа и колонки таблицы блока",
				key=ключ,
				names=неизвестные,
			)

	return {
		"grid": [" ".join(ряд) for ряд in сетка],
		"labels": подписи,
		"sketch": набросок,
		"summary": сводка,
	}


def _сетка(сырая, по_ключам: dict, набросок: str | None) -> list[list[str]]:
	"""Строки сетки — ячейками; каждая область — прямоугольник.

	`Why:` CSS grid-template-areas отбрасывает сетку целиком, если область не
	прямоугольник или строки разной длины, — страница ученика осталась бы без
	раскладки молча.
	"""
	if not isinstance(сырая, list) or not сырая:
		raise Отказ(НЕВЕРНАЯ_СХЕМА, "Сетке холста нужны строки: grid", key="canvas")
	сетка = []
	for ряд in сырая:
		if not isinstance(ряд, str) or not ряд.split():
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Строка сетки — ключи блоков через пробел", key="canvas")
		сетка.append([к.lower() for к in ряд.split()])
	if len({len(ряд) for ряд in сетка}) != 1:
		raise Отказ(
			НЕВЕРНАЯ_СХЕМА,
			"В строках сетки поровну ячеек",
			key="canvas",
			cells=[len(ряд) for ряд in сетка],
		)

	ячейки: dict[str, list[tuple[int, int]]] = {}
	for у, ряд in enumerate(сетка):
		for х, ключ in enumerate(ряд):
			if ключ == ПУСТАЯ_ЯЧЕЙКА:
				continue
			if ключ not in по_ключам:
				raise Отказ(НЕВЕРНАЯ_СХЕМА, "Ячейка холста — блок этого документа", key=ключ)
			if ключ == набросок:
				raise Отказ(НЕВЕРНАЯ_СХЕМА, "Набросок не ячейка холста, а её «как было»", key=ключ)
			ячейки.setdefault(ключ, []).append((у, х))
	for ключ, место in ячейки.items():
		верх, низ = min(у for у, _ in место), max(у for у, _ in место)
		лево, право = min(х for _, х in место), max(х for _, х in место)
		if len(место) != (низ - верх + 1) * (право - лево + 1):
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Область блока на холсте — прямоугольник", key=ключ)
	return сетка


def _словарь_холста(значение, имя: str, ключи: set[str]) -> dict:
	"""Подписи и сводка — по ключам ячеек сетки; чужой ключ — отказ."""
	if not значение:
		return {}
	if not isinstance(значение, dict):
		raise Отказ(НЕВЕРНАЯ_СХЕМА, f"{имя} холста — объект по ключам ячеек", key="canvas")
	итог = {}
	for ключ, что in значение.items():
		ключ = str(ключ).strip().lower()
		if ключ not in ключи:
			raise Отказ(НЕВЕРНАЯ_СХЕМА, f"{имя}: ключ — ячейка сетки холста", key=ключ)
		итог[ключ] = что
	return итог


def холст(сырое) -> dict | None:
	"""Холст схемы из базы; нет или не читается — `None`."""
	if not сырое:
		return None
	if isinstance(сырое, str):
		try:
			сырое = json.loads(сырое)
		except ValueError:
			return None
	return сырое if isinstance(сырое, dict) else None
