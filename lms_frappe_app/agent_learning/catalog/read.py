# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Чтение опубликованного каталога компетенций (learning-services#825).

Каталог пишет только публикация (`catalog.service`); здесь — чтение
последнего выпуска и всё, чем каталог служит релизу курса:

- проверка ссылок релиза на каталог при публикации (`проблемы_релиза`);
- карта курса на каталоге (`карты_по_целям`): релиз на каталоге несёт только
  цели уроков `{competency, target}` со слагами, а определения компетенций
  берутся из каталога при чтении. Курс видит последнюю опубликованную
  версию каталога: ссылка релиза на выпуск (`competency_catalog`) — для
  воспроизводимости, а не для показа.
"""

import json

import frappe

ВЫПУСК = "Agent Competency Catalog Release"
КОМПЕТЕНЦИЯ = "Agent Competency"
ОБЛАСТЬ = "Agent Competency Area"


#: Поля компетенции каталога, которые отдаёт карта курса.
ПОЛЯ_КОМПЕТЕНЦИИ = [
	"name",
	"title",
	"description",
	"kind",
	"area",
	"version",
	"level_1",
	"level_2",
	"level_3",
	"level_4",
]

ВЕРСИЯ_НЕИЗВЕСТНА = "catalog_version_unknown"
ВЕРСИЯ_УСТАРЕЛА = "catalog_version_stale"
ПРЕФИКС_ИМЕНИ = "catalog-"


def имя_выпуска(версия: int) -> str:
	"""Имя записи выпуска каталога версии `версия` — `autoname` доктайпа `catalog-{version}`."""
	return f"{ПРЕФИКС_ИМЕНИ}{int(версия)}"


def версия_выпуска(имя: str | None) -> int | None:
	"""Версия выпуска каталога по имени его записи — обратное к `имя_выпуска`; пусто — `None`."""
	return int(имя.removeprefix(ПРЕФИКС_ИМЕНИ)) if имя else None


def проблемы_релиза(релиз: dict) -> list[dict]:
	"""Ссылки релиза курса на каталог (`catalog`) — `[{code, where, …}]`, как у `releases.checks`.

	- `catalog` — версия последнего выпуска каталога: курс видит последнюю, и
	  конвейер выпускает изменённый каталог перед курсом. Выпуска такой версии нет —
	  `catalog_version_unknown`, есть выпуск новее — `catalog_version_stale` (`latest`).
	- Слаг цели урока — компетенция опубликованного каталога, иначе `broken_ref`.
	- Ключ контекста курса в срезе пакета агента (`agent.lessons.<урок>.competencies`) —
	  компетенция карты курса: цель урока или её составляющая по каталогу, как у карты
	  внутри релиза; иначе `broken_ref`.

	Сравнение ключей — с учётом регистра. Три выборки: последняя версия, компетенции
	целей, составляющие каталога.
	"""
	найдено: list[dict] = []
	версия = int(релиз["catalog"])
	последняя = frappe.db.sql(f"select max(version) from `tab{ВЫПУСК}`")[0][0]
	if not последняя or версия > последняя:
		найдено.append(
			{
				"code": ВЕРСИЯ_НЕИЗВЕСТНА,
				"where": "catalog",
				"version": версия,
				"message": "выпуск каталога этой версии не опубликован",
			}
		)
	elif версия < последняя:
		найдено.append(
			{
				"code": ВЕРСИЯ_УСТАРЕЛА,
				"where": "catalog",
				"version": версия,
				"latest": последняя,
				"message": "релиз собран не по последнему выпуску каталога",
			}
		)
	цели = [
		(f"lessons[{у['key']}].competencies", ц["competency"])
		for у in релиз["lessons"]
		for ц in у.get("competencies") or []
	]
	контекст: list[tuple[str, str]] = []
	пакет = релиз.get("agent")
	срезы = пакет.get("lessons") if isinstance(пакет, dict) else None
	# Пакет непрозрачен: сверяются только ключи частей-объектов, как в `releases.checks`.
	for ключ, срез in срезы.items() if isinstance(срезы, dict) else []:
		свои = срез.get("competencies") if isinstance(срез, dict) else None
		if isinstance(свои, dict):
			контекст += [(f"agent.lessons.{ключ}.competencies", компетенция) for компетенция in свои]
	if not цели and not контекст:
		return найдено
	# Сравнение — в Python: сравнение строк в MariaDB не различает регистр.
	известные = (
		set(frappe.get_all(КОМПЕТЕНЦИЯ, filters={"name": ("in", list({к for _, к in цели}))}, pluck="name"))
		if цели
		else set()
	)
	найдено += [
		{"code": "broken_ref", "where": где, "key": ключ} for где, ключ in цели if ключ not in известные
	]
	карта = set(
		_замкнуть(list(dict.fromkeys(к for _, к in цели if к in известные)), _части_каталога())
		if контекст
		else ()
	)
	найдено += [
		{"code": "broken_ref", "where": где, "key": ключ} for где, ключ in контекст if ключ not in карта
	]
	return найдено


def _части_каталога() -> dict[str, list[dict]]:
	"""Составляющие всех компетенций каталога: ключ → `[{competency, from_level}]` по порядку
	каталога. Одна выборка: таблица связей мала."""
	части: dict[str, list[dict]] = {}
	for ч in frappe.get_all(
		"Agent Competency Part",
		filters={"parenttype": КОМПЕТЕНЦИЯ},
		fields=["parent", "competency", "from_level"],
		order_by="parent asc, idx asc",
	):
		части.setdefault(ч.parent, []).append({"competency": ч.competency, "from_level": ч.from_level})
	return части


def _замкнуть(порядок: list[str], части: dict[str, list[dict]]) -> list[str]:
	"""`порядок`, за ним составляющие его компетенций, составляющие составляющих — без повторов."""
	итог = list(порядок)
	# Список растёт по ходу обхода: составляющие дописываются в конец и обходятся следом.
	for ключ in итог:
		for часть in части.get(ключ, []):
			if часть["competency"] not in итог:
				итог.append(часть["competency"])
	return итог


def карты_по_целям(цели_релизов: dict[str, dict[str, list[dict]]]) -> dict[str, list[dict]]:
	"""Карты курсов на каталоге по целям уроков их релизов — `{релиз: [компетенция]}`.

	`цели_релизов` — релиз → ключ урока → цели `{competency, target}` по порядку
	релиза (`index.цели_уроков`). Карта курса — компетенции целей в порядке первого
	появления, за ними их составляющие по каталогу и составляющие составляющих:
	как у карты в релизе, где составляющая обязана быть в карте. Компетенция — в
	форме карты релиза `{key, kind, title, description, parts}` и сверх неё `area`,
	`version`, `similar` и `levels` — признаки уровней `{"1": …, "4": …}`. Слага,
	которого в каталоге нет, в карте нет.

	Три выборки на все релизы: составляющие всего каталога, компетенции карт и их
	похожие. Без релизов и целей — ни одной.
	"""
	порядки = {
		релиз: порядок
		for релиз, цели in цели_релизов.items()
		if (порядок := list(dict.fromkeys(ц["competency"] for список in цели.values() for ц in список)))
	}
	if not порядки:
		return {}
	части = _части_каталога()
	порядки = {релиз: _замкнуть(порядок, части) for релиз, порядок in порядки.items()}
	ключи = list({к for порядок in порядки.values() for к in порядок})
	похожие: dict[str, list[str]] = {}
	for п in frappe.get_all(
		"Agent Competency Similar",
		filters={"parenttype": КОМПЕТЕНЦИЯ, "parent": ("in", ключи)},
		fields=["parent", "competency"],
		order_by="parent asc, idx asc",
	):
		похожие.setdefault(п.parent, []).append(п.competency)
	определения = {
		к.name: {
			"key": к.name,
			"kind": к.kind,
			"title": к.title,
			"description": к.description or "",
			"parts": части.get(к.name, []),
			"area": к.area,
			"version": к.version,
			"similar": похожие.get(к.name, []),
			"levels": {str(уровень): к.get(f"level_{уровень}") for уровень in range(1, 5)},
		}
		for к in frappe.get_all(КОМПЕТЕНЦИЯ, filters={"name": ("in", ключи)}, fields=ПОЛЯ_КОМПЕТЕНЦИИ)
	}
	return {
		релиз: [определения[к] for к in порядок if к in определения] for релиз, порядок in порядки.items()
	}


def последний() -> dict:
	"""Последний выпуск каталога: `{catalog_release, version, digest, commit, published_at,
	published_by, catalog, versions}`; `catalog` — снимок выпуска, `versions` — текущая
	версия каждой компетенции. Каталог ещё не публиковали — те же ключи с `None` и `{}`."""
	найдено = frappe.get_all(
		ВЫПУСК,
		fields=["name", "version", "digest", "source_commit", "published_at", "published_by", "snapshot"],
		order_by="version desc",
		limit=1,
	)
	if not найдено:
		return {
			"catalog_release": None,
			"version": None,
			"digest": None,
			"commit": None,
			"published_at": None,
			"published_by": None,
			"catalog": None,
			"versions": {},
		}
	выпуск = найдено[0]
	return {
		"catalog_release": выпуск.name,
		"version": выпуск.version,
		"digest": выпуск.digest,
		"commit": выпуск.source_commit or None,
		"published_at": выпуск.published_at.isoformat(),
		"published_by": выпуск.published_by,
		"catalog": json.loads(выпуск.snapshot),
		"versions": dict(frappe.get_all(КОМПЕТЕНЦИЯ, fields=["name", "version"], as_list=True)),
	}
