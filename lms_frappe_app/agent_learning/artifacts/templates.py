# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Шаблоны документов и привязка к ним курса (learning-services#370).

Шаблон — схема документа без уроков, версиями: каждая запись — новая версия,
прежние не меняются. Курс закрепляет версию шаблона и хранит правки к ней;
итоговая схема собирается при записи привязки (`overlay.собрать`) и ложится
туда же, куда ложится схема от автора целиком, — ученик, страница и выгрузка
о шаблонах не знают.

Права не проверяются здесь, как и во всём пакете: роль автора проверяют
методы `api.authoring`.
"""

from __future__ import annotations

import json
import re

import frappe

from lms_frappe_app.agent_learning.artifacts import overlay
from lms_frappe_app.agent_learning.artifacts.canvas import проверить_холст, холст
from lms_frappe_app.agent_learning.artifacts.codes import (
	НЕВЕРНАЯ_СХЕМА,
	НЕВЕРНЫЕ_ПРАВКИ,
	НЕВЕРНЫЙ_ШАБЛОН,
	ШАБЛОН_НЕ_НАЙДЕН,
)
from lms_frappe_app.agent_learning.artifacts.course import блок_наружу, записать_схему, строки_схемы
from lms_frappe_app.agent_learning.errors import УРОК_НЕ_НАЙДЕН, Отказ

DOCTYPE = "Agent Artifact Template"

#: Дефис разрешён в отличие от ключа документа курса: шаблон называют
#: словами (`risk-register`), а ключ документа — идентификатор в данных ученика.
КЛЮЧ_ШАБЛОНА = re.compile(r"^[a-z][a-z0-9_-]{0,59}$")


def ключ_шаблона(значение) -> str:
	return str(значение or "").strip().lower()


def проверить_шаблон(блоки: list, canvas) -> tuple[list[dict], dict | None]:
	"""Блоки и холст шаблона в каноническом виде; неверные — отказ.

	Проверка та же, что у схемы от автора, — блоки, поля, колонки, холст, —
	плюс то, что у шаблона своё: уроков нет, ключи блоков не повторяются.
	`Why:` у схемы курса повтор ключа ловит контроллер при записи, а шаблон
	пишется JSON-ом, и повтор дошёл бы до курса, собирающего из него схему.
	"""
	блоки = [json.loads(блок) if isinstance(блок, str) else dict(блок or {}) for блок in блоки]
	if not блоки:
		raise Отказ(НЕВЕРНАЯ_СХЕМА, "Шаблону нужны блоки", key="blocks")
	overlay.без_уроков(блоки)
	ключи = []
	for блок in блоки:
		ключ = str(блок.get("key") or "").strip().lower()
		if not ключ:
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "У каждого блока шаблона есть ключ", key=блок.get("key"))
		if ключ in ключи:
			raise Отказ(НЕВЕРНАЯ_СХЕМА, "Ключи блоков шаблона не повторяются", key=ключ)
		ключи.append(ключ)
		блок["key"] = ключ
	строки = строки_схемы(блоки)
	холст_шаблона = проверить_холст(canvas, строки)
	наружу = []
	for строка in строки:
		блок = блок_наружу(строка)
		блок.pop("lesson")
		наружу.append(блок)
	return наружу, холст_шаблона


def записать_шаблон(template: str, title: str, блоки: list, layout: str, canvas, note: str | None) -> dict:
	"""Новая версия шаблона. Контракт — у `api.authoring.set_artifact_template`."""
	ключ = ключ_шаблона(template)
	if not КЛЮЧ_ШАБЛОНА.match(ключ):
		raise Отказ(
			НЕВЕРНЫЙ_ШАБЛОН,
			"Ключ шаблона — латиница в нижнем регистре, цифры, «_» и «-», до 60 знаков",
			template=template,
		)
	if not str(title or "").strip():
		raise Отказ(НЕВЕРНЫЙ_ШАБЛОН, "У шаблона нужно название", template=ключ)
	блоки, холст_шаблона = проверить_шаблон(блоки, canvas)
	документ = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"template": ключ,
			"title": str(title).strip(),
			"layout": layout or "sections",
			"blocks": json.dumps(блоки, ensure_ascii=False),
			"canvas": json.dumps(холст_шаблона, ensure_ascii=False) if холст_шаблона else None,
			"note": note or None,
		}
	).insert()
	return {"id": документ.name, "template": ключ, "version": документ.version}


def шаблон(template: str, version=None) -> dict:
	"""Версия шаблона целиком — последняя, если номер не назван; нет — отказ."""
	ключ = ключ_шаблона(template)
	фильтры = {"template": ключ}
	if version not in (None, ""):
		фильтры["version"] = frappe.utils.cint(version)
	записи = frappe.get_all(
		DOCTYPE,
		filters=фильтры,
		fields=["name", "template", "version", "title", "layout", "blocks", "canvas", "note", "creation"],
		order_by="version desc",
		limit=1,
	)
	if not записи:
		raise Отказ(ШАБЛОН_НЕ_НАЙДЕН, "Такого шаблона нет", template=template, version=version or None)
	запись = записи[0]
	return {
		"template": запись.template,
		"version": запись.version,
		"title": запись.title,
		"layout": запись.layout or "sections",
		"blocks": _json(запись.blocks) or [],
		"canvas": холст(запись.canvas),
		"note": запись.note or None,
		"created": запись.creation.isoformat(),
	}


def шаблоны() -> list[dict]:
	"""Последняя версия каждого шаблона и курсы, привязанные к нему.

	Курсы — по действующим схемам документов, с версией шаблона, которую
	каждый закрепил: по ним автор видит, кого затронет новая версия.
	"""
	последние: dict[str, dict] = {}
	for запись in frappe.get_all(
		DOCTYPE,
		fields=["template", "version", "title", "note"],
		order_by="template asc, version desc",
	):
		последние.setdefault(запись.template, запись)

	привязки = frappe.get_all(
		"Agent Course Artifact",
		filters={"is_active": 1, "template": ("is", "set")},
		fields=["course", "slug", "template", "template_version"],
		order_by="course asc, slug asc",
	)
	названия = dict(
		frappe.get_all(
			"LMS Course",
			filters={"name": ("in", sorted({п.course for п in привязки}))},
			fields=["name", "title"],
			as_list=True,
		)
		if привязки
		else []
	)
	курсы: dict[str, list[dict]] = {}
	for привязка in привязки:
		курсы.setdefault(привязка.template, []).append(
			{
				"course": привязка.course,
				"course_title": названия.get(привязка.course),
				"artifact": привязка.slug,
				"version": привязка.template_version,
			}
		)
	return [
		{
			"template": ключ,
			"title": запись.title,
			"version": запись.version,
			"note": запись.note or None,
			"courses": курсы.get(ключ, []),
		}
		for ключ, запись in последние.items()
	]


def привязать(course: str, artifact: str, template: str, version, правки) -> dict:
	"""Схема документа курса из шаблона с правками — новой версией.

	Контракт — у `api.authoring.set_course_artifact_template`. Пишется тем же
	`записать_схему`, что и схема от автора целиком: собранное проверяется и
	хранится так же, и чтение ученика не пересобирает схему на каждый запрос.
	"""
	исходный = шаблон(template, version)
	правки = разобрать_правки(правки)
	собранное = overlay.собрать(исходный, правки)
	for блок in собранное["blocks"]:
		урок = блок.get("lesson")
		# Урок — только этого курса: правки курса ссылаются на его уроки, и
		# урок соседнего курса у ученика этого курса не откроется.
		if урок and (
			not isinstance(урок, str) or frappe.db.get_value("Course Lesson", урок, "course") != course
		):
			raise Отказ(УРОК_НЕ_НАЙДЕН, "В этом курсе нет такого урока", id=урок)
	версия = записать_схему(
		course,
		artifact,
		собранное["title"],
		собранное["blocks"],
		собранное["layout"],
		собранное["canvas"],
		привязка={
			"template": исходный["template"],
			"template_version": исходный["version"],
			"overlay": правки,
		},
	)
	return {
		"id": версия["id"],
		"course": course,
		"artifact": версия["slug"],
		"version": версия["version"],
		"template": исходный["template"],
		"template_version": исходный["version"],
	}


def разобрать_правки(правки) -> dict:
	"""Правки курса — словарём; строкой JSON они приезжают из формы."""
	if правки in (None, ""):
		return {}
	if isinstance(правки, str):
		try:
			правки = json.loads(правки)
		except ValueError as ошибка:
			raise Отказ(НЕВЕРНЫЕ_ПРАВКИ, "Правки курса — JSON") from ошибка
	if not isinstance(правки, dict):
		raise Отказ(НЕВЕРНЫЕ_ПРАВКИ, "Правки курса — объект")
	return правки


def _json(значение):
	if isinstance(значение, str):
		try:
			return json.loads(значение)
		except ValueError:
			return None
	return значение
