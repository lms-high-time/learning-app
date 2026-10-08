# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Сигналы агенту — выводы сервера из истории ученика (learning-services#416).

Агент видит один разговор, сервер — все занятия ученика, его отметки и
попытки. Сигнал — код и данные: «урок бросали два занятия подряд», а не сырые
записи. Фразу для агента складывает потребитель контракта — готовые
тексты для агента сюда не входят (CONTRIBUTING.md).

Время сервер использует только сам — решить, что занятие брошено, посчитать
срок, — а наружу отдаёт вывод. `Why:` агенты не отслеживают реальное время и
неверно толкуют его внутри урока: в одном чате бывают паузы, ученик
возвращается назавтра (решение владельца, #416). По той же причине сигналов
«прошло N минут» здесь нет.
"""

from __future__ import annotations

from math import ceil

import frappe
from frappe.utils import getdate, nowdate

from lms_frappe_app.agent_learning.artifacts.document import _заполнен
from lms_frappe_app.agent_learning.constants import ЗАНЯТИЕ_БРОШЕНО, ПРОЙДЕН

БРОШЕНЫ_ПОДРЯД = "abandoned_in_row"
ДОКУМЕНТ_ПУСТ = "blocks_empty"
ТЕМП_К_СРОКУ = "deadline_pace"

#: Порядок — приоритет: когда кандидатов больше предела, остаются первые.
ПРИОРИТЕТ = (БРОШЕНЫ_ПОДРЯД, ДОКУМЕНТ_ПУСТ, ТЕМП_К_СРОКУ)

#: Сколько сигналов в одном ответе. `Why:` подсказка, которая приходит пачкой,
#: перестаёт читаться — агент обработает первую и забудет остальные.
НЕ_БОЛЬШЕ = 3

#: Сколько последних занятий по уроку брошены подряд без единой отметки пункта.
БРОШЕННЫХ_ПОДРЯД = 2

#: С какого темпа к сроку курса стоит сказать ученику: уроков в неделю.
УРОКОВ_В_НЕДЕЛЮ = 3


def занятия_урока(ученик: str, lesson: str, кроме: str) -> list[dict]:
	"""Прошлые занятия ученика по уроку, новые первыми: `{status, marked}`.

	`marked` — в прохождении занятия есть пункт, отмеченный в этом занятии.
	Отметки ищутся по строкам прохождения занятия (`run`) — по индексу
	родителя, а не по всей таблице пунктов, как у `start.opening`.
	"""
	return [
		{"status": з.status, "marked": bool(з.marked)}
		for з in frappe.db.sql(
			"""
			select s.status, exists (
				select 1 from `tabAgent Lesson Run Goal` g
				where g.parent = s.run and g.parenttype = 'Agent Lesson Run' and g.session = s.name
			) as marked
			from `tabAgent Learning Session` s
			where s.student = %(student)s and s.lesson = %(lesson)s and s.name != %(session)s
			order by s.creation desc
			""",
			{"student": ученик, "lesson": lesson, "session": кроме},
			as_dict=True,
		)
	]


def брошены_подряд(занятия: list[dict]) -> list[dict]:
	"""Последние занятия по уроку, брошенные подряд без единой отметки пункта.

	Брошенное с отметками — не срыв: урок прошли в два приёма, а занятие
	закрыл таймаут. Сигнал — о попытках, которые обрывались, не начавшись.
	"""
	подряд = 0
	for занятие in занятия:
		if занятие["status"] != ЗАНЯТИЕ_БРОШЕНО or занятие["marked"]:
			break
		подряд += 1
	if подряд < БРОШЕННЫХ_ПОДРЯД:
		return []
	return [{"code": БРОШЕНЫ_ПОДРЯД, "attempts": подряд}]


def осталось_уроков(ученик: str, курс: str) -> int:
	"""Непройденные уроки курса — одним запросом.

	Порядок уроков здесь не нужен, только их число, поэтому без обхода глав
	`уроки_курса`: тот стоит запроса на каждую главу.
	"""
	return frappe.db.sql(
		"""
		select count(*)
		from `tabCourse Lesson` l
		join `tabCourse Chapter` c on c.name = l.chapter
		where c.course = %(course)s and not exists (
			select 1 from `tabLMS Course Progress` p
			where p.member = %(student)s and p.course = %(course)s
				and p.lesson = l.name and p.status = %(done)s
		)
		""",
		{"course": курс, "student": ученик, "done": ПРОЙДЕН},
	)[0][0]


def темп_к_сроку(срок, просрочен: bool, осталось_уроков: int) -> list[dict]:
	"""Темп к сроку курса в уроках в неделю — когда срок жмёт или прошёл.

	Даты наружу не идут: агенту нужен вывод «успеваем ли», а не календарь.
	"""
	if not срок or осталось_уроков <= 0:
		return []
	if просрочен:
		return [
			{"code": ТЕМП_К_СРОКУ, "lessons_left": осталось_уроков, "lessons_per_week": None, "overdue": True}
		]
	дней = max((getdate(срок) - getdate(nowdate())).days, 1)
	в_неделю = ceil(осталось_уроков * 7 / дней)
	if в_неделю < УРОКОВ_В_НЕДЕЛЮ:
		return []
	return [
		{"code": ТЕМП_К_СРОКУ, "lessons_left": осталось_уроков, "lessons_per_week": в_неделю, "overdue": False}
	]


def отобрать(занятие, кандидаты: list[dict]) -> list[dict]:
	"""Сигналы ответа: новые для занятия, по приоритету, не больше предела.

	Показанный сигнал занятие помнит и второй раз не отдаёт. `Why:`
	`start_lesson` зовут повторно — продолжение, рамка по запросу, веб-чат на
	каждой странице, — и одно и то же наблюдение на каждом вызове стало бы
	шумом, который агент научится пропускать.
	"""
	показанные = set((занятие.get("signals_shown") or "").split())
	новые = [с for с in кандидаты if с["code"] not in показанные]
	новые.sort(key=lambda с: ПРИОРИТЕТ.index(с["code"]))
	новые = новые[:НЕ_БОЛЬШЕ]
	if новые:
		занятие.db_set(
			"signals_shown",
			"\n".join(sorted(показанные | {с["code"] for с in новые})),
			update_modified=False,
		)
	return новые


def документ_пуст(статусы_целей: list[str], блоки: list[dict]) -> list[dict]:
	"""Половина целей урока разобрана, а ни один блок документа урока не начат.

	`статусы_целей` — статусы целей урока в прохождении (снятые не в счёт),
	`блоки` — блоки документа урока с содержимым ученика (`_блоки_урока`).

	`Why:` ранний видимый результат — первый блок документа; к середине урока
	его всё ещё нет, и в конце занятия документ собирают наспех или не
	собирают вовсе.
	"""
	разобрано = sum(с == "covered" for с in статусы_целей)
	if not блоки or not статусы_целей or 2 * разобрано < len(статусы_целей):
		return []
	if any(_заполнен(б) for б in блоки):
		return []
	return [
		{
			"code": ДОКУМЕНТ_ПУСТ,
			"blocks": [{"artifact": б["artifact"], "key": б["key"], "title": б["title"]} for б in блоки],
		}
	]
