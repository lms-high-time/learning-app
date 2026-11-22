# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Публикация каталога компетенций (learning-services#825).

Порядок: разобрать → схема → проверки сервера → последний выпуск с
блокировкой → тот же каталог? → ключи не пропали → вид правки каждой
компетенции → записи под точкой сохранения: выпуск, области, изменённые
компетенции. Неизменённые компетенции и области не переписываются.

Ключи каталога постоянные: компетенция и область, которых нет в новом
каталоге, — отказ всей публикации, а не удаление. `Why:` ключ компетенции —
её имя в журнале свидетельств навсегда; молча снятая компетенция оставила бы
свидетельства и цели уроков курсов с ссылкой в никуда, а пропажа ключа в
источнике — скорее ошибка сборки, чем намерение.

Точка сохранения и проверки до первой записи — как у публикации релиза
курса (`releases.service`): `@контракт` превращает `Отказ` в успешный ответ,
и Frappe зафиксировал бы записанное до отказа. Записи — без проверки прав
DocType: кто публикует, решает метод (`authoring._публикатор`).
"""

import json

import frappe
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.catalog import checks, read
from lms_frappe_app.agent_learning.doctype.agent_competency.agent_competency import ИЗ_КАТАЛОГА
from lms_frappe_app.agent_learning.errors import ЗАНЯТО, Отказ
from lms_frappe_app.agent_learning.releases import index
from lms_frappe_app.agent_learning.releases import schema as проверка_схемы
from lms_frappe_app.agent_learning.releases import service as релизы

ТОЧКА = "publish_competency_catalog"

КАТАЛОГ_НЕВЕРЕН = "catalog_invalid"
ФОРМАТ_НЕ_ТОТ = "catalog_format_unsupported"
КАТАЛОГ_НЕ_СХОДИТСЯ = "catalog_inconsistent"
КЛЮЧИ_ПРОПАЛИ = "catalog_keys_removed"


def опубликовать(каталог, автор: str, коммит: str | None = None) -> dict:
	"""Каталог — новым выпуском; тот же каталог ещё раз — `unchanged`, без записей.

	`коммит` — проверенный хеш коммита git, из которого собран каталог: пишется
	в новый выпуск; на `unchanged` не пишется, ответ отдаёт коммит выпуска.
	Две публикации идут по очереди: последний выпуск читается с блокировкой
	строки, а первую публикацию держит уникальная версия — проигравшая из двух
	одновременных получает `busy`.
	"""
	каталог = _разобрать(каталог)
	_проверить(каталог)
	дайджест = релизы._дайджест(каталог)
	последний = _последний_с_блокировкой()
	if последний and последний.digest == дайджест:
		return _ответ(последний.name, пусто=True)
	версии = {к.name: к.version for к in frappe.get_all(read.КОМПЕТЕНЦИЯ, fields=["name", "version"])}
	области = {о.name: о.title for о in frappe.get_all(read.ОБЛАСТЬ, fields=["name", "title"])}
	_ключи_на_месте(каталог, set(версии), set(области))
	прежний = json.loads(frappe.db.get_value(read.ВЫПУСК, последний.name, "snapshot")) if последний else None
	изменения = checks.правки(прежний, каталог, версии)

	frappe.db.savepoint(ТОЧКА)
	try:
		выпуск = _записать_выпуск(
			каталог, (последний.version if последний else 0) + 1, дайджест, изменения, автор, коммит
		)
		_записать_области(каталог["areas"], области)
		_записать_компетенции(каталог["competencies"], изменения, выпуск)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		frappe.db.rollback(save_point=ТОЧКА)
		frappe.clear_last_message()
		raise Отказ(ЗАНЯТО, "Каталог сейчас публикует другой запрос: повторите вызов") from None
	except Отказ:
		frappe.db.rollback(save_point=ТОЧКА)
		raise
	frappe.db.release_savepoint(ТОЧКА)
	return _ответ(выпуск, изменения=изменения)


def _разобрать(каталог) -> dict:
	"""Каталог — объект JSON без `NaN` и бесконечностей; иначе `catalog_invalid`."""
	try:
		return релизы._разобрать(каталог)
	except Отказ as отказ:
		raise Отказ(
			КАТАЛОГ_НЕВЕРЕН, отказ.сообщение.replace("Релиз", "Каталог"), **отказ.подробности
		) from None


def _проверить(каталог: dict) -> None:
	if каталог.get("format") != checks.ФОРМАТ:
		raise Отказ(
			ФОРМАТ_НЕ_ТОТ,
			f"Формат каталога — {checks.ФОРМАТ}",
			format=каталог.get("format"),
			supported=[checks.ФОРМАТ],
		)
	if найдено := checks.ошибки_схемы(каталог):
		raise Отказ(
			КАТАЛОГ_НЕВЕРЕН,
			"Каталог не проходит схему",
			errors=найдено[: проверка_схемы.ОШИБОК_НЕ_БОЛЬШЕ],
			total=len(найдено),
		)
	if найдено := checks.проблемы(каталог):
		raise Отказ(
			КАТАЛОГ_НЕ_СХОДИТСЯ,
			"Части каталога не сходятся",
			problems=найдено[: проверка_схемы.ОШИБОК_НЕ_БОЛЬШЕ],
			total=len(найдено),
		)


def _последний_с_блокировкой():
	"""Последний выпуск каталога — `name`, `version`, `digest` — с блокировкой строки; нет — `None`."""
	найдено = frappe.db.sql(
		f"select name, version, digest from `tab{read.ВЫПУСК}` order by version desc limit 1 for update",
		as_dict=True,
	)
	return найдено[0] if найдено else None


def _ключи_на_месте(каталог: dict, компетенции: set[str], области: set[str]) -> None:
	"""Отказ `catalog_keys_removed`, если в каталоге нет опубликованной компетенции или области."""
	нет_компетенций = sorted(компетенции - {к["key"] for к in каталог["competencies"]})
	нет_областей = sorted(области - {о["key"] for о in каталог["areas"]})
	if нет_компетенций or нет_областей:
		raise Отказ(
			КЛЮЧИ_ПРОПАЛИ,
			"Ключи каталога постоянные: опубликованные компетенции и области из каталога не уходят",
			competencies=нет_компетенций,
			areas=нет_областей,
		)


def _записать_выпуск(
	каталог: dict, версия: int, дайджест: str, изменения: dict, автор: str, коммит: str | None
) -> str:
	# Создавать выпуск мимо сервиса не может никто — у доктайпа нет права `create`.
	return (
		frappe.get_doc(
			{
				"doctype": read.ВЫПУСК,
				"version": версия,
				"catalog_format": checks.ФОРМАТ,
				"digest": дайджест,
				"source_commit": коммит,
				"changes": index.в_json(изменения),
				"published_by": автор,
				"published_at": now_datetime(),
				"snapshot": json.dumps(каталог, ensure_ascii=False, allow_nan=False),
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _сохранить(документ) -> None:
	"""Запись каталога — с флагом публикации и без проверки ссылок: ссылки уже сверены
	(`checks.проблемы`), а составляющая может появиться в этом же выпуске позже по порядку."""
	документ.flags[ИЗ_КАТАЛОГА] = True
	документ.flags.ignore_links = True
	документ.save(ignore_permissions=True)


def _записать_области(новые: list[dict], прежние: dict[str, str]) -> None:
	"""Области — новые заводятся, у прежних с другим названием оно меняется."""
	for область in новые:
		if область["key"] not in прежние:
			_сохранить(
				frappe.get_doc(
					{"doctype": read.ОБЛАСТЬ, "area_key": область["key"], "title": область["title"]}
				)
			)
		elif прежние[область["key"]] != область["title"]:
			документ = frappe.get_doc(read.ОБЛАСТЬ, область["key"])
			документ.title = область["title"]
			_сохранить(документ)


def _записать_компетенции(компетенции: list[dict], изменения: dict, выпуск: str) -> None:
	"""Изменённые компетенции — по виду правки (`checks.правки`): новая — версии 1, по
	существу — следующей версии; у обеих версия с этого выпуска. Редакторская правка
	версию не трогает."""
	новые = set(изменения[checks.ДОБАВЛЕНА])
	версии = {п["key"]: п["version"] for п in изменения[checks.ПО_СУЩЕСТВУ]}
	редакторские = set(изменения[checks.РЕДАКТОРСКАЯ])
	for комп in компетенции:
		ключ = комп["key"]
		if ключ in новые:
			документ = frappe.get_doc(
				{"doctype": read.КОМПЕТЕНЦИЯ, "competency_key": ключ, "version": 1, "version_release": выпуск}
			)
		elif ключ in версии or ключ in редакторские:
			документ = frappe.get_doc(read.КОМПЕТЕНЦИЯ, ключ)
			if ключ in версии:
				документ.update({"version": версии[ключ], "version_release": выпуск})
		else:
			continue
		редакторское = checks.редакторское(комп)
		документ.update(
			{
				"title": редакторское["title"],
				"description": редакторское["description"],
				"kind": редакторское["kind"],
				"area": редакторское["area"],
				**{f"level_{уровень}": текст for уровень, текст in checks.по_существу(комп).items()},
			}
		)
		документ.set("parts", редакторское["parts"])
		документ.set("similar", [{"competency": похожая} for похожая in редакторское["similar"]])
		_сохранить(документ)


def _ответ(выпуск: str, изменения: dict | None = None, *, пусто: bool = False) -> dict:
	"""Ответ публикации: выпуск, его версия, дайджест и коммит, правки этой публикации."""
	запись = frappe.db.get_value(read.ВЫПУСК, выпуск, ["version", "digest", "source_commit"], as_dict=True)
	return {
		"catalog_release": выпуск,
		"version": запись.version,
		"digest": запись.digest,
		"commit": запись.source_commit or None,
		"unchanged": пусто,
		"changes": изменения or {checks.ДОБАВЛЕНА: [], checks.ПО_СУЩЕСТВУ: [], checks.РЕДАКТОРСКАЯ: []},
	}
