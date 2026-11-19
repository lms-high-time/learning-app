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
- **Организация** видит только уровни — те же, что ученик: ни тексты
  свидетельств, ни исходы вроде «затруднения» руководителю не отдаются.
- **Автор** видит сводку по всем ученикам без имён — про курс, а не про
  людей: где цель урока не достигается, где уровни ставятся с подсказкой или
  после исправления, где свидетельства бывают только с выбором из вариантов.
"""

import frappe

from lms_frappe_app.agent_learning.competencies import evidence
from lms_frappe_app.agent_learning.competencies import queue as очередь
from lms_frappe_app.agent_learning.releases import index
from lms_frappe_app.agent_learning.runs import service as прохождения

НЕТ_ИНФОРМАЦИИ, УРОВЕНЬ, ЗАБЛОКИРОВАНО = "no_info", "level", "locked"


def уровни_по_данным(
	карта: list[dict], цели_уроков: dict[str, list[dict]], статусы: dict[str, str], записи: list
) -> list[dict]:
	"""Уровни ученика по карте: `{key, title, status, level, open_level}` по порядку карты. Без базы.

	`status` — `locked` (курс компетенцию ещё не открыл), `level` (есть показанный уровень) или
	`no_info`; `level` — показанный уровень, иначе `None`; `open_level` — отметка цели на шкале.
	"""
	открытые = очередь.открытые_уровни(цели_уроков, set(статусы))
	состояние = evidence.состояние_по_записям(записи)
	итог = []
	for к in карта:
		открыт = открытые.get(к["key"], 0)
		показан = очередь.показанный_уровень(состояние.get(к["key"], evidence.ПУСТО)["level"], открыт)
		итог.append(
			{
				"key": к["key"],
				"title": к["title"],
				"status": ЗАБЛОКИРОВАНО if not открыт else (УРОВЕНЬ if показан else НЕТ_ИНФОРМАЦИИ),
				"level": показан,
				"open_level": открыт,
			}
		)
	return итог


def основание(уровень: dict, записи: list) -> dict | None:
	"""Основание показанного состояния компетенции по записям журнала с текстами. Без базы."""
	if уровень["status"] == ЗАБЛОКИРОВАНО:
		return None
	свои = [з for з in записи if з.competency == уровень["key"] and з.kind == evidence.СВИДЕТЕЛЬСТВО]
	if уровень["status"] == НЕТ_ИНФОРМАЦИИ:
		if not свои:
			return None
		з = свои[-1]
		return {
			"outcome": з.outcome,
			"task": з.task or None,
			"student_action": з.student_action or None,
			"assessment": з.assessment or None,
			"uncertainties": з.uncertainties or None,
		}
	выведено = evidence._выведенные(записи)
	действующие = [з for з in свои if з.outcome == evidence.ПОДТВЕРЖДЕНО and з.name not in выведено]
	видимые = [з for з in действующие if з.level <= уровень["level"]]
	if видимые:
		з = max(видимые, key=lambda з: з.level)
		return {
			"outcome": з.outcome,
			"level": з.level,
			"task": з.task or None,
			"student_action": з.student_action or None,
			"uncertainties": з.uncertainties or None,
		}
	# Подтверждение только на скрытом уровне: без оценки и полей переноса — они назвали бы его.
	з = max(действующие, key=lambda з: з.level)
	return {"outcome": з.outcome, "task": з.task or None, "student_action": з.student_action or None}


ТЕКСТЫ_ОСНОВАНИЯ = ("task", "student_action", "assessment", "uncertainties")


def _записи_с_текстами(ученик: str, курс: str) -> list:
	return frappe.get_all(
		evidence.ЖУРНАЛ,
		filters={"student": ученик, "course": курс},
		fields=[
			"name",
			"kind",
			"competency",
			"outcome",
			"level",
			"withdrawn",
			"lesson_key",
			*ТЕКСТЫ_ОСНОВАНИЯ,
		],
		order_by="creation asc, name asc",
	)


def для_ученика(ученик: str, курс: str, релиз: str) -> list[dict]:
	"""Карта ученика по курсу: уровни с описанием и видом и основание; курс без карты — `[]`."""
	карта = index.карта_компетенций(релиз)
	if not карта:
		return []
	записи = _записи_с_текстами(ученик, курс)
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


def уровни_учеников(курс: str, релиз: str, ученики: list[str]) -> dict[str, list[dict]]:
	"""Уровни учеников курса для организации — `{ученик: [{key, title, status, level}]}`; курс без
	карты — `{}`. Постоянное число запросов на курс, а не на ученика."""
	карта = index.карта_компетенций(релиз) if релиз else []
	if not карта or not ученики:
		return {}
	цели = index.цели_уроков(релиз)
	статусы: dict[str, dict[str, str]] = {у: {} for у in ученики}
	for р in frappe.get_all(
		"Agent Lesson Run",
		filters={"student": ("in", ученики), "course": курс},
		fields=["student", "lesson_key", "status"],
	):
		статусы[р.student][р.lesson_key] = р.status
	записи: dict[str, list] = {у: [] for у in ученики}
	for з in frappe.get_all(
		evidence.ЖУРНАЛ,
		filters={"student": ("in", ученики), "course": курс},
		fields=["name", "student", "kind", "competency", "outcome", "level", "withdrawn"],
		order_by="creation asc, name asc",
	):
		записи[з.student].append(з)
	return {
		у: [
			{"key": о["key"], "title": о["title"], "status": о["status"], "level": о["level"]}
			for о in уровни_по_данным(карта, цели, статусы[у], записи[у])
		]
		for у in ученики
	}


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
	компетенции = []
	for к in карта:
		свои = [з for з in записи if з.competency == к["key"] and з.kind == evidence.СВИДЕТЕЛЬСТВО]
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
