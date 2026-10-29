# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Данные документа ученика: чтение, запись раздела и очистка.

Документ ученика хранит данные JSON-ом:

	{"tables": {"agreements": [{"id": "A1", "topic": "…"}]},
	 "fields": {"default_method": "…"},
	 "seq": {"agreements": 1}}

`tables` — строки разделов `many` по ключу раздела, `fields` — поля разделов
`one` по ключу колонки (он уникален в документе, `schema.проверить_документ`),
`seq` — последний выданный номер строки раздела. Пустые клетки и поля не
хранятся.
"""

from __future__ import annotations

import json
import re

from frappe import _

from lms_frappe_app.agent_learning.artifacts.codes import (
	НЕ_ТОТ_РАЗДЕЛ,
	НЕВЕРНОЕ_ЗНАЧЕНИЕ,
	НЕТ_КОЛОНКИ,
	НЕТ_ПОЛЯ,
	НЕТ_СТРОКИ,
	НЕЧЕГО_ПИСАТЬ,
	СТРОК_СЛИШКОМ_МНОГО,
)
from lms_frappe_app.agent_learning.artifacts.schema import МНОГО, ОДИН
from lms_frappe_app.agent_learning.artifacts.values import привести, пусто
from lms_frappe_app.agent_learning.errors import Отказ

#: Документ курса — журнал, а не база: больше строк почти наверняка ошибка
#: агента, и платить за неё контекстом незачем.
СТРОК_МАКС = 200


def данные(сырые) -> dict:
	"""JSON документа ученика — в словарь с тремя разделами."""
	if isinstance(сырые, str):
		try:
			сырые = json.loads(сырые) if сырые.strip() else {}
		except ValueError:
			сырые = {}
	сырые = сырые if isinstance(сырые, dict) else {}
	return {
		"tables": dict(сырые.get("tables") or {}),
		"fields": dict(сырые.get("fields") or {}),
		"seq": dict(сырые.get("seq") or {}),
	}


def строки(раздел: dict, д: dict) -> list[dict]:
	"""Строки раздела `many`: ID и непустые клетки колонок схемы.

	Клетка колонки, убранной из схемы, в базе остаётся и вернётся вместе с
	колонкой, но наружу не выходит.
	"""
	ключи = [к["key"] for к in раздел["columns"]]
	return [
		{"id": с["id"], **{к: с[к] for к in ключи if not пусто(с.get(к))}}
		for с in д["tables"].get(раздел["key"]) or []
	]


def поля(раздел: dict, д: dict) -> dict:
	"""Непустые поля раздела `one` по ключам колонок."""
	return {
		к["key"]: д["fields"][к["key"]] for к in раздел["columns"] if not пусто(д["fields"].get(к["key"]))
	}


def префикс(раздел: dict) -> str:
	"""Начало ID строки раздела: первая буква его ключа в верхнем регистре."""
	return раздел["key"][:1].upper()


def _следующий_id(раздел: dict, д: dict, занятые: list[dict]) -> str:
	# Номера не переиспользуются: удалённая A3 не вернётся другой строкой,
	# иначе ссылки на неё в разговоре поехали бы на чужую.
	номер = (
		max(
			int(д["seq"].get(раздел["key"]) or 0),
			*(int(re.sub(r"\D", "", с["id"]) or 0) for с in занятые),
			0,
		)
		+ 1
	)
	д["seq"][раздел["key"]] = номер
	return f"{префикс(раздел)}{номер}"


def записать(раздел: dict, сырые, rows=None, delete_rows=None, fields=None) -> dict:
	"""Новые данные документа после записи в раздел.

	`rows` и `delete_rows` — у раздела `many`: строки без `id` заводятся,
	со `id` — правятся названные клетки; ID на удаление. `fields` — у раздела
	`one`: значения полей. Всё проверяется до записи: либо данные меняются
	целиком, либо отказ и ничего.
	"""
	rows = _список(rows, "rows")
	delete_rows = _список(delete_rows, "delete_rows")
	fields = _словарь(fields, "fields")
	if not (rows or delete_rows or fields):
		raise Отказ(
			НЕЧЕГО_ПИСАТЬ, _("Nothing to write: pass rows, delete_rows, fields, or clear"), key=раздел["key"]
		)
	if раздел["rows"] == ОДИН and (rows or delete_rows):
		raise Отказ(
			НЕ_ТОТ_РАЗДЕЛ, _("This section is filled in once: fields only"), key=раздел["key"], rows=ОДИН
		)
	if раздел["rows"] == МНОГО and fields:
		raise Отказ(
			НЕ_ТОТ_РАЗДЕЛ,
			_("This section is kept in rows: rows and delete_rows only"),
			key=раздел["key"],
			rows=МНОГО,
		)

	д = данные(сырые)
	свои = [к["key"] for к in раздел["columns"]]
	if fields:
		for имя, значение in fields.items():
			if имя not in свои:
				raise Отказ(
					НЕТ_ПОЛЯ, _("This section has no such field"), key=раздел["key"], field=имя, fields=свои
				)
			приведено = привести(значение, field=имя)
			if приведено is None:
				д["fields"].pop(имя, None)
			else:
				д["fields"][имя] = приведено
		return д

	текущие = [dict(с) for с in д["tables"].get(раздел["key"]) or []]
	for id_строки in delete_rows:
		найдено = _найти(текущие, id_строки)
		if найдено is None:
			raise Отказ(НЕТ_СТРОКИ, _("There's no such row"), key=раздел["key"], row=id_строки)
		текущие.remove(найдено)
	for строка in rows:
		строка = _словарь(строка, "rows")
		id_строки = str(строка.pop("id", "") or "").strip()
		for имя in строка:
			if имя not in свои:
				raise Отказ(
					НЕТ_КОЛОНКИ,
					_("This section has no such column"),
					key=раздел["key"],
					column=имя,
					columns=свои,
				)
		if id_строки:
			цель = _найти(текущие, id_строки)
			if цель is None:
				raise Отказ(НЕТ_СТРОКИ, _("There's no such row"), key=раздел["key"], row=id_строки)
		else:
			if len(текущие) >= СТРОК_МАКС:
				raise Отказ(
					СТРОК_СЛИШКОМ_МНОГО,
					_("No more than {0} rows").format(СТРОК_МАКС),
					key=раздел["key"],
					max=СТРОК_МАКС,
				)
			цель = {"id": _следующий_id(раздел, д, текущие)}
			текущие.append(цель)
		for имя, значение in строка.items():
			приведено = привести(значение, column=имя)
			if приведено is None:
				цель.pop(имя, None)
			else:
				цель[имя] = приведено
	д["tables"][раздел["key"]] = текущие
	return д


def затронутое(раздел: dict, прежние, новые) -> list[str]:
	"""Что из заполненного раздела запись меняет: удалённые строки и клетки
	или поля, у которых было значение, а стало другое или пусто.

	Пусто — запись только дописывает: новые строки и пустые клетки. `Why:`
	дописывание и правка заполненного — разные по риску действия, и клиенту
	нужно знать, когда спрашивать подтверждение (learning-services#614).
	"""
	было, стало = данные(прежние), данные(новые)
	if раздел["rows"] == ОДИН:
		return [
			к["key"]
			for к in раздел["columns"]
			if not пусто(было["fields"].get(к["key"]))
			and было["fields"].get(к["key"]) != стало["fields"].get(к["key"])
		]
	новые_строки = {с["id"]: с for с in стало["tables"].get(раздел["key"]) or []}
	итог = []
	for строка in было["tables"].get(раздел["key"]) or []:
		после = новые_строки.get(строка["id"])
		if после is None:
			итог.append(строка["id"])
			continue
		итог += [
			f"{строка['id']}.{имя}"
			for имя, значение in строка.items()
			if имя != "id" and not пусто(значение) and после.get(имя) != значение
		]
	return итог


def _найти(строки_раздела: list[dict], id_строки) -> dict | None:
	искомый = str(id_строки or "").strip().lower()
	return next((с for с in строки_раздела if str(с.get("id", "")).lower() == искомый), None)


def очистить(раздел: dict, сырые) -> dict:
	"""Данные документа без значений раздела: у `many` — все его строки, у
	`one` — все его поля. Счётчик строк остаётся: номера не переиспользуются.
	"""
	д = данные(сырые)
	if раздел["rows"] == МНОГО:
		д["tables"].pop(раздел["key"], None)
	else:
		for колонка in раздел["columns"]:
			д["fields"].pop(колонка["key"], None)
	return д


def _список(значение, имя: str) -> list:
	if значение in (None, "", []):
		return []
	if isinstance(значение, str):
		try:
			значение = json.loads(значение)
		except ValueError as ошибка:
			raise Отказ(НЕВЕРНОЕ_ЗНАЧЕНИЕ, _("{0} must be a list").format(имя), param=имя) from ошибка
	if not isinstance(значение, list):
		raise Отказ(НЕВЕРНОЕ_ЗНАЧЕНИЕ, _("{0} must be a list").format(имя), param=имя)
	return значение


def _словарь(значение, имя: str) -> dict:
	if значение in (None, "", {}):
		return {}
	if isinstance(значение, str):
		try:
			значение = json.loads(значение)
		except ValueError as ошибка:
			raise Отказ(НЕВЕРНОЕ_ЗНАЧЕНИЕ, _("{0} must be an object").format(имя), param=имя) from ошибка
	if not isinstance(значение, dict):
		raise Отказ(НЕВЕРНОЕ_ЗНАЧЕНИЕ, _("{0} must be an object").format(имя), param=имя)
	return dict(значение)
