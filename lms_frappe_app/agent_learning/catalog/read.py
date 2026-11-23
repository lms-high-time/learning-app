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
СХЕМА_КЛАССИФИКАЦИИ = "Agent Competency Classification"
#: Класс схемы классификации — строка её таблицы `classes`.
КЛАСС_СХЕМЫ = "Agent Competency Classification Class"
#: Класс компетенции `{classification, class_key}` — строка таблицы `classes` компетенции.
КЛАСС = "Agent Competency Class Assignment"
#: Похожая компетенция — строка таблицы `similar` компетенции.
ПОХОЖАЯ = "Agent Competency Similar"


#: Поля компетенции каталога, которые отдаёт карта курса.
ПОЛЯ_КОМПЕТЕНЦИИ = [
	"name",
	"title",
	"description",
	"kind",
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
	форме карты релиза `{key, kind, title, description, parts}` и сверх неё `classes` —
	`{схема: класс}`, `version`, `similar` и `levels` — признаки уровней `{"1": …,
	"4": …}`. Слага, которого в каталоге нет, в карте нет.

	Три выборки на все релизы: составляющие всего каталога, компетенции карт, их
	похожие вместе с классами (`компетенции`). Без релизов и целей — ни одной.
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
	определения = компетенции(list({к for порядок in порядки.values() for к in порядок}), части)
	return {
		релиз: [определения[к] for к in порядок if к in определения] for релиз, порядок in порядки.items()
	}


def компетенции(ключи: list[str], части: dict[str, list[dict]] | None = None) -> dict[str, dict]:
	"""Компетенции каталога `ключи` — `{ключ: {key, kind, title, description, parts, classes,
	version, similar, levels}}`; слага, которого в каталоге нет, нет и здесь. `parts` — из
	`части` (`_части_каталога`), без них — `[]`. Две выборки: похожие с классами и сами
	компетенции; без ключей — ни одной."""
	if not ключи:
		return {}
	части = части or {}
	похожие, классы = _похожие_и_классы(ключи)
	return {
		к.name: {
			"key": к.name,
			"kind": к.kind,
			"title": к.title,
			"description": к.description or "",
			"parts": части.get(к.name, []),
			"classes": классы.get(к.name, {}),
			"version": к.version,
			"similar": похожие.get(к.name, []),
			"levels": {str(уровень): к.get(f"level_{уровень}") for уровень in range(1, 5)},
		}
		for к in frappe.get_all(КОМПЕТЕНЦИЯ, filters={"name": ("in", ключи)}, fields=ПОЛЯ_КОМПЕТЕНЦИИ)
	}


def названия_и_версии(ключи: list[str]) -> dict[str, dict]:
	"""Название и текущая версия компетенций каталога — `{ключ: {title, version}}`; одна
	выборка, без ключей — ни одной. Для похожих вне карты курса: их определения целиком
	не нужны."""
	if not ключи:
		return {}
	return {
		к.name: {"title": к.title, "version": к.version}
		for к in frappe.get_all(
			КОМПЕТЕНЦИЯ, filters={"name": ("in", ключи)}, fields=["name", "title", "version"]
		)
	}


def _похожие_и_классы(ключи: list[str]) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]:
	"""Похожие компетенций `ключи` и их классы `{схема: класс}` — одной выборкой из двух таблиц.

	«Похожа» хранится в одну сторону (у той компетенции, где связь записана), а читается в обе:
	похожие компетенции — записанные у неё по порядку каталога, за ними те, у кого записана она,
	по ключу. `Why:` карту курса читает старт урока, и отдельная выборка классов или обратных
	связей была бы ещё одним запросом на каждый старт (`api/test_query_budget`)."""
	вперёд: dict[str, list[str]] = {}
	назад: dict[str, list[str]] = {}
	классы: dict[str, dict[str, str]] = {}
	# Ветка объединения — по метке `kind`: строки похожих и строка класса различаются ею, а не
	# пустотой столбцов. У обратной связи `parent` — компетенция из `ключи`, на которую ссылаются.
	for вид, родитель, _, похожая, схема, класс in frappe.db.sql(
		f"""(select 'similar' as kind, parent, idx, competency, null as classification, null as class_key
			from `tab{ПОХОЖАЯ}` where parenttype = %(тип)s and parent in %(ключи)s)
		union all
		(select 'similar_of', competency, idx, parent, null, null
			from `tab{ПОХОЖАЯ}` where parenttype = %(тип)s and competency in %(ключи)s)
		union all
		(select 'class', parent, idx, null, classification, class_key
			from `tab{КЛАСС}` where parenttype = %(тип)s and parent in %(ключи)s)
		order by parent, idx""",
		{"тип": КОМПЕТЕНЦИЯ, "ключи": tuple(ключи)},
	):
		if вид == "similar":
			вперёд.setdefault(родитель, []).append(похожая)
		elif вид == "similar_of":
			назад.setdefault(родитель, []).append(похожая)
		else:
			классы.setdefault(родитель, {})[схема] = класс
	похожие = {
		к: (свои := вперёд.get(к, [])) + sorted(set(назад.get(к, [])) - set(свои))
		for к in вперёд.keys() | назад.keys()
	}
	return похожие, классы


def обязательная_схема() -> dict | None:
	"""Обязательная схема классификации, по которой группируется сквозная карта ученика:
	`{key, title, classes: [{key, title}]}`, классы — по порядку схемы. Обязательных несколько —
	первая по ключу; ни одной — `None`. Одна выборка."""
	с, к = frappe.qb.DocType(СХЕМА_КЛАССИФИКАЦИИ), frappe.qb.DocType(КЛАСС_СХЕМЫ)
	строки = (
		frappe.qb.from_(с)
		.left_join(к)
		.on((к.parent == с.name) & (к.parenttype == СХЕМА_КЛАССИФИКАЦИИ))
		.select(с.name, с.title, к.class_key, к.title.as_("class_title"))
		.where(с.required == 1)
		.orderby(с.name)
		.orderby(к.idx)
		.run(as_dict=True)
	)
	if not строки:
		return None
	первая = строки[0].name
	return {
		"key": первая,
		"title": строки[0].title,
		"classes": [
			{"key": с.class_key, "title": с.class_title} for с in строки if с.name == первая and с.class_key
		],
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
