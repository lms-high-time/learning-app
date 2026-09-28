# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Методы руководителя организации.

Изоляция проверяется **явно**, и это не дублирование хуков: `frappe.get_all`
— это `get_list(ignore_permissions=True)`, и `permission_query_conditions` к
нему не применяются никогда. Отчёт, положившийся на хуки, отдавал занятия
любого человека на платформе кому угодно — проверено эксплуатацией.
"""

import frappe

from lms_frappe_app.agent_learning.access import курсы_ученика
from lms_frappe_app.agent_learning.constants import ПРОЙДЕН
from lms_frappe_app.agent_learning.doctype.course_allocation.course_allocation import (
	адресаты_назначения,
)
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.structure import уроки_курса
from lms_frappe_app.agent_learning import artifact_tables
from lms_frappe_app.api.student import _заполненность, _схемы_курса
from lms_frappe_app.agent_learning.permissions import (
	организации_менеджера,
	свои_организации_пересекаются,
)
from lms_frappe_app.api import контракт, текущий_пользователь

ЧУЖОЙ_УЧЕНИК = "not_your_student"


@frappe.whitelist()
@контракт
def org_report(course: str | None = None, status: str | None = None) -> dict:
	"""Обучение своей организации: кто на чём и что просрочено."""
	менеджер = текущий_пользователь()
	организации = организации_менеджера(менеджер)
	if not организации:
		return {"rows": []}

	назначения = frappe.get_all(
		"Course Allocation",
		filters={"organization": ("in", организации), **({"course": course} if course else {})},
		fields=["name", "organization", "course", "audience", "deadline", "mandatory"],
	)

	строки = []
	# Данные, общие для всех участников курса, читаются один раз: прежняя
	# редакция строила список уроков и запрашивала прогресс на каждую пару
	# «назначение × участник», и организация на пятьсот человек с десятью
	# курсами давала десятки тысяч запросов в одном вызове.
	for назначение in назначения:
		участники = _адресаты(назначение)
		if not участники:
			continue
		уроки = уроки_курса(назначение.course)
		пройдено = _пройдено_по_участникам(назначение.course, участники, уроки)
		имена = _имена(участники)
		последняя_активность = _последняя_активность(назначение.course, участники)
		всего_блоков, заполнено_блоков = _документ_по_участникам(
			назначение.course, участники, назначение.organization
		)

		for участник in участники:
			строка = _строка_отчёта(
				участник,
				назначение,
				уроки=уроки,
				пройдено=пройдено.get(участник, 0),
				имя=имена.get(участник),
				активность=последняя_активность.get(участник),
				документ={
					"blocks_total": всего_блоков,
					"blocks_filled": заполнено_блоков.get(участник, 0),
				},
			)
			if status and строка["status"] != status:
				continue
			строки.append(строка)
	return {"rows": строки}


def _пройдено_по_участникам(
	курс: str, участники: list[str], уроки: list[str]
) -> dict[str, int]:
	"""Сколько уроков курса пройдено каждым — одним запросом на курс.

	Считаются только уроки, которые сейчас в курсе. `Why:` записи прогресса
	переживают перенос урока в другую главу, и подсчёт «по строкам» давал
	долю больше единицы, а статус «пройден» не наступал никогда.
	"""
	в_курсе = set(уроки)
	записи = frappe.get_all(
		"LMS Course Progress",
		filters={"course": курс, "member": ("in", участники), "status": ПРОЙДЕН},
		fields=["member", "lesson"],
	)
	пройдено: dict[str, set[str]] = {}
	for запись in записи:
		if запись.lesson in в_курсе:
			пройдено.setdefault(запись.member, set()).add(запись.lesson)
	return {участник: len(уроки) for участник, уроки in пройдено.items()}


def _документ_по_участникам(
	курс: str, участники: list[str], организация: str
) -> tuple[int, dict[str, int]]:
	"""Сколько блоков в документах курса и сколько заполнил каждый — в пространстве организации.

	Личный документ по тому же курсу и документ для другой компании не
	считаются: они не организации (learning-services#341).

	Считаются блоки действующих схем: убранный из схемы блок не засчитывается,
	даже если текст в базе остался. Два запроса на курс, не на участника.
	`Why:` урок засчитывает квиз, и без этой колонки руководитель не отличит
	пройденный курс с пустым документом от собранного (learning-services#296).
	"""
	схемы = {схема.slug: схема for схема in _схемы_курса(курс)}
	всего = sum(len(схема.blocks) for схема in схемы.values())
	if not всего:
		return 0, {}

	экземпляры = {
		запись.name: запись
		for запись in frappe.get_all(
			"Agent Student Artifact",
			filters={"course": курс, "student": ("in", участники), "organization": организация},
			fields=["name", "student", "artifact", "data"],
		)
	}
	if not экземпляры:
		return всего, {}

	содержимое: dict[str, dict[str, str]] = {}
	вложения: dict[str, dict[str, dict]] = {}
	for строка in frappe.get_all(
		"Agent Artifact Content",
		filters={"parent": ("in", list(экземпляры)), "parenttype": "Agent Student Artifact"},
		fields=["parent", "block_key", "content", "file", "url"],
	):
		содержимое.setdefault(строка.parent, {})[строка.block_key] = строка.content or ""
		# Заполнен блок с текстом, файлом или ссылкой (#315); сам файл отчёт
		# не показывает — только то, что блок не пуст.
		if строка.file or строка.url:
			вложения.setdefault(строка.parent, {})[строка.block_key] = {"file": строка.file, "url": строка.url}

	заполнено: dict[str, int] = {}
	for имя, экземпляр in экземпляры.items():
		схема = схемы.get(экземпляр.artifact)
		if not схема:
			continue
		# Правило то же, что у ученика: блок с таблицей — по её строкам (#330).
		сколько = _заполненность(
			схема, содержимое.get(имя, {}), вложения.get(имя, {}), artifact_tables.данные(экземпляр.data)
		)["blocks_filled"]
		заполнено[экземпляр.student] = заполнено.get(экземпляр.student, 0) + сколько
	return всего, заполнено


def _названия_курсов(курсы: list[str]) -> dict[str, str]:
	if not курсы:
		return {}
	return {
		запись.name: запись.title
		for запись in frappe.get_all(
			"LMS Course", filters={"name": ("in", курсы)}, fields=["name", "title"]
		)
	}


def _имена(участники: list[str]) -> dict[str, str]:
	return {
		запись.name: запись.full_name
		for запись in frappe.get_all(
			"User", filters={"name": ("in", участники)}, fields=["name", "full_name"]
		)
	}


def _последняя_активность(курс: str, участники: list[str]) -> dict[str, object]:
	"""Последнее занятие каждого участника по курсу — агрегатом.

	`Why:` максимум брался в Python по всем занятиям курса, и годовая история
	компании приезжала в память целиком ради одной даты на человека. Считает
	база, наружу идёт по строке на участника.
	"""
	return {
		строка.student: строка.started_at
		for строка in frappe.get_all(
			"Agent Learning Session",
			filters={"course": курс, "student": ("in", участники)},
			fields=["student", {"MAX": "started_at", "as": "started_at"}],
			group_by="student",
		)
	}


@frappe.whitelist()
@контракт
def student_detail(user: str) -> dict:
	"""Подробности по одному ученику своей организации.

	Тексты ответов на вопросы не отдаются, заметки агента об ученике — тем
	более: отчёт про результат, а не про содержание диалога. Покрытие целей
	при этом отдаётся — это тот же учебный результат, что и зачёт, просто
	мельче: видно, какие темы разобраны, а на какие обратить внимание дальше.
	"""
	менеджер = текущий_пользователь()
	if not свои_организации_пересекаются(менеджер, user):
		raise Отказ(ЧУЖОЙ_УЧЕНИК, "Этот ученик не из вашей организации", user=user)

	# Только курсы, пришедшие от организаций вызывающего: человек может
	# состоять в нескольких компаниях, и назначения чужой руководителя не
	# касаются, как и его самозаписи. Список организаций и названия курсов
	# читаются один раз на всю выдачу, а не на каждый курс ученика.
	свои_организации = организации_менеджера(менеджер)
	курсы = [
		запись
		for запись in курсы_ученика(user)
		if запись["organization"] in свои_организации
	]
	названия = _названия_курсов([запись["course"] for запись in курсы])

	занятия = frappe.get_all(
		"Agent Learning Session",
		filters={"student": user},
		fields=["name", "lesson", "course", "status", "started_at", "finished_at"],
		order_by="started_at desc",
		limit=50,
	)
	попытки = frappe.get_all(
		"Agent Quiz Attempt",
		filters={"student": user},
		fields=["quiz", "lesson", "attempt_number", "status", "score", "passed", "finished_at"],
		order_by="finished_at desc",
		limit=50,
	)
	покрытие = _покрытие_целей([з.name for з in занятия])
	return {
		"user": user,
		"full_name": frappe.db.get_value("User", user, "full_name"),
		"courses": [
			{
				"id": запись["course"],
				"title": названия.get(запись["course"]),
				"deadline": запись["deadline"],
				"overdue": запись["overdue"],
				"mandatory": запись["mandatory"],
			}
			for запись in курсы
		],
		"sessions": [
			{
				"lesson": з.lesson,
				"course": з.course,
				"status": з.status,
				"started_at": з.started_at.isoformat() if з.started_at else None,
				"finished_at": з.finished_at.isoformat() if з.finished_at else None,
				"objectives": покрытие.get(з.name, []),
			}
			for з in занятия
		],
		"quiz_attempts": [
			{
				"lesson": п.lesson,
				"attempt": п.attempt_number,
				"status": п.status,
				"score": п.score,
				"passed": bool(п.passed),
				"finished_at": п.finished_at.isoformat() if п.finished_at else None,
			}
			for п in попытки
		],
	}


def _покрытие_целей(занятия: list[str]) -> dict[str, list[dict]]:
	"""Как прошли цели каждого занятия — одним запросом на всю выдачу.

	`Why:` занятий здесь до полусотни, и запрос на каждое превратил бы
	открытие карточки сотрудника в полсотни обходов базы.
	"""
	покрытие: dict[str, list[dict]] = {}
	if not занятия:
		return покрытие
	for строка in frappe.get_all(
		"Agent Objective Outcome",
		filters={"parent": ("in", занятия), "parenttype": "Agent Learning Session"},
		fields=["parent", "objective", "status"],
		order_by="parent asc, idx asc",
	):
		покрытие.setdefault(строка.parent, []).append(
			{"objective": строка.objective, "status": строка.status}
		)
	return покрытие


def _адресаты(назначение) -> list[str]:
	"""Кому предназначено назначение — общим правилом, без загрузки документа.

	Копия этого правила здесь уже разъезжалась с оригиналом. Правило одно, но
	поднимать документ с дочерними таблицами на каждое назначение отчёту
	незачем — ради этого и вынесена функция.
	"""
	return адресаты_назначения(
		назначение.name, назначение.organization, назначение.audience
	)


def _строка_отчёта(
	участник: str,
	назначение,
	*,
	уроки: list[str],
	пройдено: int,
	имя: str | None,
	активность,
	документ: dict,
) -> dict:
	from frappe.utils import getdate, nowdate

	доля = (пройдено / len(уроки)) if уроки else 0.0

	if пройдено == 0:
		статус = "not_started"
	elif уроки and пройдено == len(уроки):
		статус = "completed"
	else:
		статус = "in_progress"

	return {
		"user": участник,
		"full_name": имя,
		"course": назначение.course,
		"organization": назначение.organization,
		"status": статус,
		"progress": round(доля, 2),
		"deadline": назначение.deadline,
		"mandatory": bool(назначение.mandatory),
		# Пройденный курс не просрочен: иначе отчёт «кто не успел» после
		# дедлайна показывает всех подряд, включая закрывших курс заранее.
		# У ученика overdue — свойство курса, здесь — свойство человека.
		"overdue": bool(
			назначение.deadline
			and статус != "completed"
			and getdate(назначение.deadline) < getdate(nowdate())
		),
		"last_activity": активность.isoformat() if активность else None,
		"document": документ,
	}


