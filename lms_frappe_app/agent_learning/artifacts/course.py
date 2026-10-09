# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Схемы документов курса: действующие версии; схему пишет публикация релиза.

Схема версионируется: у документа курса одна действующая версия, прежние
остаются в базе. Содержимое ученика хранится по ключам блоков, поэтому новая
версия его не рушит.
"""

from __future__ import annotations

import json

import frappe

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
from lms_frappe_app.agent_learning.releases.course_guard import ИЗ_РЕЛИЗА


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
		fields=["name", "slug", "title", "purpose", "layout"],
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


def строки_схемы(блоки: list) -> list[dict]:
	"""Блоки от автора — строками схемы в каноническом виде; неверные — отказ.

	Базу не трогает. `блоки` — словари или JSON-строки.
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
				# Что сюда записывают — ученику (learning-services#500).
				"description": блок.get("description") or None,
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
	"""Строка схемы — блоком в той форме, в какой его принимает `проверить_схему`.

	`Why:` проверка каталога сверяет записанную схему, прогоняя её блоки через
	ту же проверку, что и запись: форма у блока одна на оба пути.
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


def проверить_схему(блоки: list, canvas) -> tuple[list[dict], dict | None]:
	"""Строки схемы документа и холст — такими, какими их запишет `записать_схему`;
	неверные — отказ. Ничего не пишет.

	`Why:` публикация релиза сверяет документ релиза с действующей схемой в
	той форме, в какой его запишет `записать_схему`, — иначе пустая подсказка
	или ширина по умолчанию плодили бы версии. Ключ блока и ширина — как их
	приводит контроллер.
	"""
	блоки = [json.loads(блок) if isinstance(блок, str) else dict(блок or {}) for блок in блоки]
	for блок in блоки:
		урок = блок.get("lesson") or None
		if урок and not frappe.db.exists("Course Lesson", урок):
			raise Отказ(УРОК_НЕ_НАЙДЕН, "Course Lesson не найден", id=урок)
	строки = строки_схемы(блоки)
	холст = проверить_холст(canvas, строки)
	for строка in строки:
		строка["block_key"] = нормализовать_ключ(строка["block_key"])
		строка["span"] = max(1, int(строка["span"] or 1))
	return строки, холст


def записать_схему(
	course: str,
	artifact: str,
	title: str,
	блоки: list,
	layout: str,
	canvas,
	purpose: str | None = None,
) -> dict:
	"""Схема документа курса новой версией; неверная — отказ до записи.

	`блоки` — словари или JSON-строки в форме `блок_наружу`. `purpose` не
	назван — остаётся у прежней версии (learning-services#462). Пустая строка
	его убирает. Прежнюю версию снимает с действия контроллер, из базы она не
	уходит.

	Пишет публикация релиза: без проверки прав и с флагом `ИЗ_РЕЛИЗА`. `Why:` у
	Course Creator на схемы только чтение, а мимо публикации схему курса из
	релиза не правит никто (`course_guard.проверить_документ`, learning-services#526).
	"""
	строки, холст = проверить_схему(блоки, canvas)
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
			"layout": layout,
			"blocks": строки,
			"canvas": json.dumps(холст, ensure_ascii=False) if холст else None,
		}
	)
	документ.flags[ИЗ_РЕЛИЗА] = True
	документ.insert(ignore_permissions=True)
	return {"id": документ.name, "course": course, "slug": ключ, "version": документ.version}
