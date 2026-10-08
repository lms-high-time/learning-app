# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Редактор Learning не правит главы и уроки курса из релиза (learning-services#512).

Методы редактора подменяются хуком `override_whitelisted_methods`: для курса
из релиза — отказ (`course_guard.запретить_правку`), иначе — метод Learning.

`Why:` хуков документа (`course_guard.проверить_структуру`) не хватает: порядок
глав и уроков Learning пишет `frappe.db.set_value` мимо `validate`, ссылку на
урок в главе вставляет строкой `Lesson Reference`, а `delete_chapter` удаляет
главу и уроки `frappe.db.delete` мимо `on_trash`. Внутренний `update_index`
не подменяется: он не whitelisted, и подмена выставила бы его наружу без
проверки прав, а все его whitelisted-вызывающие — здесь.

Проверяются все курсы, которых касается вызов: перенос урока между главами —
правка и курса, откуда урок ушёл, и курса, куда пришёл.
"""

import frappe
from lms.lms import api as learning

from lms_frappe_app.agent_learning.releases.course_guard import запретить_правку

ПОДМЕНЯЕМЫЕ = (
	"delete_chapter",
	"update_lesson_index",
	"update_chapter_index",
	"delete_lesson",
	"create_lesson",
	"upsert_chapter",
)


def _курс_главы(глава: str | None) -> str | None:
	return frappe.db.get_value("Course Chapter", глава, "course") if глава else None


def _курс_урока(урок: str | None) -> str | None:
	return frappe.db.get_value("Course Lesson", урок, "course") if урок else None


@frappe.whitelist()
def delete_chapter(chapter: str):
	запретить_правку(_курс_главы(chapter))
	return learning.delete_chapter(chapter)


@frappe.whitelist()
def update_lesson_index(lesson: str, sourceChapter: str, targetChapter: str, idx: int):
	запретить_правку(_курс_урока(lesson), _курс_главы(sourceChapter), _курс_главы(targetChapter))
	return learning.update_lesson_index(lesson, sourceChapter, targetChapter, idx)


@frappe.whitelist()
def update_chapter_index(chapter: str, course: str, idx: int):
	запретить_правку(course, _курс_главы(chapter))
	return learning.update_chapter_index(chapter, course, idx)


@frappe.whitelist()
def delete_lesson(lesson: str, chapter: str):
	запретить_правку(_курс_урока(lesson), _курс_главы(chapter))
	return learning.delete_lesson(lesson, chapter)


@frappe.whitelist()
def create_lesson(chapter: str) -> str:
	запретить_правку(_курс_главы(chapter))
	return learning.create_lesson(chapter)


@frappe.whitelist()
def upsert_chapter(
	title: str, course: str, is_scorm_package: bool, scorm_package: dict = None, name: str = None
):
	# Аргументы не той формы отклоняет сам Learning — до них проверка не доходит.
	if isinstance(course, str) and (name is None or isinstance(name, str)):
		запретить_правку(course, _курс_главы(name))
	return learning.upsert_chapter(title, course, is_scorm_package, scorm_package, name)
