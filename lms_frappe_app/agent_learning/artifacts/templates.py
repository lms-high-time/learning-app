# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Шаблоны документов и привязка к ним курса (learning-services#370).

Шаблон — схема документа без уроков, версиями: каждая запись — новая версия,
прежние не меняются. Курс закрепляет версию шаблона и хранит правки к ней;
итоговая схема собирается при записи привязки (`overlay.собрать`) и ложится
туда же, куда ложится схема от автора целиком, — ученик, страница и выгрузка
о шаблонах не знают.

Наследник (learning-services#375) — шаблон, заданный правками к закреплённой
версии другого шаблона, в том же формате, что правки курса, но без уроков.
Его схема собирается при записи и хранится в записи шаблона целиком, как у
любого шаблона: курс привязывается к наследнику так же, и никто не собирает
схему дважды. Новая версия родителя наследника не меняет — он переходит на
неё своей новой версией.

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


def записать_шаблон(
	template: str,
	title: str,
	блоки: list,
	layout: str,
	canvas,
	note: str | None,
	extends: str | None = None,
	extends_version=None,
	правки=None,
) -> dict:
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
	наследование = {"extends": None, "extends_version": None, "overlay": None}
	if extends not in (None, ""):
		родитель, правки = _родитель(ключ, extends, extends_version, блоки, canvas, правки)
		собранное = собрать_наследника(родитель, правки)
		# Раскладка наследника — родителя или из правок, а не `layout`: его
		# MCP шлёт всегда, и наследник холста молча стал бы столбцом.
		блоки, холст_шаблона, layout = собранное["blocks"], собранное["canvas"], собранное["layout"]
		наследование = {
			"extends": родитель["template"],
			"extends_version": родитель["version"],
			"overlay": json.dumps(правки, ensure_ascii=False),
		}
	else:
		if extends_version not in (None, "") or правки not in (None, "", {}):
			raise Отказ(
				НЕВЕРНЫЙ_ШАБЛОН,
				"extends_version и overlay — только у наследника: назовите extends",
				template=ключ,
			)
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
			**наследование,
		}
	).insert()
	return {"id": документ.name, "template": ключ, "version": документ.version}


def _родитель(ключ: str, extends, extends_version, блоки: list, canvas, правки) -> tuple[dict, dict]:
	"""Версия родителя наследника и правки к ней; наследник не по правилам — отказ.

	Наследник наследника — отказ. `Why:` двухуровневое наследование уже не
	читается глазами: чтобы понять документ, пришлось бы собирать три схемы в
	уме. Блоков и холста у наследника нет: его схема — родитель с правками, и
	второй источник схемы разошёлся бы с первым.
	"""
	if ключ_шаблона(extends) == ключ:
		raise Отказ(НЕВЕРНЫЙ_ШАБЛОН, "Шаблон не наследует сам себя", template=ключ)
	if блоки or canvas:
		raise Отказ(
			НЕВЕРНЫЙ_ШАБЛОН,
			"У наследника нет своих блоков и холста: правки родителя — в overlay",
			template=ключ,
		)
	родитель = шаблон(extends, extends_version)
	if родитель["extends"]:
		raise Отказ(
			НЕВЕРНЫЙ_ШАБЛОН,
			"Наследник наследника не заводится: наследуйте родителя",
			template=ключ,
			extends=родитель["template"],
		)
	правки = разобрать_правки(правки)
	if "title" in правки:
		raise Отказ(НЕВЕРНЫЕ_ПРАВКИ, "Название наследника — в title, а не в overlay", name="title")
	return родитель, правки


def собрать_наследника(родитель: dict, правки: dict) -> dict:
	"""Схема наследника — родитель с правками: `{layout, blocks, canvas}` в
	каноническом виде, как её хранит запись шаблона.

	Уроков в правках наследника нет: они — дело курса.
	"""
	overlay.без_уроков_в_правках(правки)
	собранное = overlay.собрать(родитель, правки)
	блоки, холст_шаблона = проверить_шаблон(собранное["blocks"], собранное["canvas"])
	return {"layout": собранное["layout"] or "sections", "blocks": блоки, "canvas": холст_шаблона}


def шаблон(template: str, version=None) -> dict:
	"""Версия шаблона целиком — последняя, если номер не назван; нет — отказ."""
	ключ = ключ_шаблона(template)
	фильтры = {"template": ключ}
	if version not in (None, ""):
		фильтры["version"] = frappe.utils.cint(version)
	записи = frappe.get_all(
		DOCTYPE,
		filters=фильтры,
		fields=[
			"name",
			"template",
			"version",
			"title",
			"layout",
			"blocks",
			"canvas",
			"note",
			"extends",
			"extends_version",
			"overlay",
			"creation",
		],
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
		# Наследник: родитель с закреплённой версией и правки к нему. Схема
		# выше — уже собранная: читать её, не собирая, может каждый.
		"extends": _наследует(запись),
		"overlay": _json(запись.overlay) if запись.extends else None,
		"created": запись.creation.isoformat(),
	}


def _наследует(запись) -> dict | None:
	if not запись.extends:
		return None
	return {"template": запись.extends, "version": запись.extends_version}


def шаблоны() -> list[dict]:
	"""Последняя версия каждого шаблона и курсы, привязанные к нему.

	Курсы — по действующим схемам документов, с версией шаблона, которую
	каждый закрепил: по ним автор видит, кого затронет новая версия.
	"""
	последние: dict[str, dict] = {}
	for запись in frappe.get_all(
		DOCTYPE,
		fields=["template", "version", "title", "note", "extends", "extends_version"],
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
			"extends": _наследует(запись),
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
