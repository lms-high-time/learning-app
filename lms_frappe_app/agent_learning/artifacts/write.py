# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Запись в документ ученика: строки и поля раздела, очистка раздела.

Права — у вызывающего: методы API проверяют доступ к курсу и пространство до
записи.
"""

from __future__ import annotations

import json

import frappe
from frappe import _

from lms_frappe_app.agent_learning.artifacts import data, fill, schema
from lms_frappe_app.agent_learning.artifacts.codes import ОЧИСТКА_С_ЗАПИСЬЮ, ПРАВКА_ЗАПОЛНЕННОГО
from lms_frappe_app.agent_learning.artifacts.course import _блок_схемы
from lms_frappe_app.agent_learning.artifacts.document import _данные, _заполненность, _экземпляр
from lms_frappe_app.agent_learning.errors import Отказ


def записать_блок(
	ученик: str,
	course: str,
	пространство: str | None,
	artifact: str,
	key: str,
	rows=None,
	delete_rows=None,
	fields=None,
	clear=False,
	fill_only=False,
) -> dict:
	"""Строки или поля раздела документа ученика — или очистка раздела.

	`fill_only` — только дописать: новые строки и пустые клетки. Удаление,
	очистка и правка заполненного тогда — отказ `artifact_overwrite` со
	списком того, что запись изменила бы (`touched`).

	Контракт — у `api.student.update_artifact`; доступ к курсу и пространство
	проверяет он. Ответ: заполненность документа, чего не хватает разделу
	(`empty_cells`) и ID строк, которые завела запись (`created`).
	"""
	схема, блок = _блок_схемы(course, artifact, key)
	раздел = schema.раздел(блок)
	документ = _экземпляр(ученик, course, схема.slug, пространство)
	прежние = _данные(документ)
	# Флаги приходят и булевыми из JSON, и строкой из формы.
	очистка = clear in (True, 1, "1", "true")
	только_дописать = fill_only in (True, 1, "1", "true")
	if очистка and только_дописать:
		raise Отказ(
			ПРАВКА_ЗАПОЛНЕННОГО,
			_("Clearing a section isn't allowed in fill-only mode"),
			artifact=схема.slug,
			key=раздел["key"],
			touched=["clear"],
		)
	if очистка:
		if any(значение not in (None, "", [], {}) for значение in (rows, delete_rows, fields)):
			raise Отказ(
				ОЧИСТКА_С_ЗАПИСЬЮ,
				_("Clearing a section takes no values: pass either rows, delete_rows, fields, or clear"),
				artifact=схема.slug,
				key=раздел["key"],
			)
		новые = data.очистить(раздел, прежние)
		созданы = []
	else:
		новые = data.записать(раздел, прежние, rows=rows, delete_rows=delete_rows, fields=fields)
		if только_дописать and (затронуто := data.затронутое(раздел, прежние, новые)):
			raise Отказ(
				ПРАВКА_ЗАПОЛНЕННОГО,
				_("This write changes filled-in values or deletes rows — that's an edit, not fill-only"),
				artifact=схема.slug,
				key=раздел["key"],
				touched=затронуто,
			)
		было = {с["id"] for с in прежние["tables"].get(раздел["key"]) or []}
		созданы = [с["id"] for с in новые["tables"].get(раздел["key"]) or [] if с["id"] not in было]

	# Очистка раздела в документе, которого ещё нет, документ не заводит:
	# результат тот же, что после очистки.
	if документ or not очистка:
		документ = документ or _новый_документ(ученик, course, схема, пространство)
		документ.data = json.dumps(новые, ensure_ascii=False)
		документ.schema_version = схема.name
		документ.save(ignore_permissions=True)
	return {
		"artifact": схема.slug,
		"key": раздел["key"],
		**_заполненность(схема, новые),
		"empty_cells": fill.пустые_клетки(раздел, новые),
		"created": созданы,
	}


def _новый_документ(ученик: str, course: str, схема, пространство: str | None):
	return frappe.get_doc(
		{
			"doctype": "Agent Student Artifact",
			"student": ученик,
			"course": course,
			"artifact": схема.slug,
			"organization": пространство,
		}
	)
