# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Профиль ученика — фиксированный набор фактов `Agent Student Note`.

`Why:` профиль не заводит своих полей: роль в профиле и факт `role` у агента
разошлись бы (learning-services#463). Подсказки «о чём спрашивать» здесь не
живут — это текст для агента, а приложение открыто (CLAUDE.md платформы §10).
"""

import frappe
from frappe import _

from lms_frappe_app.agent_learning.constants import ЗАМЕТКА_ФАКТ

#: Блок → ключи фактов. Порядок — порядок на странице и в интервью.
ПРОФИЛЬ = (
	("work", "Work context", (("role", "Role"), ("industry", "Industry"), ("company", "Company and team"))),
	("goal", "Goal and task", (("goal", "Learning goal"), ("current_task", "Current task"))),
	("level", "Starting level", (("background", "Experience"), ("tools", "Tools and methods"))),
	("style", "How I learn best", (("learning_style", "Learning style"), ("time_budget", "Time per week"))),
)
КЛЮЧИ_ПРОФИЛЯ = frozenset(ключ for _блок, _имя, поля in ПРОФИЛЬ for ключ, _подпись in поля)
#: С какого числа заполненных ключей профиль считается заполненным и
#: подсказка в сайдбаре гаснет.
ПОРОГ_ЗАПОЛНЕННОСТИ = 6
#: Контекст перевода подписей. `Why:` `ru.csv` действует на всю платформу, и
#: «Experience» без контекста переименовал бы чужую строку upstream в «Опыт в
#: теме».
КОНТЕКСТ_ПЕРЕВОДА = "Learner profile"


def заполненность(ученик: str) -> dict:
	"""Сколько ключей профиля заполнено — одним `count`, без самих фактов.

	`Why:` её читает сайдбар на каждой загрузке, и тянуть ради одной
	подсказки весь профиль незачем.
	"""
	заполнено = frappe.db.count(
		"Agent Student Note",
		{
			"student": ученик,
			"course": "",
			"kind": ЗАМЕТКА_ФАКТ,
			"note_key": ("in", tuple(КЛЮЧИ_ПРОФИЛЯ)),
		},
	)
	return _итог(заполнено)


def профиль(ученик: str) -> dict:
	"""Факты ученика по блокам профиля и факты вне набора.

	Курсовые наблюдения и проекты сюда не входят: они про конкретный курс, а
	профиль — про человека (#408). Факты вне набора не теряются, а идут в
	`other_facts`, свежие сверху.

	`ignore_permissions` здесь безопасен: фильтр по ученику уже сузил выборку
	до одного человека, а кому этот человек виден, решает вызывающий метод.
	"""
	записи = frappe.get_all(
		"Agent Student Note",
		filters={"student": ученик, "course": "", "kind": ЗАМЕТКА_ФАКТ},
		fields=["note_key", "text", "modified"],
		order_by="modified desc",
		ignore_permissions=True,
	)
	по_ключу = {запись.note_key: запись for запись in записи}
	блоки = [
		{
			"id": блок,
			"title": _(имя, context=КОНТЕКСТ_ПЕРЕВОДА),
			"facts": [_факт(ключ, по_ключу.get(ключ), подпись) for ключ, подпись in поля],
		}
		for блок, имя, поля in ПРОФИЛЬ
	]
	прочие = [
		_факт(запись.note_key, запись)
		for запись in записи
		if запись.note_key not in КЛЮЧИ_ПРОФИЛЯ
	]
	заполнено = sum(1 for ключ in КЛЮЧИ_ПРОФИЛЯ if ключ in по_ключу)
	return {"blocks": блоки, "other_facts": прочие, **_итог(заполнено)}


def _факт(ключ: str, запись, подпись: str | None = None) -> dict:
	"""Факт наружу. Подпись есть только у ключей набора: прочие ключи придумал агент."""
	факт = {"key": ключ}
	if подпись is not None:
		факт["label"] = _(подпись, context=КОНТЕКСТ_ПЕРЕВОДА)
	факт["text"] = запись.text if запись else None
	факт["updated"] = запись.modified.isoformat() if запись else None
	return факт


def _итог(заполнено: int) -> dict:
	return {
		"filled": заполнено,
		"total": len(КЛЮЧИ_ПРОФИЛЯ),
		"complete": заполнено >= ПОРОГ_ЗАПОЛНЕННОСТИ,
	}
