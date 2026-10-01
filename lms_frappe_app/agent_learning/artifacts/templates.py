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

from lms_frappe_app.agent_learning.artifacts import data, overlay, upgrade
from lms_frappe_app.agent_learning.artifacts.canvas import проверить_холст, холст
from lms_frappe_app.agent_learning.artifacts.codes import (
	АРТЕФАКТ_НЕ_НАЙДЕН,
	НЕ_ПРИВЯЗАН,
	НЕВЕРНАЯ_СХЕМА,
	НЕВЕРНЫЕ_ПРАВКИ,
	НЕВЕРНЫЙ_ШАБЛОН,
	ТА_ЖЕ_ВЕРСИЯ,
	ШАБЛОН_НЕ_НАЙДЕН,
)
from lms_frappe_app.agent_learning.artifacts.course import (
	_блоки_схем,
	блок_наружу,
	записать_схему,
	проверить_схему,
	строки_схемы,
)
from lms_frappe_app.agent_learning.doctype.agent_course_artifact.agent_course_artifact import (
	нормализовать_ключ,
)
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
	renamed=None,
	description=None,
) -> dict:
	"""Новая версия шаблона. Контракт — у `api.authoring.set_artifact_template`.

	Описание у версии своё и от родителя не наследуется. `Why:` у наследника
	оно говорит, чем он отличается от базы, а базовое описание сказало бы
	про другой документ.
	"""
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
	переименования = upgrade.разобрать(renamed, template=ключ)
	if переименования:
		upgrade.проверить(переименования, _прошлые_блоки(ключ), блоки, template=ключ)
	документ = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"template": ключ,
			"title": str(title).strip(),
			"description": str(description or "").strip() or None,
			"layout": layout or "sections",
			"blocks": json.dumps(блоки, ensure_ascii=False),
			"canvas": json.dumps(холст_шаблона, ensure_ascii=False) if холст_шаблона else None,
			"note": note or None,
			"renamed": json.dumps(переименования, ensure_ascii=False) if переименования else None,
			**наследование,
		}
	).insert()
	return {"id": документ.name, "template": ключ, "version": документ.version}


def _прошлые_блоки(ключ: str) -> list | None:
	"""Блоки последней записанной версии шаблона; шаблона ещё нет — `None`."""
	try:
		return шаблон(ключ)["blocks"]
	except Отказ:
		return None


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
			"description",
			"layout",
			"blocks",
			"canvas",
			"note",
			"renamed",
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
		"description": запись.description or None,
		"layout": запись.layout or "sections",
		"blocks": _json(запись.blocks) or [],
		"canvas": холст(запись.canvas),
		"note": запись.note or None,
		"renamed": _json(запись.renamed) or None,
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

	Описание — у каждого: по нему агент выбирает шаблон, когда названия
	похожи, — «реестр рисков» и «реестр рисков стройки».

	Курсы — по действующим схемам документов, с версией шаблона, которую
	каждый закрепил: по ним автор видит, кого затронет новая версия.
	"""
	последние: dict[str, dict] = {}
	for запись in frappe.get_all(
		DOCTYPE,
		fields=["template", "version", "title", "description", "note", "extends", "extends_version"],
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
			"description": запись.description or None,
			"version": запись.version,
			"note": запись.note or None,
			"extends": _наследует(запись),
			"courses": курсы.get(ключ, []),
		}
		for ключ, запись in последние.items()
	]


def привязать(
	course: str, artifact: str, template: str, version, правки, purpose: str | None = None
) -> dict:
	"""Схема документа курса из шаблона с правками — новой версией.

	Контракт — у `api.authoring.set_course_artifact_template`. Пишется тем же
	`записать_схему`, что и схема от автора целиком: собранное проверяется и
	хранится так же, и чтение ученика не пересобирает схему на каждый запрос.
	"""
	исходный, правки, собранное = _собрать_привязку(course, template, version, правки)
	return _записать_привязку(course, artifact, исходный, правки, собранное, purpose)


def _собрать_привязку(course: str, template: str, version, правки) -> tuple[dict, dict, dict]:
	"""Версия шаблона, правки курса и собранная из них схема; урок не этого курса — отказ.

	Ничего не пишет: так же собирает схему предпросмотр перехода.
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
	return исходный, правки, собранное


def _записать_привязку(
	course: str,
	artifact: str,
	исходный: dict,
	правки: dict,
	собранное: dict,
	purpose: str | None = None,
) -> dict:
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
		purpose=purpose,
	)
	return {
		"id": версия["id"],
		"course": course,
		"artifact": версия["slug"],
		"version": версия["version"],
		"template": исходный["template"],
		"template_version": исходный["version"],
	}


def перейти(course: str, artifact: str, version=None, dry_run: bool = False) -> dict:
	"""Документ курса — на новую версию своего шаблона, с данными учеников.

	Контракт — у `api.authoring.upgrade_course_artifact`. Переименования
	версий между закреплённой и новой складываются в одно; им
	переименовываются правки курса, и схема собирается заново тем же
	`привязать`. Всё проверяется до записи: правки, которые не собрались с
	новой версией, — отказ, и ничего не меняется. Данные учеников переносятся
	на новые ключи в той же транзакции, что и новая схема.

	`dry_run` (learning-services#383) — всё то же до записи: собранная схема
	проверена, разница посчитана, документы учеников, которые поменялись бы,
	сосчитаны, — но не пишется ничего, и не откатом транзакции, а потому, что
	запись не вызывается. `Why:` переход необратим, и куратор должен увидеть
	разницу до него.
	"""
	ключ = нормализовать_ключ(artifact)
	действующая = frappe.db.get_value(
		"Agent Course Artifact",
		{"course": course, "slug": ключ, "is_active": 1},
		["name", "slug", "version", "template", "template_version", "overlay"],
		as_dict=True,
	)
	if not действующая:
		raise Отказ(АРТЕФАКТ_НЕ_НАЙДЕН, "В этом курсе нет такого документа", artifact=artifact)
	if not действующая.template:
		raise Отказ(
			НЕ_ПРИВЯЗАН,
			"Документ задан схемой целиком, а не шаблоном: привяжите его — set_course_artifact_template",
			artifact=ключ,
		)
	цель = шаблон(действующая.template, version)
	было_версия = действующая.template_version
	if цель["version"] == было_версия:
		raise Отказ(
			ТА_ЖЕ_ВЕРСИЯ, "Курс уже на этой версии шаблона", template=цель["template"], version=было_версия
		)
	if цель["version"] < было_версия:
		# Назад — не переход: переименований в обратную сторону нет, и данные
		# учеников остались бы под ключами новой версии.
		raise Отказ(
			НЕВЕРНЫЙ_ШАБЛОН,
			"Версия ниже закреплённой: откат — set_course_artifact_template с версией",
			template=цель["template"],
			version=цель["version"],
		)

	прежний = шаблон(действующая.template, было_версия)
	переименования = upgrade.сложить(
		[
			_json(запись)
			for запись in frappe.get_all(
				DOCTYPE,
				filters=[
					["template", "=", цель["template"]],
					["version", ">", было_версия],
					["version", "<=", цель["version"]],
				],
				pluck="renamed",
				order_by="version asc",
			)
		]
	)
	правки = upgrade.переименовать_правки(
		разобрать_правки(действующая.overlay),
		переименования,
		upgrade.таблицы_блоков(прежний["blocks"]),
	)
	было = [блок_наружу(с) for с in _блоки_схем([действующая.name]).get(действующая.name, [])]

	исходный, правки, собранное = _собрать_привязку(course, цель["template"], цель["version"], правки)
	строки, _ = проверить_схему(собранное["blocks"], собранное["canvas"])
	стало = [блок_наружу(с) for с in строки]
	разница = upgrade.разница(upgrade.переименовать_схему(было, переименования), стало)

	if dry_run:
		return {
			"id": None,
			"course": course,
			"artifact": ключ,
			"version": действующая.version,
			"template": цель["template"],
			"template_version": цель["version"],
			"from_version": было_версия,
			"diff": разница,
			"students": _перенести_данные(course, ключ, переименования, записать=False),
			"dry_run": True,
		}
	записано = _записать_привязку(course, ключ, исходный, правки, собранное)
	return {
		**записано,
		"from_version": было_версия,
		"diff": разница,
		"students": _перенести_данные(course, ключ, переименования),
	}


def _перенести_данные(course: str, artifact: str, переименования: dict, записать: bool = True) -> int:
	"""Документы учеников по курсу и ключу — на ключи новой версии; сколько поменялось.

	Пишется в обход контроллера и без отметки изменения: ученик ничего не
	правил, и документ не должен выглядеть тронутым им. Без `записать` —
	только считается, теми же переименованиями: так предпросмотр называет
	то же число, что назовёт переход.
	"""
	if not переименования:
		return 0
	блоки = переименования.get("blocks") or {}
	перенесено = 0
	for документ in frappe.get_all(
		"Agent Student Artifact",
		filters={"course": course, "artifact": artifact},
		fields=["name", "data"],
	):
		тронут = False
		if документ.data:
			прежние = data.данные(документ.data)
			новые = upgrade.переименовать_данные(прежние, переименования)
			тронут = новые != прежние
			if тронут and записать:
				frappe.db.set_value(
					"Agent Student Artifact",
					документ.name,
					"data",
					json.dumps(новые, ensure_ascii=False),
					update_modified=False,
				)
		if блоки:
			строки = frappe.get_all(
				"Agent Artifact Content",
				filters={"parent": документ.name, "parenttype": "Agent Student Artifact"},
				fields=["name", "block_key"],
			)
			переименовать = [с for с in строки if с.block_key in блоки]
			тронут = тронут or bool(переименовать)
			if записать:
				_переименовать_блоки(строки, переименовать, блоки)
		if тронут:
			перенесено += 1
	return перенесено


def _переименовать_блоки(строки: list, переименовать: list, блоки: dict) -> None:
	"""Тексты блоков ученика — под новые ключи блоков."""
	занятые = {блоки[с.block_key] for с in переименовать}
	for строка in строки:
		# Под новым ключом — текст блока, давно убранного из схемы:
		# ученик его не видит, а ключ теперь у того, что он заполнял.
		if строка.block_key in занятые and строка.block_key not in блоки:
			frappe.db.delete("Agent Artifact Content", {"name": строка.name})
	for строка in переименовать:
		frappe.db.set_value(
			"Agent Artifact Content",
			строка.name,
			"block_key",
			блоки[строка.block_key],
			update_modified=False,
		)


def последние_версии(шаблоны: set[str]) -> dict[str, dict]:
	"""Последняя версия каждого названного шаблона с её пояснением — одним запросом."""
	последние: dict[str, dict] = {}
	if not шаблоны:
		return последние
	for запись in frappe.get_all(
		DOCTYPE,
		filters={"template": ("in", sorted(шаблоны))},
		fields=["template", "version", "note"],
		order_by="version asc",
	):
		последние[запись.template] = {"version": запись.version, "note": запись.note or None}
	return последние


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
