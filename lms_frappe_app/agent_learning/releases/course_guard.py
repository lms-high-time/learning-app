# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Курс из релиза правится только публикацией релиза (learning-services#500, #512).

Хук `validate` у `LMS Course`: правка из desk и любой путь мимо сервиса
публикации проходят здесь же. Ключ курса и действующий релиз ставит только
сервис публикации (`ИЗ_РЕЛИЗА` во флагах документа), а действующий релиз —
релиз этого курса с содержимым: освобождённая версия
(`retention`, learning-services#514) действующей не становится.

`Why:` на ключе держится поиск курса публикацией, на действующем релизе —
программа курса. Ключ, поправленный в desk, увёл бы следующий релиз в новый
курс, чужой релиз подменил бы программу.

Структура курса из релиза — главы, уроки и их порядок — тоже за публикацией:
хуки `validate` и `on_trash` у `Course Chapter` и `Course Lesson`, `on_trash` у
строк оглавления (`проверить_ссылку`), `before_rename` у курса, главы и урока
(`проверить_переименование`), порядок глав в `проверить_курс`, методы
редактора Learning — в `learning_editor`. Карточка курса (название, описание,
публикация) правится как раньше.

Домашки уроков и схема документа курса из релиза — проекции релиза, как главы
и уроки (learning-services#526): хуки `validate` и `on_trash` у
`Agent Lesson Homework` и `Agent Course Artifact` (`проверить_домашку`,
`проверить_документ`), `on_trash` у строк схемы (`проверить_блок`),
`before_rename` у схемы.

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
ДОМАШКА = "Agent Lesson Homework"
ДОКУМЕНТ = "Agent Course Artifact"
#: Флаг записи курса, главы, урока, домашки или схемы документа, которым сервис
#: публикации помечает свою запись.
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
	if not релиз:
		return
	# Снимок — признаком, а не значением: его мегабайты в ответ не идут.
	# `is not null`, а не `("is", "set")`: см. `retention`.
	запись = frappe.db.sql(f"select course, snapshot is not null from `tab{РЕЛИЗ}` where name = %s", релиз)
	if not запись:
		frappe.throw(frappe._("Такого релиза нет"), title=frappe._("Курс из релиза"))
	[(курс_релиза, со_снимком)] = запись
	if not со_снимком:
		frappe.throw(
			frappe._("Содержимое этой версии освобождено: откат — публикация прежнего коммита новой версией"),
			title=frappe._("Курс из релиза"),
		)
	if курс_релиза != doc.name:
		frappe.throw(frappe._("Действующий релиз — релиз другого курса"), title=frappe._("Курс из релиза"))


def проверить_структуру(doc, method=None) -> None:
	"""Хук `validate` и `on_trash` у `Course Chapter` и `Course Lesson`.

	Глава или урок, сменившие курс, проверяются по обоим курсам: перенос из
	курса из релиза — такая же правка его структуры, как перенос в него.

	`on_trash` контроллера Learning у урока (`cleanup_lesson_backreferences`:
	заметки ученика, ссылки квиза, записи, сдачи) выполняется раньше этого
	хука. Отказ здесь откатывает и его: всё идёт в одной транзакции запроса.
	"""
	if doc.flags.get(ИЗ_РЕЛИЗА):
		return
	прежний = doc.get_doc_before_save()
	запретить_правку(doc.get("course"), прежний.get("course") if прежний else None)


def проверить_ссылку(doc, method=None) -> None:
	"""Хук `on_trash` у `Chapter Reference` и `Lesson Reference`: строку
	оглавления курса из релиза не удалить саму по себе.

	`Why:` Desk (`delete_items`) и `delete_documents` Learning удаляют строку
	`frappe.delete_doc` с проверкой права `delete` на родителе, а оно у Course
	Creator есть: глава или урок выпали бы из оглавления мимо `validate` курса
	и главы. Строки, которые снимает сохранение родителя (проекция релиза), и
	`frappe.db.delete` (`delete_course` Learning) этот хук не вызывают: первые
	проверяет `validate` родителя, вторые идут удалением курса целиком.
	"""
	if doc.parenttype == "LMS Course":
		курс = doc.parent
	else:
		курс = frappe.db.get_value("Course Chapter", doc.parent, "course")
	запретить_правку(курс)


def проверить_домашку(doc, method=None) -> None:
	"""Хук `validate` и `on_trash` у `Agent Lesson Homework`: домашку урока курса
	из релиза пишет только публикация (`releases.homework`).

	Курс домашки — курс её урока; домашка, сменившая урок, проверяется по обоим
	курсам, как глава и урок в `проверить_структуру`.
	"""
	if doc.flags.get(ИЗ_РЕЛИЗА):
		return
	прежний = doc.get_doc_before_save()
	уроки = dict.fromkeys(filter(None, [doc.get("lesson"), прежний.get("lesson") if прежний else None]))
	курсы = [frappe.db.get_value("Course Lesson", урок, "course") for урок in уроки]
	запретить_правку(*курсы, текст=_текст_проекции())


def проверить_документ(doc, method=None) -> None:
	"""Хук `validate` и `on_trash` у `Agent Course Artifact`: схему документа курса
	из релиза пишет только публикация (`releases.document`)."""
	if doc.flags.get(ИЗ_РЕЛИЗА):
		return
	прежний = doc.get_doc_before_save()
	курсы = [doc.get("course"), прежний.get("course") if прежний else None]
	запретить_правку(*курсы, текст=_текст_проекции())


def проверить_блок(doc, method=None) -> None:
	"""Хук `on_trash` у `Agent Artifact Block`: строку схемы документа курса из
	релиза не удалить саму по себе.

	`Why:` то же, что у строк оглавления (`проверить_ссылку`): `delete_items` Desk
	удаляет строку `frappe.delete_doc` с проверкой права на схеме, а оно у
	Moderator есть. Строки, которые снимает сохранение схемы, проверяет
	`validate` схемы.
	"""
	if doc.parenttype == ДОКУМЕНТ:
		запретить_правку(frappe.db.get_value(ДОКУМЕНТ, doc.parent, "course"), текст=_текст_проекции())


def проверить_переименование(doc, method=None, old=None, new=None, merge=False) -> None:
	"""Хук `before_rename` у `LMS Course`, `Course Chapter`, `Course Lesson` и
	`Agent Course Artifact`.

	`Why:` `rename_doc` идёт мимо `validate` и `on_trash`, а `merge` урока
	курса из релиза с другим уроком перевёл бы на тот урок строку индекса
	релиза, оглавление и прогресс учеников. При `merge` проверяется и курс
	записи, с которой сливают. Проекция релиза и `удалить_курс` ничего не
	переименовывают.
	"""
	if doc.doctype == "LMS Course":
		курсы = [doc.name, new if merge else None]
	else:
		курсы = [doc.get("course"), frappe.db.get_value(doc.doctype, new, "course") if merge else None]
	запретить_правку(*курсы, текст=_текст_проекции() if doc.doctype == ДОКУМЕНТ else None)


def запретить_правку(*курсы: str | None, текст: str | None = None) -> None:
	"""Отказ, если среди курсов есть курс из релиза.

	Отказ — `Отказ` через `frappe.throw`: desk и редактор Learning показывают
	текст, методы контракта отдают код `course_from_release`.
	"""
	for курс in dict.fromkeys(filter(None, курсы)):
		if _из_релиза(курс):
			текст = текст or frappe._("Курс собран из релиза: главы и уроки правит новый релиз")
			frappe.throw(
				текст, exc=Отказ(КУРС_ИЗ_РЕЛИЗА, текст, course=курс), title=frappe._("Курс из релиза")
			)


def _текст_проекции() -> str:
	"""Текст отказа для домашек и схемы документа; у курса, глав и уроков — свой."""
	return frappe._("Курс собран из релиза: домашки и документ курса правит новый релиз")


def _из_релиза(курс: str) -> bool:
	"""Курс с ключом релиза; курс, который `удалить_курс` удаляет целиком, — уже нет."""
	if frappe.flags.get(УДАЛЯЕТСЯ_КУРС) == курс:
		return False
	return bool(frappe.db.get_value("LMS Course", курс, "course_key"))


def _главы(курс) -> list[str]:
	"""Главы курса в порядке `idx`: по нему порядок читает Learning.

	`Why:` порядок строк в памяти и `idx` расходятся у `frappe.client.save` с
	переставленными `idx` и у `frappe.client.set_value` по строке: сравнение
	строк в памяти таких перестановок не видит.
	"""
	return [строка.chapter for строка in sorted(курс.get("chapters") or [], key=lambda с: с.idx or 0)]
