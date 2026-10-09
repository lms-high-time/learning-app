# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Домашка урока из релиза — проекцией в шаблон `Agent Lesson Homework` (learning-services#504).

Домашка — часть релиза (решение владельца, #497), а шаблон урока — её
проекция, как глава и урок Learning. `Why:` выдача, сроки, проверка куратором
и права уже работают на шаблоне.

Домашка ушла из релиза — вместе с уроком или без него: шаблон без сдач
удаляется, со сдачами получает `retired` — его больше не выдают, а выданное
ученик сдаёт и куратор проверяет. Вернулась — тот же шаблон (`lesson`
уникален) снова действует.

Права: проекция пишет шаблоны без проверки прав и с флагом `ИЗ_РЕЛИЗА`, как
`projection.py` — главы и уроки. `Why:` у Course Creator на шаблоны только
чтение, а мимо публикации шаблон курса из релиза не правит никто
(`course_guard.проверить_домашку`, learning-services#526). `удалить_шаблоны`
(удаление курса, путь администратора) — тоже без проверки прав.
"""

import json

import frappe

from lms_frappe_app.agent_learning import homework
from lms_frappe_app.agent_learning.releases import index
from lms_frappe_app.agent_learning.releases.course_guard import ИЗ_РЕЛИЗА

ЗАДАНИЕ = homework.ЗАДАНИЕ


def спроецировать(курс: str, домашки: dict[str, dict | None]) -> None:
	"""Шаблоны уроков курса — по домашкам релиза.

	`домашки` — урок Learning → домашка релиза или `None` по всем урокам
	релиза. У уроков курса, которых в релизе нет, домашки нет.
	"""
	шаблоны = homework.задания_курса(курс)
	for урок, домашка in домашки.items():
		if домашка:
			_записать(урок, домашка, шаблоны.get(урок))
	снятые = [шаблон for урок, шаблон in шаблоны.items() if not домашки.get(урок)]
	if not снятые:
		return
	# Why: сдачи всех снимаемых шаблонов — одним запросом по индексу `lesson`,
	# а не запросом на шаблон при каждой публикации.
	есть_сдачи = set(
		frappe.get_all(
			homework.СДАЧА,
			filters={"lesson": ("in", [шаблон.lesson for шаблон in снятые])},
			pluck="homework",
			distinct=True,
		)
	)
	for шаблон in снятые:
		_снять(шаблон, шаблон.name in есть_сдачи)


def спроецировать_действующий(курс: str) -> None:
	"""Шаблоны уроков курса — по его действующему релизу; релиза нет — ничего."""
	релиз = frappe.db.get_value("LMS Course", курс, "active_release")
	if not релиз:
		return
	строки = frappe.get_all(
		index.УРОК,
		filters={"parenttype": index.РЕЛИЗ, "parent": релиз},
		fields=["lesson", "homework"],
	)
	спроецировать(курс, {с.lesson: json.loads(с.homework) if с.homework else None for с in строки})


def _поля(домашка: dict) -> dict:
	"""Поля шаблона по домашке релиза.

	`Why:` относительный срок в 0 дней шаблон не принимает (`проверить_срок`),
	а релиз — да: такая домашка — без срока.
	"""
	дней = домашка["due_days"] or 0
	return {
		"title": домашка["title"],
		"description": домашка["description"],
		"answer_mode": домашка["answer_mode"],
		"due_mode": "relative" if дней > 0 else "none",
		"due_days": дней if дней > 0 else None,
		"due_date": None,
		"retired": 0,
	}


def _записать(урок: str, домашка: dict, шаблон: frappe._dict | None) -> None:
	поля = _поля(домашка)
	if not шаблон:
		_сохранить(frappe.get_doc({"doctype": ЗАДАНИЕ, "lesson": урок, **поля}))
		return
	if any((шаблон.get(поле) or None) != (значение or None) for поле, значение in поля.items()):
		документ = frappe.get_doc(ЗАДАНИЕ, шаблон.name)
		документ.update(поля)
		_сохранить(документ)


def _сохранить(документ) -> None:
	документ.flags[ИЗ_РЕЛИЗА] = True
	документ.save(ignore_permissions=True)


def _снять(шаблон: frappe._dict, есть_сдачи: bool) -> None:
	"""Шаблон домашки, которой в релизе больше нет: без сдач — удалить, со сдачами — снять.

	Сдачи в счёт и архивные после сброса прогресса: они ссылаются на шаблон.
	Уже снятый шаблон со сдачами не трогается.

	`Why:` перед удалением — блокировка шаблона и блокирующее чтение сдач.
	Выдача и первое сохранение блокируют шаблон перед вставкой сдачи
	(`homework.заблокировать_задание`): без этого публикация удалила бы шаблон,
	пока параллельная вставка заводит по нему сдачу со своего снимка, — и сдача
	осталась бы без задания. Проигравший при снимочной изоляции MariaDB получает
	взаимоблокировку или «запись изменилась» и откатывается; фоновая выдача
	повторяет себя (`выдать_по_записи`).
	"""
	if not есть_сдачи:
		frappe.db.get_value(ЗАДАНИЕ, шаблон.name, "name", for_update=True)
		есть_сдачи = bool(
			frappe.db.get_value(homework.СДАЧА, {"homework": шаблон.name}, "name", for_update=True)
		)
	if есть_сдачи:
		if not шаблон.retired:
			документ = frappe.get_doc(ЗАДАНИЕ, шаблон.name)
			документ.retired = 1
			_сохранить(документ)
		return
	homework.снять_сроки_назначений(шаблон.name)
	frappe.delete_doc(ЗАДАНИЕ, шаблон.name, ignore_permissions=True, flags={ИЗ_РЕЛИЗА: True})


def удалить_шаблоны(курс: str) -> None:
	"""Шаблоны уроков курса — перед удалением курса.

	Шаблон со сдачами не удаляется: удаление курса остановит ссылка сдачи.
	Без проверки прав, как релизы и схемы документа в `service.удалить_курс`:
	удаление курса решено вызывающим, а шаблоны — проекции его релиза.
	"""
	for шаблон in homework.задания_курса(курс).values():
		homework.снять_сроки_назначений(шаблон.name)
		frappe.delete_doc(ЗАДАНИЕ, шаблон.name, ignore_permissions=True)
