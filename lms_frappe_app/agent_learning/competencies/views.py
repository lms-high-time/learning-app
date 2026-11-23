# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Представления карты компетенций: ученик, организация, автор (learning-services#810, дизайн §11).

- **Ученик** видит каждую компетенцию в одном из трёх состояний: нет
  информации, уровень 1–4 (меньшее из подтверждённого и открытого) или
  заблокировано — и основание своими словами по нажатию. Основание уровня —
  свидетельство не выше показанного уровня; если подтверждение есть только на
  скрытом уровне, — задание и действие ученика без оценки и полей переноса,
  чтобы не раскрыть скрытый уровень. Основание «нет информации» — последнее
  свидетельство. Процентов, оценок, слов «ошибся» здесь нет.
- **Организация** видит только уровни: ни тексты свидетельств, ни исходы
  вроде «затруднения» руководителю не отдаются. У компетенций каталога
  уровни — только по записям курсов, назначенных ученику организациями
  руководителя, включая пересмотры в них (learning-services#827): личная
  работа в других курсах не видна. Цель, закрытая засчётом из такой работы, —
  состояние «засчитано ранее» с видимым уровнем, без источника.
- **Автор** видит сводку по всем ученикам без имён — про курс, а не про
  людей: где цель урока не достигается, где уровни ставятся с подсказкой или
  после исправления, где свидетельства бывают только с выбором из вариантов.
"""

import json
from collections.abc import Callable

import frappe
from frappe.query_builder import Order

from lms_frappe_app.agent_learning.catalog import read as каталог
from lms_frappe_app.agent_learning.competencies import evidence
from lms_frappe_app.agent_learning.competencies import queue as очередь
from lms_frappe_app.agent_learning.constants import СОБЫТИЕ_ЦЕЛЬ_ЗАСЧИТАНА
from lms_frappe_app.agent_learning.releases import index
from lms_frappe_app.agent_learning.runs import service as прохождения

НЕТ_ИНФОРМАЦИИ, УРОВЕНЬ, ЗАБЛОКИРОВАНО = "no_info", "level", "locked"
#: Цель закрыта засчётом из курса, которого руководитель не видит (learning-services#827).
ЗАСЧИТАНО_РАНЕЕ = "credited"


def уровни_по_данным(
	карта: list[dict],
	цели_уроков: dict[str, list[dict]],
	статусы: dict[str, str],
	записи: list,
	засчитано: dict[str, int] | None = None,
) -> list[dict]:
	"""Уровни ученика по карте: `{key, title, status, level, open_level}` по порядку карты. Без базы.

	`status` — `locked` (курс компетенцию ещё не открыл), `level` (есть показанный уровень) или
	`no_info`; `level` — показанный уровень, иначе `None`; `open_level` — отметка цели на шкале.
	`засчитано` — компетенция → наивысшая цель урока, закрытая засчётом из курса, которого
	смотрящий не видит: если показанный уровень её не достигает — `credited`, а `level` —
	показанный по видимым записям (или `None`); источника засчёта здесь нет.
	"""
	открытые = очередь.открытые_уровни(цели_уроков, set(статусы))
	состояние = evidence.состояние_по_записям(записи)
	засчитано = засчитано or {}
	итог = []
	for к in карта:
		открыт = открытые.get(к["key"], 0)
		показан = очередь.показанный_уровень(состояние.get(к["key"], evidence.ПУСТО)["level"], открыт)
		if к["key"] in засчитано and (показан or 0) < засчитано[к["key"]]:
			статус = ЗАСЧИТАНО_РАНЕЕ
		else:
			статус = ЗАБЛОКИРОВАНО if not открыт else (УРОВЕНЬ if показан else НЕТ_ИНФОРМАЦИИ)
		итог.append(
			{
				"key": к["key"],
				"title": к["title"],
				"status": статус,
				"level": показан,
				"open_level": открыт,
			}
		)
	return итог


def основание(уровень: dict, записи: list) -> dict | None:
	"""Основание показанного состояния компетенции по записям журнала с текстами. Без базы."""
	if уровень["status"] == ЗАБЛОКИРОВАНО:
		return None
	выведено = evidence._выведенные(записи)
	# Порядковый номер — чтобы при равных уровнях брать последнее по времени.
	все = [(н, з) for н, з in enumerate(записи) if з.competency == уровень["key"]]
	if уровень["status"] == НЕТ_ИНФОРМАЦИИ:
		# Последнее, что действует: пересмотр — его причина, выведенное подтверждение — нет.
		действует = [
			з
			for _, з in все
			if з.kind == evidence.ПЕРЕСМОТР or (з.kind == evidence.СВИДЕТЕЛЬСТВО and з.name not in выведено)
		]
		if not действует:
			return None
		з = действует[-1]
		if з.kind == evidence.ПЕРЕСМОТР:
			return {"outcome": "revised", "assessment": з.assessment or None}
		return {
			"outcome": з.outcome,
			"task": з.task or None,
			"student_action": з.student_action or None,
			"assessment": з.assessment or None,
			"uncertainties": з.uncertainties or None,
		}
	действующие = [
		(н, з)
		for н, з in все
		if з.kind == evidence.СВИДЕТЕЛЬСТВО and з.outcome == evidence.ПОДТВЕРЖДЕНО and з.name not in выведено
	]
	видимые = [(н, з) for н, з in действующие if з.level <= уровень["level"]]
	if видимые:
		_, з = max(видимые, key=lambda п: (п[1].level, п[0]))
		return {
			"outcome": з.outcome,
			"level": з.level,
			"task": з.task or None,
			"student_action": з.student_action or None,
			"uncertainties": з.uncertainties or None,
		}
	# Подтверждение только на скрытом уровне: без оценки и полей переноса — они назвали бы его;
	# наименьшее скрытое — самое близкое к открытому, при равных — последнее.
	_, з = min(действующие, key=lambda п: (п[1].level, -п[0]))
	return {"outcome": з.outcome, "task": з.task or None, "student_action": з.student_action or None}


ТЕКСТЫ_ОСНОВАНИЯ = ("task", "student_action", "assessment", "uncertainties")


def _записи_с_текстами(ученик: str, курс: str, сквозные: list[str]) -> list:
	"""Журнал ученика с текстами для основания — по курсу, а по компетенциям каталога `сквозные` —
	во всех курсах, как у состояния и очереди (`evidence._записи`, learning-services#827)."""
	ж = frappe.qb.DocType(evidence.ЖУРНАЛ)
	где_записано = ж.course == курс
	if сквозные:
		где_записано |= ж.competency.isin(сквозные) & (ж.competency_version > 0)
	return (
		frappe.qb.from_(ж)
		.select(
			*(
				ж[поле]
				for поле in (
					"name",
					"kind",
					"course",
					"competency",
					"outcome",
					"level",
					"withdrawn",
					"lesson_key",
					*ТЕКСТЫ_ОСНОВАНИЯ,
				)
			)
		)
		.where((ж.student == ученик) & где_записано)
		.orderby(ж.creation, ж.name, order=Order.asc)
		.run(as_dict=True)
	)


def для_ученика(ученик: str, курс: str, релиз: str) -> list[dict]:
	"""Карта ученика по курсу: уровни с описанием и видом и основание; курс без карты — `[]`.
	Уровни, выведенное и основание у компетенций каталога — по журналу всех курсов ученика."""
	карта = index.карта_компетенций(релиз)
	if not карта:
		return []
	записи = _записи_с_текстами(ученик, курс, evidence.сквозные(карта))
	по_ключу = {к["key"]: к for к in карта}
	return [
		{
			**у,
			"kind": по_ключу[у["key"]]["kind"],
			"description": по_ключу[у["key"]].get("description") or "",
			"basis": основание(у, записи),
		}
		for у in уровни_по_данным(
			карта, index.цели_уроков(релиз), прохождения.статусы_уроков(ученик, курс), записи
		)
	]


def записи_назначенных(записи: list, курс: str, сквозные: set[str], назначенные: set[str]) -> list:
	"""Записи, по которым руководитель видит уровни курса `курс`: записанные в нём, а по
	компетенциям каталога `сквозные` — и в других назначенных ему курсах (`назначенные`), включая
	их пересмотры и заключения аудита. Записей других курсов — личных и назначенных чужими
	организациями — нет, и их пересмотры уровней не снижают. Без базы."""
	return [
		з
		for з in записи
		if з.course == курс or (з.course in назначенные and з.competency in сквозные and з.competency_version)
	]


def засчитанные_вне(
	ученики: list[str], курсы: list[str], назначенные: dict[str, set[str]]
) -> dict[tuple[str, str], dict[str, int]]:
	"""Цели уроков курсов `курсы`, закрытые засчётом свидетельства из курса, не назначенного
	ученику (`назначенные`): `{(ученик, курс): {компетенция: наивысшая цель}}`. Одна выборка —
	события засчёта занятий (`Goal Credited`) с курсом засчитанного свидетельства; без учеников
	или курсов — ни одной."""
	if not ученики or not курсы:
		return {}
	с, з, ж = (
		frappe.qb.DocType("Agent Session Event"),
		frappe.qb.DocType("Agent Learning Session"),
		frappe.qb.DocType(evidence.ЖУРНАЛ),
	)
	итог: dict[tuple[str, str], dict[str, int]] = {}
	for строка in (
		frappe.qb.from_(с)
		.join(з)
		.on(з.name == с.session)
		.join(ж)
		.on(ж.name == с.credited_by)
		.select(з.student, з.course, с.competency, с.target, ж.course.as_("source"))
		.where((с.kind == СОБЫТИЕ_ЦЕЛЬ_ЗАСЧИТАНА) & з.student.isin(ученики) & з.course.isin(курсы))
		.run(as_dict=True)
	):
		if строка.source not in назначенные.get(строка.student, set()):
			цели = итог.setdefault((строка.student, строка.course), {})
			цели[строка.competency] = max(цели.get(строка.competency, 0), строка.target or 0)
	return итог


def уровни_отчёта(
	курсы: list[tuple[str, str | None, list[str]]], назначенные: dict[str, set[str]]
) -> dict[tuple[str, str], list[dict]]:
	"""Уровни для отчёта организации — `{(курс, ученик): [{key, title, status, level}]}`.

	`курсы` — строки отчёта `(курс, действующий релиз, ученики)`; курсы без карты не входят.
	`назначенные` — ученик → курсы, назначенные ему организациями руководителя: по компетенциям
	каталога уровни — по записям этих курсов (`записи_назначенных`), засчитанное из остальных —
	`credited` (`засчитанные_вне`). Число запросов не зависит ни от учеников, ни от назначений:
	карта и цели уроков — по релизу, прохождения, журнал и засчёты — одной выборкой на отчёт."""
	карты: dict[str, list[dict]] = {}
	цели: dict[str, dict[str, list[dict]]] = {}
	с_картой = []
	for курс, релиз, ученики in курсы:
		if not релиз or not ученики:
			continue
		if релиз not in карты:
			карты[релиз] = index.карта_компетенций(релиз)
			if карты[релиз]:
				цели[релиз] = index.цели_уроков(релиз)
		if карты[релиз]:
			с_картой.append((курс, релиз, ученики))
	if not с_картой:
		return {}
	все = sorted({у for _, _, ученики in с_картой for у in ученики})
	курсы_отчёта = {курс for курс, _, _ in с_картой}
	сквозные = {курс: set(evidence.сквозные(карты[релиз])) for курс, релиз, _ in с_картой}
	на_каталоге = [курс for курс, свои in сквозные.items() if свои]
	статусы: dict[tuple[str, str], dict[str, str]] = {}
	for р in frappe.get_all(
		"Agent Lesson Run",
		filters={"student": ("in", все), "course": ("in", list(курсы_отчёта))},
		fields=["student", "course", "lesson_key", "status"],
	):
		статусы.setdefault((р.student, р.course), {})[р.lesson_key] = р.status
	курсы_журнала = курсы_отчёта | (
		set().union(*(назначенные.get(у, set()) for у in все)) if на_каталоге else set()
	)
	записи: dict[str, list] = {у: [] for у in все}
	for з in frappe.get_all(
		evidence.ЖУРНАЛ,
		filters={"student": ("in", все), "course": ("in", list(курсы_журнала))},
		fields=[
			"name",
			"student",
			"course",
			"kind",
			"competency",
			"competency_version",
			"outcome",
			"level",
			"withdrawn",
		],
		order_by="creation asc, name asc",
	):
		записи[з.student].append(з)
	засчитано = засчитанные_вне(все, на_каталоге, назначенные)
	return {
		(курс, у): [
			{"key": о["key"], "title": о["title"], "status": о["status"], "level": о["level"]}
			for о in уровни_по_данным(
				карты[релиз],
				цели[релиз],
				статусы.get((у, курс), {}),
				записи_назначенных(записи[у], курс, сквозные[курс], назначенные.get(у, set())),
				засчитано.get((у, курс)),
			)
		]
		for курс, релиз, ученики in с_картой
		for у in ученики
	}


def уровни_по_курсам(
	ученик: str, релизы: dict[str, str | None], назначенные: Callable[[], set[str]] | None = None
) -> dict[str, list[dict]]:
	"""Уровни одного ученика по нескольким курсам — `{курс: [{key, title, status, level}]}`; курсы
	без карты не входят. `назначенные` — как получить курсы, назначенные ученику организациями
	руководителя (без него — курсы `релизы`): зовётся, только если среди курсов есть курс на
	каталоге. По компетенциям каталога уровни — по записям этих курсов и своего
	(`записи_назначенных`), засчитанное из остальных — `credited` (`засчитанные_вне`).
	Постоянное число запросов на всю выдачу, а не на курс: карты релизов, цели их уроков,
	прохождения, журнал и засчёты — по выборке; карты релизов на каталоге — одним обращением к
	каталогу на все (`catalog.read.карты_по_целям`)."""
	свои_курсы = set(релизы)
	релизы = {курс: р for курс, р in релизы.items() if р}
	if not релизы:
		return {}
	карты: dict[str, list[dict]] = {}
	на_каталоге: set[str] = set()
	for р in frappe.get_all(
		index.РЕЛИЗ,
		filters={"name": ("in", list(релизы.values()))},
		fields=["name", "competency_map", "competency_catalog"],
	):
		if р.competency_catalog:
			на_каталоге.add(р.name)
		else:
			карты[р.name] = _список(р.competency_map)
	кандидаты = {курс: р for курс, р in релизы.items() if карты.get(р) or р in на_каталоге}
	if not кандидаты:
		return {}
	цели: dict[str, dict[str, list[dict]]] = {р: {} for р in кандидаты.values()}
	for у in frappe.get_all(
		index.УРОК,
		filters={"parenttype": index.РЕЛИЗ, "parent": ("in", list(кандидаты.values()))},
		fields=["parent", "lesson_key", "competencies"],
		order_by="idx asc",
	):
		if список := _список(у.competencies):
			цели[у.parent][у.lesson_key] = список
	if на_каталоге:
		карты |= каталог.карты_по_целям({р: цели[р] for р in на_каталоге if р in цели})
	с_картой = {курс: р for курс, р in кандидаты.items() if карты.get(р)}
	if not с_картой:
		return {}
	статусы: dict[str, dict[str, str]] = {курс: {} for курс in с_картой}
	for р in frappe.get_all(
		"Agent Lesson Run",
		filters={"student": ученик, "course": ("in", list(с_картой))},
		fields=["course", "lesson_key", "status"],
	):
		статусы[р.course][р.lesson_key] = р.status
	сквозные = {курс: set(evidence.сквозные(карты[р])) for курс, р in с_картой.items()}
	назначенные_курсы = свои_курсы
	if any(сквозные.values()) and назначенные:
		назначенные_курсы = свои_курсы | назначенные()
	записи = frappe.get_all(
		evidence.ЖУРНАЛ,
		filters={
			"student": ученик,
			"course": ("in", list(назначенные_курсы if any(сквозные.values()) else с_картой)),
		},
		fields=[
			"name",
			"course",
			"kind",
			"competency",
			"competency_version",
			"outcome",
			"level",
			"withdrawn",
		],
		order_by="creation asc, name asc",
	)
	засчитано = засчитанные_вне(
		[ученик], [курс for курс, свои in сквозные.items() if свои], {ученик: назначенные_курсы}
	)
	return {
		курс: [
			{"key": о["key"], "title": о["title"], "status": о["status"], "level": о["level"]}
			for о in уровни_по_данным(
				карты[р],
				цели[р],
				статусы[курс],
				записи_назначенных(записи, курс, сквозные[курс], назначенные_курсы),
				засчитано.get((ученик, курс)),
			)
		]
		for курс, р in с_картой.items()
	}


def _список(значение) -> list:
	if isinstance(значение, str):
		значение = json.loads(значение) if значение else None
	return значение if isinstance(значение, list) else []


def сводка_по_данным(
	карта: list[dict], цели_уроков: dict[str, list[dict]], прохождения: list, записи: list
) -> dict:
	"""Сводка автору без имён: по компетенциям и по целям уроков. Без базы.

	`прохождения` — `{student, lesson_key, status}` всех учеников курса; `записи` — журнал курса
	со `student`. По компетенции — сколько свидетельств каждого исхода, форма заданий и
	подсказки у подтверждений, выведенные пересмотром, у скольких учеников открыто затруднение
	после подтверждения. По цели урока — сколько учеников прошли урок и у скольких из них
	подтверждённый уровень не ниже цели.
	"""
	по_ученикам: dict[str, list] = {}
	for з in записи:
		по_ученикам.setdefault(з.student, []).append(з)
	состояния = {у: evidence.состояние_по_записям(з) for у, з in по_ученикам.items()}
	затруднения = {у: очередь.затруднения_после(з) for у, з in по_ученикам.items()}
	выведено = evidence._выведенные(записи)
	# Один проход по журналу: записи по компетенциям, а не перебор журнала на каждую.
	по_компетенциям: dict[str, list] = {}
	for з in записи:
		if з.kind == evidence.СВИДЕТЕЛЬСТВО:
			по_компетенциям.setdefault(з.competency, []).append(з)
	итоговая_цель: dict[str, int] = {}
	for список in цели_уроков.values():
		for ц in список:
			итоговая_цель[ц["competency"]] = max(итоговая_цель.get(ц["competency"], 0), ц["target"])
	компетенции = []
	for к in карта:
		свои = по_компетенциям.get(к["key"], [])
		подтверждения = [з for з in свои if з.outcome == evidence.ПОДТВЕРЖДЕНО]
		компетенции.append(
			{
				"key": к["key"],
				"title": к["title"],
				"evidence": {исход: sum(з.outcome == исход for з in свои) for исход in evidence.ИСХОДЫ},
				"confirmed_forms": {ф: sum(з.task_form == ф for з in подтверждения) for ф in evidence.ФОРМЫ},
				"confirmed_with_hints": sum(bool(з.hints) for з in подтверждения),
				"confirmed_corrected": sum(bool(з.corrected) for з in подтверждения),
				"confirmed_indirect": sum(bool(з.indirect) for з in подтверждения),
				"withdrawn": sum(з.name in выведено for з in подтверждения),
				"students": len({з.student for з in свои}),
				"open_difficulties": sum(к["key"] in з for з in затруднения.values()),
				# Компетенция закрыта (дизайн §3): уровень не ниже наивысшей цели курса.
				"students_closed": sum(
					bool(итоговая_цель.get(к["key"]))
					and (с.get(к["key"], evidence.ПУСТО)["level"] or 0) >= итоговая_цель[к["key"]]
					for с in состояния.values()
				),
			}
		)
	прошли: dict[str, set[str]] = {}
	for р in прохождения:
		if р.status == очередь.ПРОЙДЕН:
			прошли.setdefault(р.lesson_key, set()).add(р.student)
	цели = [
		{
			"lesson": урок,
			"competency": ц["competency"],
			"target": ц["target"],
			"passed": len(прошли.get(урок, ())),
			"reached": sum(
				(состояния.get(у, {}).get(ц["competency"], evidence.ПУСТО)["level"] or 0) >= ц["target"]
				for у in прошли.get(урок, ())
			),
		}
		for урок, список in цели_уроков.items()
		for ц in список
	]
	return {"competencies": компетенции, "targets": цели}


def для_автора(курс: str, релиз: str) -> dict:
	"""Сводка автору по курсу; курс без карты — пустые списки. Архив сброса не считается."""
	карта = index.карта_компетенций(релиз) if релиз else []
	if not карта:
		return {"competencies": [], "targets": []}
	прохождения = frappe.get_all(
		"Agent Lesson Run",
		filters={"course": курс, "student": ("is", "set")},
		fields=["student", "lesson_key", "status"],
	)
	записи = frappe.get_all(
		evidence.ЖУРНАЛ,
		filters={"course": курс, "student": ("is", "set")},
		fields=[
			"name",
			"student",
			"kind",
			"competency",
			"outcome",
			"level",
			"withdrawn",
			"task_form",
			"hints",
			"corrected",
			"indirect",
		],
		order_by="creation asc, name asc",
	)
	return сводка_по_данным(карта, index.цели_уроков(релиз), прохождения, записи)
