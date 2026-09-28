# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Схемы документов курса: действующие версии и новая версия от автора.

Схема версионируется: у документа курса одна действующая версия, прежние
остаются в базе. Содержимое ученика хранится по ключам блоков, поэтому новая
версия его не рушит.
"""

from __future__ import annotations

import json

import frappe

from lms_frappe_app.agent_learning import directives
from lms_frappe_app.agent_learning.artifacts import files, schema
from lms_frappe_app.agent_learning.artifacts.canvas import проверить_холст
from lms_frappe_app.agent_learning.artifacts.codes import (
	АРТЕФАКТ_НЕ_НАЙДЕН,
	БЛОК_НЕ_НАЙДЕН,
	НЕВЕРНЫЙ_ВИД_БЛОКА,
)
from lms_frappe_app.agent_learning.doctype.agent_course_artifact.agent_course_artifact import (
	нормализовать_ключ,
)
from lms_frappe_app.agent_learning.errors import УРОК_НЕ_НАЙДЕН, Отказ


def _схемы_курса(course: str) -> list:
	"""Действующие схемы артефактов курса — в порядке, в каком документы
	впервые объявили.

	`Why:` порядок по дате действующей версии менялся бы при каждой правке
	схемы: поправленный документ уезжал бы в конец. Порядок по ключу — в
	алфавитном, а не в смысловом. Первая версия у документа одна и навсегда.

	Блоки подтягиваются списком, а не документом на схему: курс с пятью
	документами стоил десяти обходов базы на каждом начале урока.
	"""
	действующие = frappe.get_all(
		"Agent Course Artifact",
		filters={"course": course, "is_active": 1},
		fields=["name", "slug", "title", "layout"],
	)
	if not действующие:
		return []

	первые = {
		запись.slug: запись.creation
		for запись in frappe.get_all(
			"Agent Course Artifact",
			filters={"course": course, "version": 1},
			fields=["slug", "creation"],
		)
	}
	действующие.sort(key=lambda з: (первые.get(з.slug) is None, первые.get(з.slug) or "", з.slug))

	блоки = _блоки_схем([з.name for з in действующие])
	for схема in действующие:
		схема.blocks = блоки.get(схема.name, [])
	return действующие


def _блоки_схем(схемы: list[str]) -> dict[str, list]:
	"""Блоки всех перечисленных схем одним запросом, по схемам и в порядке автора."""
	по_схемам: dict[str, list] = {}
	for строка in frappe.get_all(
		"Agent Artifact Block",
		filters={"parent": ("in", схемы), "parenttype": "Agent Course Artifact"},
		fields=["parent", "block_key", "title", "hint", "lesson", "span", "kind", "accept", "spec"],
		order_by="parent asc, idx asc",
	):
		по_схемам.setdefault(строка.parent, []).append(строка)
	return по_схемам


def _действующая_схема(course: str, artifact: str):
	имя = frappe.db.get_value(
		"Agent Course Artifact",
		{"course": course, "slug": нормализовать_ключ(artifact), "is_active": 1},
	)
	if not имя:
		raise Отказ(АРТЕФАКТ_НЕ_НАЙДЕН, "В этом курсе нет такого документа", artifact=artifact)
	return frappe.get_doc("Agent Course Artifact", имя)


def _блок_схемы(course: str, artifact: str, key: str):
	"""Действующая схема и её блок по ключу; нет блока — отказ."""
	схема = _действующая_схема(course, artifact)
	ключ = нормализовать_ключ(key)
	блок = next((б for б in схема.blocks if б.block_key == ключ), None)
	if блок is None:
		raise Отказ(
			БЛОК_НЕ_НАЙДЕН, "В этом документе нет такого блока", artifact=схема.slug, key=key
		)
	return схема, блок


def _действующие_артефакты(course: str) -> list[dict]:
	"""Схемы документов курса, которые сейчас получает ученик, с версиями."""
	записи = frappe.get_all(
		"Agent Course Artifact",
		filters={"course": course, "is_active": 1},
		fields=["name", "slug", "title", "layout", "version", "template", "template_version"],
		order_by="creation asc",
	)
	последние = последние_версии_шаблонов({з.template for з in записи if з.template})
	собранное = []
	for запись in записи:
		собранное.append(
			{
				"id": запись.name,
				"version": запись.version,
				"artifact": запись.slug,
				"title": запись.title,
				"layout": запись.layout,
				# Шаблон и закреплённая версия; `template_latest` — чтобы автор
				# видел, что шаблон ушёл вперёд (learning-services#370).
				"template": запись.template or None,
				"template_version": запись.template_version or None,
				"template_latest": последние.get(запись.template),
				"blocks": [
					{
						"key": блок.block_key,
						"title": блок.title,
						"hint": блок.hint or "",
						"lesson": блок.lesson or None,
						"span": блок.span or 1,
						"kind": files.вид(блок),
						"accept": files.допустимые(блок),
					}
					for блок in frappe.get_all(
						"Agent Artifact Block",
						filters={"parent": запись.name},
						fields=["block_key", "title", "hint", "lesson", "span", "kind", "accept"],
						order_by="idx asc",
					)
				],
			}
		)
	return собранное


def последние_версии_шаблонов(шаблоны: set[str]) -> dict[str, int]:
	"""Последняя версия каждого названного шаблона — одним запросом."""
	последние: dict[str, int] = {}
	if not шаблоны:
		return последние
	for запись in frappe.get_all(
		"Agent Artifact Template",
		filters={"template": ("in", sorted(шаблоны))},
		fields=["template", "version"],
	):
		последние[запись.template] = max(последние.get(запись.template, 0), запись.version)
	return последние


def строки_схемы(блоки: list) -> list[dict]:
	"""Блоки от автора — строками схемы в каноническом виде; неверные — отказ.

	Базу не трогает: так же проверяются блоки шаблона, у которого курса нет.
	`блоки` — словари или JSON-строки.
	"""
	строки = []
	for блок in блоки:
		блок = json.loads(блок) if isinstance(блок, str) else dict(блок or {})
		# Вид блока: текст, файл ученика или ссылка на внешний документ (#315).
		вид = блок.get("kind") or files.ТЕКСТ
		if вид not in files.ВИДЫ:
			raise Отказ(
				НЕВЕРНЫЙ_ВИД_БЛОКА,
				"Вид блока: " + ", ".join(files.ВИДЫ),
				key=блок.get("key"),
				kind=вид,
			)
		спек = schema.проверить_спек(блок.get("spec"), блок.get("key"))
		строки.append(
			{
				"block_key": блок.get("key"),
				"title": блок.get("title"),
				"hint": блок.get("hint"),
				"lesson": блок.get("lesson") or None,
				"span": блок.get("span") or 1,
				"kind": вид,
				"accept": ",".join(files.допустимые(блок)) or None,
				"spec": json.dumps(спек, ensure_ascii=False) if спек else None,
			}
		)
	schema.проверить_документ(строки)
	return строки


def блок_наружу(строка) -> dict:
	"""Строка схемы — блоком в той форме, в какой его принимает `set_course_artifact`.

	`Why:` шаблон отдаёт блоки автору и собирается с правками курса в схему,
	которую пишет тот же `записать_схему`: форма у блока одна на все пути.
	"""
	return {
		"key": schema.ключ_блока(строка),
		"title": строка.get("title"),
		"hint": строка.get("hint") or "",
		"lesson": строка.get("lesson") or None,
		"span": строка.get("span") or 1,
		"kind": files.вид(строка),
		"accept": files.допустимые(строка),
		"spec": schema.спек(строка) or None,
	}


def записать_схему(
	course: str,
	artifact: str,
	title: str,
	блоки: list,
	layout: str,
	canvas,
	привязка: dict | None = None,
) -> dict:
	"""Схема документа курса новой версией; неверная — отказ до записи.

	Контракт — у `api.authoring.set_course_artifact`. `блоки` — как их прислал
	автор: словари или JSON-строки. `привязка` — шаблон, его версия и правки
	курса, из которых схема собрана (`set_course_artifact_template`); схема от
	автора целиком — без неё, и новая версия шаблона не наследует.
	"""
	блоки = [json.loads(блок) if isinstance(блок, str) else dict(блок or {}) for блок in блоки]
	for блок in блоки:
		урок = блок.get("lesson") or None
		if урок and not frappe.db.exists("Course Lesson", урок):
			raise Отказ(УРОК_НЕ_НАЙДЕН, "Course Lesson не найден", id=урок)
	строки = строки_схемы(блоки)
	холст = проверить_холст(canvas, строки)
	привязка = привязка or {}
	правки = привязка.get("overlay")
	return directives.записать(
		"Agent Course Artifact",
		{"course": course, "slug": нормализовать_ключ(artifact)},
		{
			"title": title,
			"layout": layout,
			"blocks": строки,
			"canvas": json.dumps(холст, ensure_ascii=False) if холст else None,
			"template": привязка.get("template"),
			"template_version": привязка.get("template_version"),
			"overlay": json.dumps(правки, ensure_ascii=False) if правки is not None else None,
		},
	)
