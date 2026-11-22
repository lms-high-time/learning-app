# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Публикация каталога компетенций (learning-services#825).

Порядок: разобрать → схема → проверки сервера → канонический вид
(`checks.канон`) → последний выпуск со снимком и блокировкой → тот же
каталог? → ключи не пропали → правки (`checks.правки`) → записи под точкой
сохранения: выпуск, изменённые схемы классификации, изменённые компетенции.
Неизменённые компетенции и схемы не переписываются. Снимок выпуска —
канонический вид.

Ключи каталога постоянные: компетенция, схема классификации и класс схемы,
которых нет в новом каталоге, — отказ всей публикации, а не удаление.
`Why:` ключ компетенции — её имя в журнале свидетельств навсегда; молча
снятая компетенция оставила бы свидетельства и цели уроков курсов с ссылкой
в никуда; по классу схемы компетенции отбирают потребители схемы (программа,
отчёт), и пропавший класс оставил бы их отбор пустым. Пропажа ключа в
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
	каталог = checks.канон(каталог)
	дайджест = релизы._дайджест(каталог)
	последний = _последний_с_блокировкой()
	if последний and последний.digest == дайджест:
		return _ответ(последний.name, пусто=True)
	версии = {к.name: к.version for к in frappe.get_all(read.КОМПЕТЕНЦИЯ, fields=["name", "version"])}
	схемы = _опубликованные_схемы()
	_ключи_на_месте(каталог, set(версии), схемы)
	прежний = json.loads(последний.snapshot) if последний else None
	изменения = checks.правки(прежний, каталог, версии, схемы)

	frappe.db.savepoint(ТОЧКА)
	try:
		выпуск = _записать_выпуск(
			каталог, (последний.version if последний else 0) + 1, дайджест, изменения, автор, коммит
		)
		_записать_схемы(каталог["classifications"], изменения["classifications"])
		_записать_компетенции(каталог["competencies"], изменения, выпуск)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		frappe.db.rollback(save_point=ТОЧКА)
		frappe.clear_last_message()
		raise Отказ(ЗАНЯТО, "Каталог сейчас публикует другой запрос: повторите вызов") from None
	except Отказ:
		frappe.db.rollback(save_point=ТОЧКА)
		raise
	frappe.db.release_savepoint(ТОЧКА)
	# Карты курсов на каталоге, прочитанные в этом запросе, — по прежнему выпуску.
	index.сбросить_кэш_карт()
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
	"""Последний выпуск каталога — `name`, `version`, `digest`, `snapshot` — с блокировкой строки;
	нет — `None`. Снимок — тем же запросом: прочитанный отдельно, он мог бы оказаться снимком
	другого выпуска, и правки разошлись бы с базой."""
	найдено = frappe.db.sql(
		f"select name, version, digest, snapshot from `tab{read.ВЫПУСК}` order by version desc limit 1 for update",
		as_dict=True,
	)
	return найдено[0] if найдено else None


def _опубликованные_схемы() -> dict[str, dict]:
	"""Схемы классификации из базы в каноническом виде (`checks.схема_классификации`) по
	ключу. Две выборки: схемы и их классы."""
	классы: dict[str, list[dict]] = {}
	for к in frappe.get_all(
		read.КЛАСС_СХЕМЫ,
		filters={"parenttype": read.СХЕМА},
		fields=["parent", "class_key", "title"],
		order_by="parent asc, idx asc",
	):
		классы.setdefault(к.parent, []).append({"key": к.class_key, "title": к.title})
	return {
		с.name: checks.схема_классификации(
			{
				"key": с.name,
				"title": с.title,
				"source": (с.source or "").splitlines(),
				"required": bool(с.required),
				"classes": классы.get(с.name, []),
			}
		)
		for с in frappe.get_all(read.СХЕМА, fields=["name", "title", "source", "required"])
	}


def _ключи_на_месте(каталог: dict, компетенции: set[str], схемы: dict[str, dict]) -> None:
	"""Отказ `catalog_keys_removed`, если в каталоге нет опубликованной компетенции, схемы
	классификации или класса схемы. Классы — только у схем, которые в каталоге остались:
	пропавшая схема называет себя целиком."""
	нет_компетенций = sorted(компетенции - {к["key"] for к in каталог["competencies"]})
	новые = {с["key"]: {к["key"] for к in с["classes"]} for с in каталог["classifications"]}
	нет_схем = sorted(set(схемы) - set(новые))
	нет_классов = {
		ключ: пропали
		for ключ in sorted(set(схемы) & set(новые))
		if (пропали := sorted({к["key"] for к in схемы[ключ]["classes"]} - новые[ключ]))
	}
	if нет_компетенций or нет_схем or нет_классов:
		raise Отказ(
			КЛЮЧИ_ПРОПАЛИ,
			"Ключи каталога постоянные: опубликованные компетенции, схемы классификации и их "
			"классы из каталога не уходят",
			competencies=нет_компетенций,
			classifications=нет_схем,
			classes=нет_классов,
		)


def _записать_выпуск(
	каталог: dict, версия: int, дайджест: str, изменения: dict, автор: str, коммит: str | None
) -> str:
	# Мимо сервиса выпуск не создаётся: права `create` у доктайпа нет ни у кого, а вставку
	# без флага публикации контроллер отклоняет и администратору.
	документ = frappe.get_doc(
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
	документ.flags[ИЗ_КАТАЛОГА] = True
	return документ.insert(ignore_permissions=True).name


def _сохранить(документ) -> None:
	"""Запись каталога — с флагом публикации и без проверки ссылок: ссылки уже сверены
	(`checks.проблемы`), а составляющая может появиться в этом же выпуске позже по порядку
	(записи идут по ключу)."""
	документ.flags[ИЗ_КАТАЛОГА] = True
	документ.flags.ignore_links = True
	документ.save(ignore_permissions=True)


def _записать_схемы(схемы: list[dict], правки: dict) -> None:
	"""Схемы классификации по правкам (`checks.правки`, `classifications`): новая заводится,
	схема с изменёнными полями, новыми или переименованными классами переписывается целиком —
	классы по каноническому порядку. Классы не уходят (`_ключи_на_месте`), так что
	переписанная таблица классов — прежние классы и новые."""
	новые = set(правки[checks.ДОБАВЛЕНА])
	изменённые = (
		{п["key"] for п in правки["changed"]}
		| set(правки["classes"][checks.ДОБАВЛЕНА])
		| set(правки["classes"]["renamed"])
	)
	for схема in схемы:
		if схема["key"] in новые:
			документ = frappe.get_doc({"doctype": read.СХЕМА, "classification_key": схема["key"]})
		elif схема["key"] in изменённые:
			документ = frappe.get_doc(read.СХЕМА, схема["key"])
		else:
			continue
		документ.update(
			{
				"title": схема["title"],
				"source": "\n".join(схема["source"]),
				"required": int(схема["required"]),
			}
		)
		документ.set("classes", [{"class_key": к["key"], "title": к["title"]} for к in схема["classes"]])
		_сохранить(документ)


def _записать_компетенции(компетенции: list[dict], изменения: dict, выпуск: str) -> None:
	"""Изменённые компетенции — по виду правки (`checks.правки`): новая — версии 1, по
	существу — следующей версии; у обеих версия с этого выпуска. Редакторская правка
	версию не трогает."""
	новые = set(изменения[checks.ДОБАВЛЕНА])
	версии = {п["key"]: п["version"] for п in изменения[checks.ПО_СУЩЕСТВУ]}
	редакторские = {п["key"] for п in изменения[checks.РЕДАКТОРСКАЯ]}
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
		документ.update(
			{
				**{поле: комп[поле] for поле in ("title", "description", "kind")},
				**{f"level_{уровень}": текст for уровень, текст in комп["levels"].items()},
			}
		)
		документ.set(
			"classes",
			[{"classification": с, "class_key": к} for с, к in комп.get("classes", {}).items()],
		)
		документ.set("parts", комп.get("parts", []))
		документ.set("similar", [{"competency": похожая} for похожая in комп.get("similar", [])])
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
		"changes": изменения or checks.пустые_правки(),
	}
