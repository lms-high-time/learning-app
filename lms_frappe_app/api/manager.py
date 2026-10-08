# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Методы руководителя организации.

Изоляция проверяется **явно**, и это не дублирование хуков: `frappe.get_all`
— это `get_list(ignore_permissions=True)`, и `permission_query_conditions` к
нему не применяются никогда. Отчёт, положившийся на хуки, отдавал занятия
любого человека на платформе кому угодно — проверено эксплуатацией.
"""

import frappe
from frappe.query_builder.functions import Min

from lms_frappe_app.agent_learning.access import курсы_ученика
from lms_frappe_app.agent_learning.artifacts import data
from lms_frappe_app.agent_learning.artifacts.course import _схемы_курса
from lms_frappe_app.agent_learning.artifacts.document import _заполненность
from lms_frappe_app.agent_learning.constants import ПРОЙДЕН
from lms_frappe_app.agent_learning.doctype.course_allocation.course_allocation import (
	адресаты_назначения,
)
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.structure import уроки_курса
from lms_frappe_app.agent_learning.permissions import (
	видит_всё,
	организации_менеджера,
	свои_организации_пересекаются,
)
from lms_frappe_app.api import контракт, текущий_пользователь

ЧУЖОЙ_УЧЕНИК = "not_your_student"


@frappe.whitelist()
@контракт
def org_report(
	course: str | None = None, status: str | None = None, organization: str | None = None
) -> dict:
	"""Обучение своей организации: кто на чём и что просрочено.

	`organization` — отчёт одной из своих организаций: страница «Команда»
	показывает отчёт той, в чьём пространстве открыта (learning-services#355).
	Чужая — пустой отчёт, как у руководителя без организаций.
	"""
	менеджер = текущий_пользователь()
	организации = организации_менеджера(менеджер)
	if organization:
		организации = [organization] if organization in организации else []
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
		квизы = _квизы_по_участникам(назначение.course, участники)

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
				квиз=квизы.get(участник, {"passed": 0, "first_try": 0}),
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
			схема, содержимое.get(имя, {}), вложения.get(имя, {}), data.данные(экземпляр.data)
		)["blocks_filled"]
		заполнено[экземпляр.student] = заполнено.get(экземпляр.student, 0) + сколько
	return всего, заполнено


def _квизы_по_участникам(курс: str, участники: list[str]) -> dict[str, dict[str, int]]:
	"""Сколько уроков курса сдано и сколько из них с первой попытки — одним запросом.

	`Why:` зачёт — за 100%, без лимита попыток (learning-services#353), и
	«сдан» уже не отличает понявшего от перебравшего ответы. Номер зачтённой
	попытки отличает. Попытки считаются по всем пространствам: прогресс у
	человека общий, и урок, сданный лично, пройден и для компании; наружу
	идут только числа, без ответов.
	"""
	попытка = frappe.qb.DocType("Agent Quiz Attempt")
	строки = (
		frappe.qb.from_(попытка)
		.select(попытка.student, попытка.lesson, Min(попытка.attempt_number).as_("first"))
		.where(
			(попытка.course == курс)
			& (попытка.student.isin(участники))
			& (попытка.passed == 1)
		)
		.groupby(попытка.student, попытка.lesson)
		.run(as_dict=True)
	)
	итог: dict[str, dict[str, int]] = {}
	for строка in строки:
		счёт = итог.setdefault(строка.student, {"passed": 0, "first_try": 0})
		счёт["passed"] += 1
		if строка.first == 1:
			счёт["first_try"] += 1
	return итог


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
	Покрытие — цели урока со статусом из прохождения урока, без пунктов и
	свидетельств; прохождение одно на урок, поэтому у занятий одного урока
	покрытие одно и то же — каким оно стало к этому часу.
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

	# Занятия и попытки — только в пространствах организаций вызывающего:
	# личную работу и работу для другой компании руководитель не видит
	# (learning-services#344). Сотрудник платформы видит всё.
	пространства = None if видит_всё(менеджер) else свои_организации
	занятия = frappe.get_all(
		"Agent Learning Session",
		filters={"student": user, **({"organization": ("in", пространства)} if пространства is not None else {})},
		fields=["name", "lesson", "course", "status", "started_at", "finished_at", "run"],
		order_by="started_at desc",
		limit=50,
	)
	попытки = _попытки_в_пространствах(user, пространства)
	покрытие = _цели_прохождений(user, занятия)
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


def _попытки_в_пространствах(user: str, организации: list[str] | None) -> list[dict]:
	"""Попытки квиза ученика в занятиях этих организаций; `None` — все. Одним запросом."""
	попытка = frappe.qb.DocType("Agent Quiz Attempt")
	занятие = frappe.qb.DocType("Agent Learning Session")
	запрос = (
		frappe.qb.from_(попытка)
		.select(
			попытка.quiz,
			попытка.lesson,
			попытка.attempt_number,
			попытка.status,
			попытка.score,
			попытка.passed,
			попытка.finished_at,
		)
		.where(попытка.student == user)
		.orderby(попытка.finished_at, order=frappe.qb.desc)
		.limit(50)
	)
	if организации is not None:
		запрос = запрос.join(занятие).on(попытка.session == занятие.name).where(
			занятие.organization.isin(организации)
		)
	return запрос.run(as_dict=True)


def _цели_прохождений(ученик: str, занятия: list) -> dict[str, list[dict]]:
	"""Цели урока каждого занятия со статусом из прохождения: занятие → `[{key, text, status}]`.

	Прохождение занятия — его `run`, у занятия без него — живое прохождение
	ученика по уроку занятия. Тексты — из релиза прохождения; снятые новым
	релизом цели не отдаются. Занятие курса старой модели — пусто.

	`Why:` занятий здесь до полусотни, и запрос на каждое превратил бы
	открытие карточки сотрудника в полсотни обходов базы: прохождения ученика
	— одной выборкой, цели всех нужных прохождений — второй.
	"""
	живые = {
		(п.course, п.lesson): п.name
		for п in frappe.get_all(
			"Agent Lesson Run", filters={"student": ученик}, fields=["name", "course", "lesson"]
		)
	}
	прохождение = {з.name: з.run or живые.get((з.course, з.lesson)) for з in занятия}
	нужные = {имя for имя in прохождение.values() if имя}
	if not нужные:
		return {}
	цели: dict[str, list[dict]] = {}
	for с in frappe.db.sql(
		"""
		select o.parent, o.objective_key, o.status, t.text
		from `tabAgent Lesson Run Objective` o
		join `tabAgent Lesson Run` r on r.name = o.parent
		left join `tabAgent Release Objective` t
			on t.parenttype = 'Agent Course Release' and t.parent = r.release
			and t.lesson_key = r.lesson_key and t.objective_key = o.objective_key
		where o.parenttype = 'Agent Lesson Run' and o.parent in %(runs)s and o.removed = 0
		order by o.idx
		""",
		{"runs": tuple(нужные)},
		as_dict=True,
	):
		цели.setdefault(с.parent, []).append({"key": с.objective_key, "text": с.text, "status": с.status})
	return {занятие: цели.get(имя, []) for занятие, имя in прохождение.items() if имя}


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
	квиз: dict,
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
		"quiz": квиз,
	}


