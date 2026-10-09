# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Урок закрывает занятие с агентом, а не просмотр страницы.

`Why:` Frappe Learning закрывает урок сам: страница урока через
`lesson_dwell_time` секунд зовёт `save_progress`, и появляется
`LMS Course Progress` со статусом `Complete` — та же запись, по которой агент
выбирает следующий урок, а отчёт руководителя считает пройденное. Ученик,
пролиставший урок в браузере, получал его закрытым без занятия и квиза, и
агент этот урок пропускал (lms-platform#305).

Метод Learning подменяется хуком `override_whitelisted_methods`, а не правкой
форка: правило обязано держаться и против прямого вызова метода своим
токеном, минуя страницу. Отказ, а не тихий успех: на успешный ответ страница
урока ставит зелёную отметку, которая пропадает после перезагрузки.

Подменяется каждое имя, под которым функция видна по HTTP: хук сверяет
строку вызова, а не функцию, а Learning импортирует `save_progress` и в
`lms.lms.api`, и в `lms_quiz`. Сторож — тест, который ищет функцию по всем
загруженным модулям. Здесь она зовётся через модуль, а не своим именем: имя
в этом модуле было бы ещё одним входом мимо подмены.

Урок курса из релиза закрывает только занятие (`закрытие_урока`), и держится
это на самой отметке — хук `validate` у `LMS Course Progress`
(`проверить_отметку`), а не только на подмене методов (learning-services#525).
`Why:` подмена срабатывает только на HTTP-вызов под подменённым именем, а
Learning доходит до `save_progress` и прямыми вызовами Python: из `mark_lesson_progress`
(браузерный квиз и задание урока), из `submit_quiz` (квиз Learning,
привязанный к уроку) и из этой подмены — SCORM-прогресс с `scorm_details`.
Проверки Learning на этих путях — запись на курс и порядок уроков — урок из
релиза не держат: квиза и задания Learning в нём нет, и каждое закрытие
открывает следующий урок. Ученик своим токеном закрывал бы так весь курс — с
сертификатом и следующим курсом программы.

Мимо `validate` (`frappe.db.set_value`) Learning правит отметку только на
SCORM-пути — по уже заведённой незакрытой отметке, например снятой
администратором, — и хук `validate` его не видит. Поэтому SCORM-прогресс урока
из релиза подмена `save_progress` отклоняет сразу, под любым именем метода.

Чего не делает: на курсе без релиза браузерный квиз и задание урока
закрывают урок, как в Learning, — методом `mark_lesson_progress`, — и
прогресс SCORM-главы идёт этим же методом с `scorm_details`: занятия с
агентом по ним нет. Не держит и тех, кому Desk даёт права на отметки
(`проверить_отметку`): `Course Creator`, записанный на курс из релиза,
закроет урок и `mark_lesson_progress`.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import frappe
from lms.lms.doctype.course_lesson import course_lesson

#: Имя, которым метод зовёт страница урока; прочие имена той же функции — в `hooks.py`.
ПОДМЕНЯЕМЫЙ = "lms.lms.doctype.course_lesson.course_lesson.save_progress"
ОТКАЗ = "Урок закрывается на занятии с агентом — в веб-чате платформы или через вашего агента"
#: `frappe.flags`: (ученик, урок), отметку которых сейчас пишет занятие (`закрытие_урока`).
ЗАКРЫВАЕТСЯ = "lms_lesson_closing"


@frappe.whitelist()
def save_progress(lesson: str, course: str, scorm_details: dict | None = None):
	"""Прогресс SCORM — как у Learning, кроме урока курса из релиза; всё остальное — отказ."""
	if scorm_details and not _из_релиза(lesson):
		return course_lesson.save_progress(lesson, course, scorm_details)
	frappe.throw(ОТКАЗ, frappe.ValidationError)


@contextmanager
def закрытие_урока(ученик: str, урок: str) -> Iterator[None]:
	"""Внутри блока отметку пройденного урока `урок` у ученика `ученик` пишет занятие.

	Пометка — пара ученика и урока, а не «можно всё»: что бы ещё ни писалось
	внутри блока, отметку другого урока или ученика она не открывает.
	"""
	прежняя = frappe.flags.get(ЗАКРЫВАЕТСЯ)
	frappe.flags[ЗАКРЫВАЕТСЯ] = (ученик, урок)
	try:
		yield
	finally:
		frappe.flags[ЗАКРЫВАЕТСЯ] = прежняя


def проверить_отметку(отметка, method=None) -> None:
	"""`validate` у `LMS Course Progress`: отметку урока курса из релиза пишет занятие.

	Без занятия — только тот, кому Desk даёт создавать и править отметки
	(права DocType: у Learning это `Moderator`, `Course Creator`,
	`System Manager`), — администратор, поправляющий прогресс руками. Права —
	пользователя, а не вызова: Learning пишет отметку ученика с
	`ignore_permissions`, а прав на отметки у ученика нет.

	Курс — по уроку, а не по полю `course` отметки: его Learning заполняет из
	главы, а `save_progress` присылает только урок.
	"""
	if frappe.flags.get(ЗАКРЫВАЕТСЯ) == (отметка.member, отметка.lesson):
		return
	if not _из_релиза(отметка.lesson):
		return
	право = "create" if отметка.is_new() else "write"
	if frappe.has_permission("LMS Course Progress", право, doc=отметка, user=frappe.session.user):
		return
	frappe.throw(ОТКАЗ, frappe.ValidationError)


def _из_релиза(урок: str) -> bool:
	"""Урок курса с действующим релизом."""
	курс = frappe.db.get_value("Course Lesson", урок, "course")
	return bool(курс and frappe.get_cached_value("LMS Course", курс, "active_release"))
