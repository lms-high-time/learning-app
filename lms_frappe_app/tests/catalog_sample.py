# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Синтетический каталог компетенций для тестов (learning-services#825).

Тексты — заглушки: содержание каталога в открытый репозиторий не кладётся
(CLAUDE.md §10). Форма — `agent_learning/catalog/catalog.public.schema.json`.
"""

import copy
import json

import frappe


def компетенция(
	ключ: str,
	классы: dict[str, str] | None = None,
	*,
	parts: list[dict] | None = None,
	similar: list[str] | None = None,
) -> dict:
	"""Компетенция каталога: базовая, а с `parts` — интегративная; `классы` — `{схема: класс}`."""
	комп = {
		"key": ключ,
		"title": f"Умение {ключ}",
		"description": f"Что человек делает, владея умением {ключ}.",
		"kind": "integrative" if parts else "base",
		"levels": {уровень: f"Признак уровня {уровень} для {ключ}" for уровень in ("1", "2", "3", "4")},
	}
	if классы:
		комп["classes"] = dict(классы)
	if parts:
		комп["parts"] = parts
	if similar:
		комп["similar"] = similar
	return комп


def ключи_примера(суффикс: str) -> dict:
	"""Ключи `пример_каталога(суффикс)`: обязательная схема `scheme` с классами `x` и `y`,
	необязательная `nos` с классами `self` и `people`, две базовые компетенции, интегративная
	из них; `classes` — классы, с которыми компетенция проходит обязательную схему."""
	к = {
		"scheme": f"domain-{суффикс}",
		"x": f"product-{суффикс}",
		"y": f"team-{суффикс}",
		"nos": f"nos-{суффикс}",
		"people": f"people-{суффикс}",
		"self": f"self-{суффикс}",
		"a": f"base-a-{суффикс}",
		"b": f"base-b-{суффикс}",
		"whole": f"whole-{суффикс}",
	}
	к["classes"] = {к["scheme"]: к["x"]}
	return к


def пример_каталога(суффикс: str) -> dict:
	"""Каталог в каноническом порядке: обязательная своя схема — без первоисточника — и
	необязательная внешняя, две базовые
	компетенции, похожие друг на друга, и интегративная из них — со второй с уровня 3.
	Класс в необязательной схеме — только у первой базовой."""
	к = ключи_примера(суффикс)
	return {
		"format": "lms-competency-catalog/1",
		"classifications": [
			{
				"key": к["scheme"],
				"title": f"Направление {суффикс}",
				"source": [],
				"required": True,
				"classes": [{"key": к["x"], "title": "Продукт"}, {"key": к["y"], "title": "Команда"}],
			},
			{
				"key": к["nos"],
				"title": f"Функции руководителя {суффикс}",
				"source": [f"https://example.com/{к['nos']}", f"https://example.org/{к['nos']}"],
				"required": False,
				"classes": [
					{"key": к["people"], "title": "Работать с людьми"},
					{"key": к["self"], "title": "Управлять собой"},
				],
			},
		],
		"competencies": [
			компетенция(к["a"], {к["scheme"]: к["x"], к["nos"]: к["people"]}, similar=[к["b"]]),
			компетенция(к["b"], {к["scheme"]: к["y"]}),
			компетенция(
				к["whole"],
				к["classes"],
				parts=[{"competency": к["a"], "from_level": 1}, {"competency": к["b"], "from_level": 3}],
			),
		],
	}


def поверх_опубликованного(каталог: dict) -> dict:
	"""Каталог вместе с последним опубликованным: его схемы, классы схем и компетенции,
	которых нет в `каталог`, дописываются (схемы — по ключу, компетенции — в начало), а
	компетенция без класса в обязательной схеме получает её первый класс. Ключи каталога
	постоянные, и публикация без опубликованных раньше — отказ; так тест не зависит от
	того, что опубликовали до него."""
	снимок = frappe.get_all(
		"Agent Competency Catalog Release", pluck="snapshot", order_by="version desc", limit=1
	)
	if not снимок:
		return каталог
	прежний = json.loads(снимок[0])
	итог = copy.deepcopy(каталог)
	свои_схемы = {с["key"]: с for с in итог["classifications"]}
	for схема in прежний["classifications"]:
		if своя := свои_схемы.get(схема["key"]):
			свои_классы = {к["key"] for к in своя["classes"]}
			своя["classes"] = sorted(
				своя["classes"] + [к for к in схема["classes"] if к["key"] not in свои_классы],
				key=lambda к: к["key"],
			)
		else:
			итог["classifications"].append(схема)
	итог["classifications"].sort(key=lambda с: с["key"])
	свои = {к["key"] for к in итог["competencies"]}
	итог["competencies"] = [к for к in прежний["competencies"] if к["key"] not in свои] + итог["competencies"]
	for схема in итог["classifications"]:
		if схема["required"]:
			for комп in итог["competencies"]:
				комп.setdefault("classes", {}).setdefault(схема["key"], схема["classes"][0]["key"])
	return итог
