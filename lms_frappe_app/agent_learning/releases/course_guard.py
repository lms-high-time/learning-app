# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Курс из релиза правится только публикацией релиза (learning-services#500, #512).

Хук `validate` у `LMS Course`: правка из desk и любой путь мимо сервиса
публикации проходят здесь же. Ключ курса и действующий релиз ставит только
сервис публикации (`ИЗ_РЕЛИЗА` во флагах документа), а действующий релиз —
релиз этого курса.

`Why:` на ключе держится поиск курса публикацией, на действующем релизе —
программа курса. Ключ, поправленный в desk, увёл бы следующий релиз в новый
курс, чужой релиз подменил бы программу.

Структура курса из релиза — главы, уроки и их порядок — тоже за публикацией:
хуки `validate` и `on_trash` у `Course Chapter` и `Course Lesson`, порядок глав
в `проверить_курс`, методы редактора Learning — в `learning_editor`. Карточка
курса (название, описание, публикация) правится как раньше.

`Why:` правка по кусочку разошлась бы с действующим релизом: индекс релиза, по
которому учат агент и квиз, её не увидел бы, а следующая публикация молча
переписала бы. Курс из релиза — курс с ключом, а не с действующим релизом:
`удалить_курс` снимает действующий релиз до удаления глав и уроков, а ключ
живёт, пока жив курс.
"""

import frappe

from lms_frappe_app.agent_learning.doctype.agent_course_release.agent_course_release import УДАЛЯЕТСЯ_КУРС
from lms_frappe_app.agent_learning.errors import КУРС_ИЗ_РЕЛИЗА, Отказ

РЕЛИЗ = "Agent Course Release"
#: Флаг документа курса, главы или урока, которым сервис публикации помечает свою запись.
ИЗ_РЕЛИЗА = "from_release"
ПОЛЯ_РЕЛИЗА = ("course_key", "active_release")


def проверить_курс(doc, method=None) -> None:
	прежний = doc.get_doc_before_save()
	if not doc.flags.get(ИЗ_РЕЛИЗА):
		for поле in ПОЛЯ_РЕЛИЗА:
			было = прежний.get(поле) if прежний else None
			if (doc.get(поле) or None) != (было or None):
				frappe.throw(
					frappe._("Поле «{0}» ставит публикация релиза, а не правка курса").format(поле),
					title=frappe._("Курс из релиза"),
				)
		if прежний and _главы(doc) != _главы(прежний):
			запретить_правку(doc.name)
	релиз = doc.get("active_release")
	if релиз and frappe.db.get_value(РЕЛИЗ, релиз, "course") != doc.name:
		frappe.throw(frappe._("Действующий релиз — релиз другого курса"), title=frappe._("Курс из релиза"))


def проверить_структуру(doc, method=None) -> None:
	"""Хук `validate` и `on_trash` у `Course Chapter` и `Course Lesson`.

	Глава или урок, сменившие курс, проверяются по обоим курсам: перенос из
	курса из релиза — такая же правка его структуры, как перенос в него.
	"""
	if doc.flags.get(ИЗ_РЕЛИЗА):
		return
	прежний = doc.get_doc_before_save()
	запретить_правку(doc.get("course"), прежний.get("course") if прежний else None)


def запретить_правку(*курсы: str | None) -> None:
	"""Отказ, если среди курсов есть курс из релиза.

	Отказ — `Отказ` через `frappe.throw`: desk и редактор Learning показывают
	текст, методы контракта отдают код `course_from_release`.
	"""
	for курс in dict.fromkeys(filter(None, курсы)):
		if _из_релиза(курс):
			текст = frappe._("Курс собран из релиза: главы и уроки правит новый релиз")
			frappe.throw(
				текст, exc=Отказ(КУРС_ИЗ_РЕЛИЗА, текст, course=курс), title=frappe._("Курс из релиза")
			)


def _из_релиза(курс: str) -> bool:
	"""Курс с ключом релиза; курс, который `удалить_курс` удаляет целиком, — уже нет."""
	if frappe.flags.get(УДАЛЯЕТСЯ_КУРС) == курс:
		return False
	return bool(frappe.db.get_value("LMS Course", курс, "course_key"))


def _главы(курс) -> list[str]:
	return [строка.chapter for строка in курс.get("chapters") or []]
