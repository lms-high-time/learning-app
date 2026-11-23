# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Сквозные представления компетенций каталога (learning-services#827, дизайн сквозных
компетенций §3–§5).

Компетенция каталога общая для всех курсов (learning-services#826): журнал по ней читается во
всех курсах ученика. Здесь — то, что из этого журнала видит ученик и его агент:

- **лучшее подтверждение** — действующее подтверждение наивысшего уровня, при равных —
  последнее: уровень, когда, в каком курсе и по какой версии компетенции записано, на текущей
  ли версии (`лучшие_подтверждения`, `подтверждение`). Давность и версию сервер не судит —
  отдаёт данными;
- **похожие** — связь «похожа» хранится в одну сторону, читается в обе
  (`catalog.read._похожие_и_классы`); подтверждение на похожей кредита не даёт и отдаётся как
  сведение;
- **старт урока** — по компетенции урока лучшее подтверждение, похожие с их подтверждениями и
  закрыта ли цель засчётом из другого курса (`для_старта`);
- **сквозная карта** — компетенции каталога, по которым у ученика есть записи, и компетенции
  его курсов: подтверждённый уровень без блокировки, похожие, курсы ученика с этой
  компетенцией; группы — по классам обязательной схемы классификации (`сквозная_карта`);
- **подсказка курса** — два «найдено затруднение» на компетенции или на связке похожих из
  разных занятий без подтверждения после них (`поводы_подсказки`) и курсы витрины, на которые
  ученик не записан, где эта компетенция или похожая в целях уроков не ниже уровня затруднения
  (`подсказки`).

Всё — только по записям самого ученика: чужих данных здесь нет.
"""

import frappe
from frappe.query_builder import Order

from lms_frappe_app.agent_learning.access import курсы_ученика
from lms_frappe_app.agent_learning.catalog import read as каталог
from lms_frappe_app.agent_learning.competencies import closing, evidence
from lms_frappe_app.agent_learning.releases import index

КУРС = "LMS Course"
ЗАПИСЬ_НА_КУРС = "LMS Enrollment"


def лучшие_подтверждения(записи: list) -> dict:
	"""Компетенция → запись действующего подтверждения наивысшего уровня, при равных — последняя
	по журналу. Выведенные пересмотром и отменённые аудитом не считаются. Без базы."""
	выведено = evidence._выведенные(записи)
	лучшие: dict = {}
	for з in записи:
		if з.kind == evidence.СВИДЕТЕЛЬСТВО and з.outcome == evidence.ПОДТВЕРЖДЕНО and з.name not in выведено:
			прежнее = лучшие.get(з.competency)
			if прежнее is None or з.level >= прежнее.level:
				лучшие[з.competency] = з
	return лучшие


def подтверждение(запись, текущая_версия: int | None, названия_курсов: dict[str, str]) -> dict | None:
	"""Подтверждение наружу: `{level, recorded_at, course, course_title, version, current_version}`;
	нет записи — `None`. `version` — версия компетенции, по признакам которой поставлен уровень, и
	`current_version` — она ли текущая; компетенции нет в каталоге (`текущая_версия` — `None`) —
	`current_version` тоже `None`. Без базы."""
	if запись is None:
		return None
	версия = запись.competency_version
	return {
		"level": запись.level,
		"recorded_at": запись.creation.isoformat() if запись.creation else None,
		"course": запись.course,
		"course_title": названия_курсов.get(запись.course),
		"version": версия,
		"current_version": (версия == текущая_версия) if текущая_версия else None,
	}


def _в_обе_стороны(похожие: dict[str, list[str]]) -> dict[str, list[str]]:
	"""Связи «похожа» в обе стороны: читатель не полагается на то, что вход уже симметричен."""
	итог: dict[str, list[str]] = {}
	for к, список in похожие.items():
		for п in список:
			for от, до in ((к, п), (п, к)):
				if до not in итог.setdefault(от, []):
					итог[от].append(до)
	return итог


def поводы_подсказки(
	записи: list, похожие: dict[str, list[str]], область: set[str] | None = None
) -> list[dict]:
	"""Поводы подсказать курс: `[{competencies, level}]` по порядку первого затруднения. Без базы.

	Затруднение открыто, пока после него нет действующего подтверждения той же компетенции не
	ниже его уровня; выведенное аудитом не считается. Связка — компетенции с открытыми
	затруднениями, связанные «похожа» (`похожие`, в обе стороны). Повод — связка, где открытые
	затруднения записаны не меньше чем в двух разных занятиях: `competencies` — компетенции
	связки с затруднениями, `level` — наименьший уровень этих затруднений. С `область` — только
	связки, где есть компетенция из неё.
	"""
	выведено = evidence._выведенные(записи)
	открытые: dict[str, list] = {}
	for з in записи:
		if з.kind != evidence.СВИДЕТЕЛЬСТВО or з.name in выведено:
			continue
		if з.outcome == evidence.ЗАТРУДНЕНИЕ and (з.level or 0) > 0:
			открытые.setdefault(з.competency, []).append(з)
		elif з.outcome == evidence.ПОДТВЕРЖДЕНО and з.competency in открытые:
			открытые[з.competency] = [д for д in открытые[з.competency] if д.level > з.level]
	с_затруднениями = [к for к, список in открытые.items() if список]
	связи = _в_обе_стороны(похожие)
	разобрано: set[str] = set()
	поводы = []
	for начало in с_затруднениями:
		if начало in разобрано:
			continue
		связка, обойти = [], [начало]
		while обойти:
			к = обойти.pop()
			if к in разобрано:
				continue
			разобрано.add(к)
			связка.append(к)
			обойти += [п for п in связи.get(к, []) if открытые.get(п) and п not in разобрано]
		затруднения = [д for к in связка for д in открытые[к]]
		if len({д.session or д.name for д in затруднения}) < 2:
			continue
		if область is not None and not область.intersection(связка):
			continue
		поводы.append(
			{
				"competencies": sorted(связка, key=с_затруднениями.index),
				"level": min(д.level for д in затруднения),
			}
		)
	return поводы


def курсы_по_целям(компетенции: set[str]) -> dict[str, dict]:
	"""Курсы витрины, где компетенции из `компетенции` — в целях уроков действующего релиза:
	`{курс: {title, targets: {компетенция: наивысшая цель}}}`. Витрина — опубликованные
	курсы, кроме анонсов: на них в личном пространстве может записаться любой
	(`spaces.каталог`). Одна выборка: курсы и строки уроков их действующих релизов, отобранные
	по вхождению слага в цели; цели разбираются здесь."""
	if not компетенции:
		return {}
	к, у = frappe.qb.DocType(КУРС), frappe.qb.DocType(index.УРОК)
	вхождение = None
	for ключ in sorted(компетенции):
		условие = у.competencies.like(f'%"{ключ}"%')
		вхождение = условие if вхождение is None else вхождение | условие
	строки = (
		frappe.qb.from_(к)
		.join(у)
		.on((у.parent == к.active_release) & (у.parenttype == index.РЕЛИЗ))
		.select(к.name, к.title, у.competencies)
		.where((к.published == 1) & (к.upcoming == 0))
		.where(вхождение)
		.run(as_dict=True)
	)
	итог: dict[str, dict] = {}
	for с in строки:
		for ц in index._список_json(с.competencies):
			if isinstance(ц, dict) and ц.get("competency") in компетенции:
				курс = итог.setdefault(с.name, {"title": с.title, "targets": {}})
				курс["targets"][ц["competency"]] = max(курс["targets"].get(ц["competency"], 0), ц["target"])
	return итог


def подсказки(
	ученик: str,
	записи: list,
	похожие: dict[str, list[str]],
	названия: dict[str, str],
	область: set[str] | None = None,
	кроме: str | None = None,
) -> list[dict]:
	"""Подсказка курса по журналу ученика (`записи` — во всех курсах):
	`[{competencies: [{key, title}], level, courses: [{course, title, targets: [{competency,
	target}]}]}]`.

	Повод — `поводы_подсказки`; курс подходит, если компетенция повода или похожая на неё в целях
	его урока с уровнем не ниже уровня повода (`курсы_по_целям`), ученик на курс не записан ни в
	одном пространстве — как личный каталог (`spaces.каталог`): пройденный и начатый курс не
	подсказываются — и это не курс `кроме`. Повод без подходящих курсов не отдаётся. Без повода —
	ни одного запроса; с поводом — курсы, записи ученика на них и, если каких-то названий
	компетенций нет в `названия`, их названия.
	"""
	поводы = поводы_подсказки(записи, похожие, область)
	if not поводы:
		return []
	связи = _в_обе_стороны(похожие)

	def со_схожими(ключи: list[str]) -> set[str]:
		return set(ключи) | {п for к in ключи for п in связи.get(к, [])}

	курсы = курсы_по_целям(со_схожими([к for п in поводы for к in п["competencies"]]))
	курсы.pop(кроме, None)
	if not курсы:
		return []
	записан = set(
		frappe.get_all(
			ЗАПИСЬ_НА_КУРС, filters={"member": ученик, "course": ("in", list(курсы))}, pluck="course"
		)
	)
	итог = []
	for повод in поводы:
		свои = со_схожими(повод["competencies"])
		подходят = []
		for курс, сведения in sorted(курсы.items(), key=lambda п: (п[1]["title"] or "", п[0])):
			цели = [
				{"competency": к, "target": ц}
				for к, ц in сведения["targets"].items()
				if к in свои and ц >= повод["level"]
			]
			if цели and курс not in записан:
				подходят.append({"course": курс, "title": сведения["title"], "targets": цели})
		if подходят:
			итог.append({**повод, "courses": подходят})
	нет_названий = [к for п in итог for к in п["competencies"] if к not in названия]
	if нет_названий:
		названия = {**названия, **{к: с["title"] for к, с in каталог.названия_и_версии(нет_названий).items()}}
	return [
		{**п, "competencies": [{"key": к, "title": названия.get(к)} for к in п["competencies"]]} for п in итог
	]


def _названия_курсов(курсы: set[str]) -> dict[str, str]:
	if not курсы:
		return {}
	return dict(
		frappe.get_all(КУРС, filters={"name": ("in", list(курсы))}, fields=["name", "title"], as_list=True)
	)


def для_старта(
	компетенции: list[dict], карта: list[dict], записи: list, курс: str, ключ_урока: str, цели: list[dict]
) -> None:
	"""Дописывает компетенциям урока (`lesson.компетенции_старта`) данные журнала ученика:
	`confirmed` — лучшее подтверждение (`подтверждение`) во всех курсах; `similar` — похожие
	`{key, title, confirmed}`; `credited` — цель урока закрыта засчётом из другого курса (`closing.засчёты_по_записям`), у
	составляющей без цели — `None`.

	`записи` — журнал карты ученика с похожими (`queue.карта_ученика`). Запросы — только по
	надобности: названия и версии похожих вне карты курса и названия курсов подтверждений.
	"""
	по_ключу = {к["key"]: к for к in карта}
	похожие = {к["key"]: list(по_ключу.get(к["key"], {}).get("similar") or []) for к in компетенции}
	вне_карты = list(dict.fromkeys(п for список in похожие.values() for п in список if п not in по_ключу))
	сведения = {к: {"title": о["title"], "version": о["version"]} for к, о in по_ключу.items()}
	сведения |= каталог.названия_и_версии(вне_карты)
	лучшие = лучшие_подтверждения(записи)
	нужные = {к["key"] for к in компетенции} | {п for список in похожие.values() for п in список}
	курсы_подтверждений = {лучшие[к].course for к in нужные if к in лучшие}
	названия_курсов = _названия_курсов(курсы_подтверждений)
	засчитано = {з["competency"] for з in closing.засчёты_по_записям(записи, курс, ключ_урока, цели)}

	def подтверждено(ключ: str) -> dict | None:
		return подтверждение(лучшие.get(ключ), (сведения.get(ключ) or {}).get("version"), названия_курсов)

	for к in компетенции:
		к["confirmed"] = подтверждено(к["key"])
		к["similar"] = [
			{"key": п, "title": сведения[п]["title"], "confirmed": подтверждено(п)}
			for п in похожие[к["key"]]
			if п in сведения
		]
		к["credited"] = (к["key"] in засчитано) if к["target"] is not None else None


def _журнал_ученика(ученик: str) -> list:
	"""Записи журнала ученика во всех курсах по порядку записи — одной выборкой. Архив сброса
	не читается — у него пуст `student`."""
	ж = frappe.qb.DocType(evidence.ЖУРНАЛ)
	return (
		frappe.qb.from_(ж)
		.select(*(ж[поле] for поле in evidence.ПОЛЯ_РАСЧЁТА))
		.where(ж.student == ученик)
		.orderby(ж.creation, ж.name, order=Order.asc)
		.run(as_dict=True)
	)


def _курсы_и_релизы(курсы: set[str]) -> dict[str, dict]:
	"""Курс → `{title, release}`: название и действующий релиз — одной выборкой."""
	if not курсы:
		return {}
	return {
		с.name: {"title": с.title, "release": с.active_release}
		for с in frappe.get_all(
			КУРС, filters={"name": ("in", list(курсы))}, fields=["name", "title", "active_release"]
		)
	}


def _цели_релизов(релизы: list[str]) -> dict[str, dict[str, list[dict]]]:
	"""Релиз → ключ урока → цели `{competency, target}` по порядку релиза — одной выборкой."""
	цели: dict[str, dict[str, list[dict]]] = {р: {} for р in релизы}
	if релизы:
		for у in frappe.get_all(
			index.УРОК,
			filters={"parenttype": index.РЕЛИЗ, "parent": ("in", релизы)},
			fields=["parent", "lesson_key", "competencies"],
			order_by="idx asc",
		):
			if список := index._список_json(у.competencies):
				цели[у.parent][у.lesson_key] = список
	return цели


def сквозная_карта(ученик: str) -> dict:
	"""Сквозная карта ученика по компетенциям каталога: `{course: None, classification, groups,
	course_hints}`.

	Компетенции — его курсов (цели уроков действующих релизов и их составляющие) и
	те, по которым у него есть записи журнала в любом курсе. У компетенции — определение из
	каталога, `level` — подтверждённый уровень по всем курсам без блокировки (открытые уровни —
	дело курса), `confirmed` — лучшее подтверждение, `courses` — курсы ученика, в карте которых
	она есть, `similar` — похожие с подтверждениями. `classification` — обязательная схема
	(`catalog.read.обязательная_схема`), `groups` — классы схемы по её порядку с компетенциями
	в порядке курсов и журнала; без класса или без схемы — группа `class: null` в конце; пустых
	групп нет. `course_hints` — подсказка курса (`подсказки`).

	Число запросов не зависит ни от числа курсов, ни от числа записей.
	"""
	записи = _журнал_ученика(ученик)
	свои_курсы = [з["course"] for з in курсы_ученика(ученик)]
	курсы = _курсы_и_релизы(set(свои_курсы) | {з.course for з in записи})
	с_релизом = {к: курсы[к]["release"] for к in свои_курсы if к in курсы and курсы[к]["release"]}
	цели = _цели_релизов(list(set(с_релизом.values())))
	части = каталог._части_каталога() if any(цели.values()) else {}
	карты = {
		курс: каталог._замкнуть(
			list(dict.fromkeys(ц["competency"] for список in цели[релиз].values() for ц in список)), части
		)
		for курс, релиз in с_релизом.items()
	}
	порядок = list(
		dict.fromkeys([к for карта in карты.values() for к in карта] + [з.competency for з in записи])
	)
	определения = каталог.компетенции(порядок, части)
	вне = [п for о in определения.values() for п in о["similar"] if п not in определения]
	сведения = {к: {"title": о["title"], "version": о["version"]} for к, о in определения.items()}
	сведения |= каталог.названия_и_версии(list(dict.fromkeys(вне)))
	состояние = evidence.состояние_по_записям(записи)
	лучшие = лучшие_подтверждения(записи)
	названия_курсов = {к: с["title"] for к, с in курсы.items()}

	def подтверждено(ключ: str) -> dict | None:
		return подтверждение(лучшие.get(ключ), (сведения.get(ключ) or {}).get("version"), названия_курсов)

	компетенции = [
		{
			"key": о["key"],
			"title": о["title"],
			"kind": о["kind"],
			"description": о["description"],
			"version": о["version"],
			"classes": о["classes"],
			"level": состояние.get(о["key"], evidence.ПУСТО)["level"],
			"confirmed": подтверждено(о["key"]),
			"courses": [курс for курс, карта in карты.items() if о["key"] in карта],
			"similar": [
				{
					"key": п,
					"title": сведения[п]["title"],
					"level": состояние.get(п, evidence.ПУСТО)["level"],
					"confirmed": подтверждено(п),
				}
				for п in о["similar"]
				if п in сведения
			],
		}
		for о in (определения[к] for к in порядок if к in определения)
	]
	схема = каталог.обязательная_схема()
	return {
		"course": None,
		"classification": {"key": схема["key"], "title": схема["title"]} if схема else None,
		"groups": группы(компетенции, схема),
		"course_hints": подсказки(
			ученик,
			записи,
			{к: о["similar"] for к, о in определения.items()},
			{к: с["title"] for к, с in сведения.items()},
		),
	}


def группы(компетенции: list[dict], схема: dict | None) -> list[dict]:
	"""Компетенции по классам схемы `схема`: `[{class, title, competencies}]` — классы по порядку
	схемы, затем `class: null` для компетенций без класса в ней (и всех, если схемы нет); пустые
	группы не отдаются. Без базы."""
	классы = схема["classes"] if схема else []
	по_классу: dict[str | None, list[dict]] = {к["key"]: [] for к in классы}
	for к in компетенции:
		класс = к["classes"].get(схема["key"]) if схема else None
		по_классу.setdefault(класс if класс in по_классу else None, []).append(к)
	названия = {к["key"]: к["title"] for к in классы}
	return [
		{"class": класс, "title": названия.get(класс), "competencies": список}
		for класс, список in sorted(по_классу.items(), key=lambda п: п[0] is None)
		if список
	]
