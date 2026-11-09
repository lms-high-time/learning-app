# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Схемы документов курса: действующие версии; схему пишет публикация релиза.

Схема версионируется: у документа курса одна действующая версия, прежние
остаются в базе. Данные ученика хранятся по ключам разделов и колонок, поэтому
новая версия их не рушит.
"""

from __future__ import annotations

import json

import frappe
from frappe import _

from lms_frappe_app.agent_learning.artifacts import schema
from lms_frappe_app.agent_learning.artifacts.codes import АРТЕФАКТ_НЕ_НАЙДЕН, БЛОК_НЕ_НАЙДЕН
from lms_frappe_app.agent_learning.doctype.agent_course_artifact.agent_course_artifact import (
	нормализовать_ключ,
)
from lms_frappe_app.agent_learning.errors import УРОК_НЕ_НАЙДЕН, Отказ

#: Поля блока схемы, которые читает движок.
ПОЛЯ_БЛОКА = ("block_key", "title", "description", "lesson", "rows", "columns")


def _схемы_курса(course: str) -> list:
	"""Действующие схемы документов курса — в порядке, в каком документы
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
		fields=["name", "slug", "title", "purpose"],
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
		fields=["parent", *ПОЛЯ_БЛОКА],
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
		raise Отказ(АРТЕФАКТ_НЕ_НАЙДЕН, _("This course has no such document"), artifact=artifact)
	return frappe.get_doc("Agent Course Artifact", имя)


def _блок_схемы(course: str, artifact: str, key: str):
	"""Действующая схема и её блок по ключу; нет блока — отказ."""
	схема = _действующая_схема(course, artifact)
	ключ = нормализовать_ключ(key)
	блок = next((б for б in схема.blocks if б.block_key == ключ), None)
	if блок is None:
		raise Отказ(БЛОК_НЕ_НАЙДЕН, _("This document has no such section"), artifact=схема.slug, key=key)
	return схема, блок


def проверить_схему(разделы: list) -> list[dict]:
	"""Строки схемы документа — такими, какими их запишет `записать_схему`;
	неверные — отказ. Ничего не пишет.

	`разделы` — разделы релиза (`schema.проверить_раздел`) с `lesson` — уроком,
	на котором раздел показывается. `Why:` публикация релиза сверяет документ
	релиза с действующей схемой в той форме, в какой его запишет
	`записать_схему`, — иначе разница в записи плодила бы версии.
	"""
	проверенные = [schema.проверить_раздел(р) for р in разделы]
	schema.проверить_документ(проверенные)
	for р in проверенные:
		if р["lesson"] and not frappe.db.exists("Course Lesson", р["lesson"]):
			raise Отказ(УРОК_НЕ_НАЙДЕН, "Course Lesson не найден", id=р["lesson"])
	return [
		{
			"block_key": р["key"],
			"title": р["title"],
			"description": р["description"],
			"lesson": р["lesson"],
			"rows": р["rows"],
			"columns": json.dumps(р["columns"], ensure_ascii=False),
		}
		for р in проверенные
	]


def записать_схему(
	course: str,
	artifact: str,
	title: str,
	разделы: list,
	purpose: str | None = None,
	флаги: dict | None = None,
) -> dict:
	"""Схема документа курса новой версией; неверная — отказ до записи.

	`purpose` не назван — остаётся у прежней версии (learning-services#462).
	Пустая строка его убирает. Прежнюю версию снимает с действия контроллер,
	из базы она не уходит.

	`флаги` — флаги записи схемы: так публикация пишет её без проверки прав и
	со своей пометкой (`releases.document`).
	"""
	строки = проверить_схему(разделы)
	ключ = нормализовать_ключ(artifact)
	if purpose is None:
		purpose = frappe.db.get_value(
			"Agent Course Artifact", {"course": course, "slug": ключ, "is_active": 1}, "purpose"
		)
	документ = frappe.get_doc(
		{
			"doctype": "Agent Course Artifact",
			"course": course,
			"slug": ключ,
			"is_active": 1,
			"title": title,
			"purpose": (purpose or "").strip() or None,
			"blocks": строки,
		}
	)
	документ.flags.update(флаги or {})
	документ.insert()
	return {"id": документ.name, "course": course, "slug": ключ, "version": документ.version}
