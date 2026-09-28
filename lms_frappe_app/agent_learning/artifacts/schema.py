# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Схема блока и документа: поля, колонки, таблицы и виды.

Схема блока приходит от автора и проверяется здесь по форме
(`проверить_спек`), схема документа — целиком, всеми блоками
(`проверить_документ`). Таблицы и поля документа собираются из схем блоков
по их порядку.
"""

from __future__ import annotations

import json
import re

from lms_frappe_app.agent_learning.artifacts.codes import НЕВЕРНАЯ_СХЕМА
from lms_frappe_app.agent_learning.artifacts.formulas import имена, разобрать
from lms_frappe_app.agent_learning.errors import Отказ

ТИПЫ_КОЛОНОК = ("text", "longtext", "number", "scale", "date", "select", "ref", "check", "formula")
ТИПЫ_ПОЛЕЙ = ("text", "longtext", "number", "date", "select", "formula")
#: Виды таблицы: матрица по двум шкалам, доклад по отмеченным строкам и набор
#: колонок — готовый срез широкой таблицы, «Кратко» или «Признаки» у реестра
#: (learning-services#342).
ВИДЫ_ПРЕДСТАВЛЕНИЙ = ("matrix", "report", "columns")

КЛЮЧ = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def спек(блок) -> dict:
	"""Схема полей и колонок блока; у старых блоков её нет — пустая."""
	сырое = блок.get("spec") if isinstance(блок, dict) else getattr(блок, "spec", None)
	if not сырое:
		return {}
	if isinstance(сырое, str):
		try:
			сырое = json.loads(сырое)
		except ValueError:
			return {}
	return сырое if isinstance(сырое, dict) else {}


def ключ_блока(блок) -> str:
	return блок.get("block_key") or блок.get("key") if isinstance(блок, dict) else блок.block_key


def проверить_спек(спецификация: dict | str | None, ключ: str) -> dict:
	"""Схема блока от автора — в каноническом виде; неверная — отказ.

	Проверяется форма одного блока. Что колонки двух блоков не столкнулись,
	а ссылки ведут на существующие таблицы, — `проверить_документ`.
	"""
	if not спецификация:
		return {}
	if isinstance(спецификация, str):
		try:
			спецификация = json.loads(спецификация)
		except ValueError as ошибка:
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Схема блока — JSON", key=ключ) from ошибка
	if not isinstance(спецификация, dict):
		raise Отказ(НЕВЕРНАЯ_СХЕМА, "Схема блока — объект", key=ключ)

	итог: dict = {}
	поля = [_поле(п, ключ) for п in спецификация.get("fields") or []]
	if поля:
		итог["fields"] = поля
	колонки = [_колонка(к, ключ) for к in спецификация.get("columns") or []]
	if колонки:
		таблица = спецификация.get("table") or ключ
		if not КЛЮЧ.match(таблица):
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Ключ таблицы — латиница в нижнем регистре", key=ключ, table=таблица)
		итог["table"] = таблица
		итог["columns"] = колонки
		for имя in ("prefix", "title"):
			if спецификация.get(имя):
				итог[имя] = str(спецификация[имя]).strip()
		if спецификация.get("rows"):
			if not isinstance(спецификация["rows"], list):
				raise Отказ(НЕВЕРНАЯ_СХЕМА, "Заготовка строк — список", key=ключ)
			итог["rows"] = [dict(р) for р in спецификация["rows"] if isinstance(р, dict)]
		представления = [_представление(в, ключ) for в in спецификация.get("views") or []]
		if представления:
			итог["views"] = представления
	elif спецификация.get("rows") or спецификация.get("views"):
		raise Отказ(НЕВЕРНАЯ_СХЕМА, "Заготовка строк и виды — только у блока с колонками", key=ключ)
	return итог


def _общее(описание, ключ: str, типы: tuple[str, ...], что: str) -> dict:
	if not isinstance(описание, dict):
		raise Отказ(НЕВЕРНАЯ_СХЕМА, f"{что} — объект", key=ключ)
	имя = str(описание.get("key") or "").strip()
	if not КЛЮЧ.match(имя) or имя == "id":
		raise Отказ(НЕВЕРНАЯ_СХЕМА, f"Ключ: {что.lower()} — латиница, не «id»", key=ключ, column=имя)
	тип = описание.get("type") or "text"
	if тип not in типы:
		raise Отказ(НЕВЕРНАЯ_СХЕМА, f"Тип: {', '.join(типы)}", key=ключ, column=имя, type=тип)
	итог = {"key": имя, "title": str(описание.get("title") or имя).strip(), "type": тип}
	if описание.get("hint"):
		итог["hint"] = str(описание["hint"]).strip()
	if тип == "select":
		варианты = [str(в).strip() for в in описание.get("options") or [] if str(в).strip()]
		if not варианты:
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "У выбора нужен список вариантов", key=ключ, column=имя)
		итог["options"] = варианты
	return итог


def _поле(описание, ключ: str) -> dict:
	итог = _общее(описание, ключ, ТИПЫ_ПОЛЕЙ, "Поле")
	if итог["type"] == "formula":
		# Формула поля — вывод из полей документа: «сколько клиентов нужно» из
		# цели этапа и цены. `Why:` ученик считал это в уме, и число в холсте
		# расходилось с его же вводными.
		if описание.get("required"):
			raise Отказ(
				НЕВЕРНАЯ_СХЕМА, "Формулу не заполняют: required ей ни к чему", key=ключ, field=итог["key"]
			)
		формула = str(описание.get("formula") or "").strip()
		разобрать(формула)
		итог["formula"] = формула
	elif описание.get("required"):
		итог["required"] = True
	return итог


def _колонка(описание, ключ: str) -> dict:
	итог = _общее(описание, ключ, ТИПЫ_КОЛОНОК, "Колонка")
	тип = итог["type"]
	if тип == "scale":
		итог["min"] = int(описание.get("min", 1))
		итог["max"] = int(описание.get("max", 5))
		if итог["min"] >= итог["max"]:
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "У шкалы min меньше max", key=ключ, column=итог["key"])
		if описание.get("labels"):
			итог["labels"] = str(описание["labels"]).strip()
	if тип == "ref":
		if not КЛЮЧ.match(str(описание.get("ref") or "")):
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Ссылке нужна таблица: ref", key=ключ, column=итог["key"])
		итог["ref"] = описание["ref"]
	if тип == "formula":
		формула = str(описание.get("formula") or "").strip()
		разобрать(формула)
		итог["formula"] = формула
	if тип == "check" and описание.get("max"):
		итог["max"] = int(описание["max"])
	обязательна = описание.get("required")
	if обязательна is True:
		итог["required"] = True
	elif isinstance(обязательна, str) and обязательна.strip():
		# Обязательна при условии: у строк, где колонка-условие истинна
		# («ответ — у рисков в работе»).
		итог["required"] = обязательна.strip()
	if тип == "formula" and "required" in итог:
		raise Отказ(
			НЕВЕРНАЯ_СХЕМА, "Формулу не заполняют: required ей ни к чему", key=ключ, column=итог["key"]
		)
	return итог


def _представление(описание, ключ: str) -> dict:
	if not isinstance(описание, dict) or описание.get("type") not in ВИДЫ_ПРЕДСТАВЛЕНИЙ:
		raise Отказ(НЕВЕРНАЯ_СХЕМА, "Вид: " + ", ".join(ВИДЫ_ПРЕДСТАВЛЕНИЙ), key=ключ)
	итог = {"type": описание["type"], "title": str(описание.get("title") or "").strip()}
	if описание["type"] == "matrix":
		for ось in ("x", "y"):
			if not описание.get(ось):
				raise Отказ(НЕВЕРНАЯ_СХЕМА, "Матрице нужны колонки x и y", key=ключ)
			итог[ось] = описание[ось]
		if описание.get("highlight"):
			итог["highlight"] = описание["highlight"]
	elif описание["type"] == "columns":
		итог["columns"] = [str(к) for к in описание.get("columns") or []]
		if not итог["title"] or not итог["columns"]:
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Набору колонок нужны название и колонки", key=ключ)
	else:
		итог["columns"] = [str(к) for к in описание.get("columns") or []]
		if описание.get("filter"):
			итог["filter"] = описание["filter"]
		if описание.get("field"):
			итог["field"] = описание["field"]
	return итог


def проверить_документ(блоки: list[dict]) -> None:
	"""Схема документа целиком: ключи колонок и полей не сталкиваются, ссылки
	и шкалы ведут на таблицы документа, формулы — на известные имена."""
	таблицы = таблицы_схемы(блоки)
	поля: dict[str, str] = {}
	for блок in блоки:
		for поле in спек(блок).get("fields", []):
			if поле["key"] in поля:
				raise Отказ(
					НЕВЕРНАЯ_СХЕМА, "Поле с таким ключом уже есть", key=ключ_блока(блок), field=поле["key"]
				)
			поля[поле["key"]] = ключ_блока(блок)
	_проверить_формулы_полей(блоки, поля)
	for имя, таблица in таблицы.items():
		ключи = ["id"]
		for колонка in таблица["columns"]:
			if колонка["key"] in ключи or колонка["key"] in поля:
				raise Отказ(
					НЕВЕРНАЯ_СХЕМА, "Колонка с таким ключом уже есть", table=имя, column=колонка["key"]
				)
			ключи.append(колонка["key"])
		for колонка in таблица["columns"]:
			if колонка["type"] == "ref" and колонка["ref"] not in таблицы:
				raise Отказ(
					НЕВЕРНАЯ_СХЕМА, "Ссылка ведёт на таблицу, которой нет", table=имя, column=колонка["key"]
				)
			if колонка.get("labels") and колонка["labels"] not in таблицы:
				raise Отказ(
					НЕВЕРНАЯ_СХЕМА,
					"Подписи шкалы — из таблицы, которой нет",
					table=имя,
					column=колонка["key"],
				)
			if колонка["type"] == "formula":
				неизвестные = имена(разобрать(колонка["formula"])) - set(ключи) - set(поля)
				if неизвестные:
					raise Отказ(
						НЕВЕРНАЯ_СХЕМА,
						"Формула ссылается на неизвестное",
						table=имя,
						column=колонка["key"],
						names=sorted(неизвестные),
					)
			условие = колонка.get("required")
			if isinstance(условие, str) and условие not in ключи:
				raise Отказ(
					НЕВЕРНАЯ_СХЕМА,
					"Условие обязательности — колонка таблицы",
					table=имя,
					column=колонка["key"],
				)


def _проверить_формулы_полей(блоки: list, поля: dict[str, str]) -> None:
	"""Формула поля — только на поля документа; на формулу — только выше себя.

	`Why:` формулы полей считаются одним проходом в порядке схемы, и формула,
	опёртая на себя или на формулу ниже, была бы пустой всегда — автор увидел
	бы это только у ученика.
	"""
	формулы = {п["key"] for б in блоки for п in спек(б).get("fields", []) if п["type"] == "formula"}
	посчитаны: set[str] = set()
	for блок in блоки:
		for поле in спек(блок).get("fields", []):
			if поле["type"] != "formula":
				continue
			названные = имена(разобрать(поле["formula"]))
			неизвестные = названные - set(поля)
			if неизвестные:
				raise Отказ(
					НЕВЕРНАЯ_СХЕМА,
					"Формула поля ссылается только на поля документа",
					key=ключ_блока(блок),
					field=поле["key"],
					names=sorted(неизвестные),
				)
			позже = названные & (формулы - посчитаны)
			if позже:
				raise Отказ(
					НЕВЕРНАЯ_СХЕМА,
					"Формула поля опирается только на формулы выше себя",
					key=ключ_блока(блок),
					field=поле["key"],
					names=sorted(позже),
				)
			посчитаны.add(поле["key"])


def таблицы_схемы(блоки: list) -> dict[str, dict]:
	"""Таблицы документа: колонки всех блоков по порядку схемы и хозяин."""
	таблицы: dict[str, dict] = {}
	for блок in блоки:
		с = спек(блок)
		if not с.get("columns"):
			continue
		имя = с["table"]
		таблица = таблицы.setdefault(
			имя,
			{
				"name": имя,
				"owner": ключ_блока(блок),
				"columns": [],
				"prefix": "",
				"title": "",
				"rows": [],
				"views": [],
			},
		)
		for колонка in с["columns"]:
			таблица["columns"].append({**колонка, "block": ключ_блока(блок)})
		if таблица["owner"] == ключ_блока(блок):
			таблица["prefix"] = с.get("prefix") or имя[:1].upper()
			# Без своего названия таблица зовётся по блоку, который её заводит.
			заголовок = блок.get("title") if isinstance(блок, dict) else getattr(блок, "title", "")
			таблица["title"] = с.get("title") or заголовок or ""
			таблица["rows"] = с.get("rows") or []
		таблица["views"].extend(с.get("views") or [])
	return таблицы


def поля_схемы(блоки: list) -> dict[str, dict]:
	return {п["key"]: {**п, "block": ключ_блока(б)} for б in блоки for п in спек(б).get("fields", [])}
