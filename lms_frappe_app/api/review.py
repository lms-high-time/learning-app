# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Проверка домашек куратором: очередь «Ждут проверки» и карточка сдачи
(learning-services#452).

Куратор — руководитель (сдачи пространств своих организаций), методист и
модератор (все сдачи, включая личные). Свою сдачу не проверяет никто.

Отбор — явный в каждом методе: `frappe.get_all` хуки прав не применяет, и
очередь на одних хуках отдала бы руководителю чужие сдачи.
"""

import frappe
from frappe.utils import cint

from lms_frappe_app.agent_learning import homework as домашка
from lms_frappe_app.agent_learning import spaces as пространства
from lms_frappe_app.agent_learning.constants import ДОМАШКА_ПРИНЯТА, ДОМАШКА_СДАНА
from lms_frappe_app.agent_learning.errors import НЕТ_ПРАВА, Отказ
from lms_frappe_app.agent_learning.permissions import (
	видит_всё,
	доступна_сдача,
	методист,
	может_проверять,
	организации_менеджера,
)
from lms_frappe_app.api import контракт, текущий_пользователь

СДАЧА_НЕ_НАЙДЕНА = "submission_not_found"

#: Сколько строк очереди отдаётся за раз. `Why:` очередь читают глазами —
#: больше полусотни не разобрать, а обогащение строк стоит запросов.
ПОТОЛОК_ОЧЕРЕДИ = 50
#: Как назвать личное пространство в очереди и фильтрах.
ЛИЧНОЕ = "Личное"

#: Действия куратора, доступные в статусе сдачи.
ДЕЙСТВИЯ = {ДОМАШКА_СДАНА: ["accept", "send_back"], ДОМАШКА_ПРИНЯТА: ["reopen"]}


def _отбор(куратор: str) -> list | None:
	"""Фильтры сдач, которые куратор вправе проверять; `None` — никаких.

	Не архивные (`member` задан) и не свои. Методист и модератор — без
	ограничения по пространству, руководитель — пространства своих организаций.
	"""
	фильтры = [["member", "is", "set"], ["member", "!=", куратор]]
	if видит_всё(куратор) or методист(куратор):
		return фильтры
	организации = организации_менеджера(куратор)
	if not организации:
		return None
	return [*фильтры, ["organization", "in", организации]]


def _названия_организаций(организации) -> dict[str | None, str]:
	имена = [о for о in организации if о]
	названия = dict(
		frappe.get_all(
			"Learning Organization",
			filters={"name": ("in", имена)},
			fields=["name", "organization_name"],
			as_list=True,
		)
		if имена
		else []
	)
	return {**названия, None: ЛИЧНОЕ}


def _имена_учеников(ученики) -> dict[str, str]:
	"""Полное имя, без имени — почта. `Why:` почту ученика куратору иначе не отдаём."""
	if not ученики:
		return {}
	return {
		строка.name: строка.full_name or строка.name
		for строка in frappe.get_all(
			"User", filters={"name": ("in", list(ученики))}, fields=["name", "full_name"]
		)
	}


@frappe.whitelist(methods=["POST"])
@контракт
def queue(
	status: str = ДОМАШКА_СДАНА,
	course: str | None = None,
	organization: str | None = None,
	limit: int = ПОТОЛОК_ОЧЕРЕДИ,
) -> dict:
	"""Сдачи, которые куратор вправе проверять, — старые сверху.

	`courses` и `organizations` — значения фильтров из всех видимых куратору
	сдач, без учёта выбранных фильтров: выбор одного не прячет остальные.
	"""
	куратор = текущий_пользователь()
	отбор = _отбор(куратор)
	if отбор is None:
		return {"items": [], "total": 0, "courses": [], "organizations": []}
	фильтры = [*отбор, ["status", "=", status or ДОМАШКА_СДАНА]]
	if organization == пространства.ЛИЧНОЕ:
		фильтры.append(["organization", "is", "not set"])
	elif organization:
		фильтры.append(["organization", "=", organization])
	if course:
		уроки_курса = frappe.get_all("Course Lesson", filters={"course": course}, pluck="name")
		if not уроки_курса:
			фильтры = None
		else:
			фильтры.append(["lesson", "in", уроки_курса])

	# Why: одна выборка на вид данных, а не на сдачу (lms-platform#196):
	# сдачи, счёт, значения фильтров, уроки с курсом, курсы, задания, имена,
	# организации, проверенные версии — по запросу.
	предел = min(max(cint(limit), 1), ПОТОЛОК_ОЧЕРЕДИ)
	сдачи = (
		frappe.get_all(
			домашка.СДАЧА,
			filters=фильтры,
			fields=[
				"name",
				"homework",
				"lesson",
				"member",
				"organization",
				"status",
				"submitted_at",
				"version",
				"due_at",
			],
			order_by="submitted_at asc, creation asc",
			limit=предел,
		)
		if фильтры
		else []
	)
	всего = frappe.db.count(домашка.СДАЧА, фильтры) if фильтры else 0
	значения = frappe.get_all(домашка.СДАЧА, filters=отбор, fields=["lesson", "organization"], distinct=True)

	уроки = домашка.уроки_с_курсом({с.lesson for с in [*сдачи, *значения]})
	курсы_фильтра = {уроки[з.lesson].course for з in значения if з.lesson in уроки} - {None}
	курсы = курсы_фильтра | {уроки[с.lesson].course for с in сдачи if с.lesson in уроки} - {None}
	названия_курсов = dict(
		frappe.get_all(
			"LMS Course", filters={"name": ("in", list(курсы))}, fields=["name", "title"], as_list=True
		)
		if курсы
		else []
	)
	организации = _названия_организаций({з.organization for з in значения})
	задания = dict(
		frappe.get_all(
			домашка.ЗАДАНИЕ,
			filters={"name": ("in", list({с.homework for с in сдачи}))},
			fields=["name", "title"],
			as_list=True,
		)
		if сдачи
		else []
	)
	имена = _имена_учеников({с.member for с in сдачи})
	проверенные = домашка.проверенные_версии([с.name for с in сдачи])

	строки = []
	for сдача in сдачи:
		урок = уроки.get(сдача.lesson) or frappe._dict()
		строки.append(
			{
				"id": сдача.name,
				"status": сдача.status,
				"student": {"name": имена.get(сдача.member, сдача.member)},
				"course": урок.course,
				"course_title": названия_курсов.get(урок.course),
				"lesson": сдача.lesson,
				"lesson_title": урок.title,
				"title": задания.get(сдача.homework),
				"organization": пространства.наружу(сдача.organization),
				"organization_title": организации.get(сдача.organization or None),
				"submitted_at": домашка._время(сдача.submitted_at),
				"version": сдача.version,
				"reviewed_version": проверенные.get(сдача.name),
				"due_at": домашка._время(сдача.due_at),
				"overdue": домашка.просрочена(сдача),
			}
		)
	return {
		"items": строки,
		"total": всего,
		"courses": sorted(
			({"id": к, "title": названия_курсов.get(к)} for к in курсы_фильтра),
			key=lambda к: (к["title"] or "", к["id"]),
		),
		"organizations": sorted(
			(
				{"id": пространства.наружу(о), "title": организации.get(о or None)}
				for о in {з.organization or None for з in значения}
			),
			key=lambda о: (о["id"] == пространства.ЛИЧНОЕ, о["title"] or ""),
		),
	}


def карточка(документ, читатель: str) -> dict:
	"""Сдача для куратора: задание, ответ с версиями, журнал и доступные действия."""
	задание = frappe.db.get_value(домашка.ЗАДАНИЕ, документ.homework, домашка.ПОЛЯ_ЗАДАНИЯ, as_dict=True)
	урок = домашка.уроки_с_курсом({документ.lesson}).get(документ.lesson) or frappe._dict()
	ученик = документ.member or документ.archived_student
	организация = документ.organization or None
	return {
		"submission": домашка.описание_сдачи(документ, с_версиями=True, читатель=читатель),
		"homework": домашка.описание_задания(задание) if задание else None,
		"student": {"name": _имена_учеников({ученик}).get(ученик, ученик) if ученик else None},
		"course": урок.course,
		"course_title": frappe.get_cached_value("LMS Course", урок.course, "title") if урок.course else None,
		"lesson": документ.lesson,
		"lesson_title": урок.title,
		"lesson_url": домашка.адрес_урока(документ.lesson, урок.course) if урок.course else None,
		"organization": пространства.наружу(организация),
		"organization_title": _названия_организаций({организация})[организация],
		"reviewed_version": домашка.проверенная_версия(документ),
		"actions": ДЕЙСТВИЯ.get(документ.status, []) if может_проверять(документ, читатель) else [],
	}


def _сдача(имя: str):
	if not имя or not frappe.db.exists(домашка.СДАЧА, имя):
		raise Отказ(СДАЧА_НЕ_НАЙДЕНА, "Такой сдачи нет", submission=имя)
	return frappe.get_doc(домашка.СДАЧА, имя)


@frappe.whitelist(methods=["POST"])
@контракт
def submission(submission: str) -> dict:
	"""Карточка сдачи. Открыть может всякий, кто её видит; действия — только куратор."""
	читатель = текущий_пользователь()
	документ = _сдача(submission)
	if not доступна_сдача(документ, "read", читатель):
		raise Отказ(НЕТ_ПРАВА, "Эта сдача вам не видна", submission=submission)
	return карточка(документ, читатель)


def _проверить(submission: str, действие: str, version, comment: str | None = None) -> dict:
	куратор = текущий_пользователь()
	документ = _сдача(submission)
	if not может_проверять(документ, куратор):
		raise Отказ(НЕТ_ПРАВА, "Эту сдачу вы не проверяете", submission=submission)
	try:
		документ = домашка.проверить(куратор, submission, действие, cint(version), comment)
	except frappe.QueryDeadlockError:
		# Why: снимок куратора открыт до блокировки, и коммит ученика между ними
		# MariaDB стенда (снимочная изоляция) отдаёт взаимоблокировкой. Откат —
		# и версия новым запросом: ученик сохранил — `stale_version`, иначе
		# гонка с другим куратором — «повторите».
		frappe.db.rollback()
		текущая = frappe.db.get_value(домашка.СДАЧА, submission, "version")
		if текущая is not None and текущая != cint(version):
			raise Отказ(
				домашка.ВЕРСИЯ_УСТАРЕЛА,
				"Ученик сохранил новую версию — посмотрите её",
				submission=submission,
				version=текущая,
			)
		raise Отказ(домашка.ЗАНЯТО, "Сдачу сейчас меняет другой запрос — повторите", submission=submission)
	return карточка(документ, куратор)


@frappe.whitelist(methods=["POST"])
@контракт
def accept(submission: str, version: int) -> dict:
	"""Принять сдачу той версии, которую куратор видел."""
	return _проверить(submission, "accept", version)


@frappe.whitelist(methods=["POST"])
@контракт
def send_back(submission: str, version: int, comment: str | None = None) -> dict:
	"""Вернуть на доработку — с комментарием, что доделать."""
	return _проверить(submission, "send_back", version, comment)


@frappe.whitelist(methods=["POST"])
@контракт
def reopen(submission: str, version: int, comment: str | None = None) -> dict:
	"""Отменить ошибочный приём: сдача снова на доработке."""
	return _проверить(submission, "reopen", version, comment)


@frappe.whitelist(methods=["POST"])
@контракт
def pending_count() -> dict:
	"""Сколько сдач ждут проверки у куратора — для счётчика у пункта меню."""
	отбор = _отбор(текущий_пользователь())
	if отбор is None:
		return {"count": 0}
	return {"count": frappe.db.count(домашка.СДАЧА, [*отбор, ["status", "=", ДОМАШКА_СДАНА]])}
