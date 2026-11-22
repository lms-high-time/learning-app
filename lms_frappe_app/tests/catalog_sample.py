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
	ключ: str, область: str, *, parts: list[dict] | None = None, similar: list[str] | None = None
) -> dict:
	"""Компетенция каталога: базовая, а с `parts` — интегративная."""
	комп = {
		"key": ключ,
		"title": f"Умение {ключ}",
		"description": f"Что человек делает, владея умением {ключ}.",
		"kind": "integrative" if parts else "base",
		"area": область,
		"levels": {уровень: f"Признак уровня {уровень} для {ключ}" for уровень in ("1", "2", "3", "4")},
	}
	if parts:
		комп["parts"] = parts
	if similar:
		комп["similar"] = similar
	return комп


def ключи_примера(суффикс: str) -> dict[str, str]:
	"""Ключи `пример_каталога(суффикс)`: область, две базовые, интегративная из них."""
	return {
		"area": f"area-{суффикс}",
		"a": f"base-a-{суффикс}",
		"b": f"base-b-{суффикс}",
		"whole": f"whole-{суффикс}",
	}


def пример_каталога(суффикс: str) -> dict:
	"""Каталог из одной области: две базовые компетенции, похожие друг на друга, и
	интегративная из них — со второй с уровня 3."""
	к = ключи_примера(суффикс)
	return {
		"format": "lms-competency-catalog/1",
		"areas": [{"key": к["area"], "title": f"Область {суффикс}"}],
		"competencies": [
			компетенция(к["a"], к["area"], similar=[к["b"]]),
			компетенция(к["b"], к["area"]),
			компетенция(
				к["whole"],
				к["area"],
				parts=[{"competency": к["a"], "from_level": 1}, {"competency": к["b"], "from_level": 3}],
			),
		],
	}


def поверх_опубликованного(каталог: dict) -> dict:
	"""Каталог вместе с последним опубликованным: его области и компетенции, которых нет в
	`каталог`, дописываются в начало. Ключи каталога постоянные, и публикация без
	опубликованных раньше — отказ; так тест не зависит от того, что опубликовали до него."""
	снимок = frappe.get_all(
		"Agent Competency Catalog Release", pluck="snapshot", order_by="version desc", limit=1
	)
	if not снимок:
		return каталог
	прежний = json.loads(снимок[0])
	итог = copy.deepcopy(каталог)
	свои_области = {о["key"] for о in итог["areas"]}
	свои = {к["key"] for к in итог["competencies"]}
	итог["areas"] = [о for о in прежний["areas"] if о["key"] not in свои_области] + итог["areas"]
	итог["competencies"] = [к for к in прежний["competencies"] if к["key"] not in свои] + итог["competencies"]
	return итог
