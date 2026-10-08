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
"""

import json

import frappe

from lms_frappe_app.agent_learning import homework
from lms_frappe_app.agent_learning.releases import index

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
	for урок, шаблон in шаблоны.items():
		if not домашки.get(урок):
			_снять(шаблон)


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


def _записать(урок: str, домашка: dict, шаблон) -> None:
	поля = _поля(домашка)
	if not шаблон:
		frappe.get_doc({"doctype": ЗАДАНИЕ, "lesson": урок, **поля}).insert()
		return
	if any((шаблон.get(поле) or None) != (значение or None) for поле, значение in поля.items()):
		документ = frappe.get_doc(ЗАДАНИЕ, шаблон.name)
		документ.update(поля)
		документ.save()


def _снять(шаблон) -> None:
	"""Шаблон домашки, которой в релизе больше нет: без сдач — удалить, со сдачами — снять.

	Сдачи в счёт и архивные после сброса прогресса: они ссылаются на шаблон.
	"""
	if frappe.db.exists(homework.СДАЧА, {"homework": шаблон.name}):
		if not шаблон.retired:
			документ = frappe.get_doc(ЗАДАНИЕ, шаблон.name)
			документ.retired = 1
			документ.save()
		return
	homework.снять_сроки_назначений(шаблон.name)
	frappe.delete_doc(ЗАДАНИЕ, шаблон.name)


def удалить_шаблоны(курс: str) -> None:
	"""Шаблоны уроков курса — перед удалением курса.

	Шаблон со сдачами не удаляется: удаление курса остановит ссылка сдачи.
	"""
	for шаблон in homework.задания_курса(курс).values():
		homework.снять_сроки_назначений(шаблон.name)
		frappe.delete_doc(ЗАДАНИЕ, шаблон.name, ignore_permissions=True)
