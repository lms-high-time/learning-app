# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Методы сборки курса.

Куратор собирает курс в диалоге со своим агентом. Методы отвечают за хранение
и нормализацию; чем курс хорош — дело агента куратора, не этого файла.

Удаления здесь нет намеренно: снятая с публикации ошибка обратима, удалённый
урок с прогрессом учеников — нет.
"""

import json
from urllib.parse import quote

import frappe
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning import (
	announcements,
	course_builder,
	course_map,
	directives,
	homework,
	normalizer,
	notes,
	notices,
	quiz,
	snapshots,
	structure,
	testers,
)
from lms_frappe_app.agent_learning.artifacts import templates
from lms_frappe_app.agent_learning.artifacts.course import _действующие_артефакты, записать_схему
from lms_frappe_app.agent_learning.constants import (
	ВИДЫ_РЕПОРТОВ,
	ИМЯ_ВИДА_РЕПОРТА,
	ИМЯ_СТАТУСА_РЕПОРТА,
	ОТКРЫТЫЕ_РЕПОРТЫ,
	СТАТУСЫ_РЕПОРТОВ,
)
from lms_frappe_app.agent_learning.errors import (
	НЕИЗВЕСТНЫЙ_ВИД_РЕПОРТА,
	Отказ,
	УРОК_НЕ_НАЙДЕН,
)
from lms_frappe_app.api import контракт, список, текущий_пользователь

#: Роли, которым разрешено собирать курсы. Совпадают с административными в
#: `permissions`: там они уже дают полный доступ к учебным записям.
АВТОРСКИЕ_РОЛИ = frozenset({"Course Creator", "Moderator", "System Manager", "Administrator"})

#: Поля директивы, которые куратор видит в черновике и в уроке. Порядок тот
#: же, в каком их принимают `set_directive` и `set_course_directive`.
ПОЛЯ_ДИРЕКТИВЫ = (
	"objectives",
	"teaching_directive",
	"probing_questions",
	"common_misconceptions",
	"success_criteria",
)
ПОЛЯ_ДИРЕКТИВЫ_КУРСА = (
	"objectives",
	"teaching_directive",
	"student_profile",
	"glossary",
	"remember_about_student",
)

КУРС_НЕ_НАЙДЕН = "course_not_found"
ГЛАВА_НЕ_НАЙДЕНА = "chapter_not_found"
КУРС_НЕ_ГОТОВ = "course_not_ready"
КВИЗ_УЖЕ_ЕСТЬ = "quiz_exists"
КВИЗА_НЕТ = "quiz_missing"
ВОПРОС_НЕ_НАЙДЕН = "question_not_found"
УРОК_В_РАБОТЕ = "lesson_in_use"
ГЛАВА_НЕ_ПУСТА = "chapter_not_empty"
НЕВЕРНЫЙ_ВОПРОС = "invalid_question"
НЕТ_ЦЕЛЕЙ_КУРСА = "course_objectives_missing"
НЕТ_АДРЕСОВ = "users_required"
ТЕСТЕР_НЕ_НАЙДЕН = "tester_not_found"
КУРС_УЖЕ_ОТКРЫТ = "course_already_published"


def _автор() -> str:
	"""Пользователь с правом собирать курсы.

	Отдельный эндпоинт `/authoring` не защищает ничего — адрес известен, токен
	у ученика тот же. Единственная настоящая граница здесь.

	`Why:` отказ идёт ошибкой прав, а не кодом контракта: у агента ученика
	нет сценария, в котором он что-то с этим сделает, а 403 однозначен и
	попадает в журнал Frappe как нарушение доступа.
	"""
	пользователь = текущий_пользователь()
	if not set(frappe.get_roles(пользователь)) & АВТОРСКИЕ_РОЛИ:
		frappe.throw(frappe._("Сборка курсов доступна кураторам"), frappe.PermissionError)
	return пользователь


# --- курс ---


@frappe.whitelist(methods=["POST"])
@контракт
def create_course(title: str, summary: str, description: str | None = None) -> dict:
	"""Заводит черновик курса. Публикуется отдельным вызовом."""
	автор = _автор()
	курс = frappe.get_doc(
		{
			"doctype": "LMS Course",
			"title": title,
			"short_introduction": summary,
			"description": description or summary,
			"published": 0,
			"instructors": [{"instructor": автор}],
		}
	).insert()
	return {"id": курс.name, "title": курс.title, "published": False}


@frappe.whitelist()
@контракт
def list_courses(published: bool | None = None) -> dict:
	"""Курсы платформы: черновики и опубликованные.

	Без этого метода сборка разваливается на второй сессии: все остальные
	инструменты требуют идентификатор, а взять его было негде — куратор,
	вернувшийся назавтра, не мог найти собственный курс.

	Курсы общие, поэтому список полный, а не «мои». Фильтр `published`
	сужает до одного состояния.
	"""
	_автор()
	отбор = {}
	if published is not None:
		отбор["published"] = 1 if published in (True, 1, "1", "true") else 0
	курсы = frappe.get_all(
		"LMS Course",
		filters=отбор,
		fields=["name", "title", "short_introduction", "published", "upcoming", "modified"],
		order_by="modified desc",
	)
	# Число уроков — одним обходом на весь список: порядок глав и уроков,
	# собираемый на каждый курс отдельно, давал по пять запросов на строку.
	уроков = structure.уроков_в_курсах([курс.name for курс in курсы])
	return {
		"courses": [
			{
				"id": курс.name,
				"title": курс.title,
				"summary": курс.short_introduction,
				"published": bool(курс.published),
				"upcoming": bool(курс.published and курс.upcoming),
				"lessons_total": уроков.get(курс.name, 0),
				"updated_at": курс.modified.isoformat() if курс.modified else None,
			}
			for курс in курсы
		]
	}


@frappe.whitelist(methods=["POST"])
@контракт
def update_course(
	course: str,
	title: str | None = None,
	summary: str | None = None,
	description: str | None = None,
	promise: str | None = None,
) -> dict:
	"""Правит название, описания или обещание курса.

	`promise` — что человек получит к концу курса; звучит ученику на первом
	занятии по курсу. Пустая строка очищает (#238).
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	документ = frappe.get_doc("LMS Course", course)
	for поле, значение in (
		("title", title),
		("short_introduction", summary),
		("description", description),
		("course_promise", promise),
	):
		if значение is not None:
			документ.set(поле, значение)
	документ.save()
	return {
		"id": документ.name,
		"title": документ.title,
		"summary": документ.short_introduction,
		"promise": документ.get("course_promise") or None,
	}


@frappe.whitelist(methods=["POST"])
@контракт
def update_chapter(chapter: str, title: str) -> dict:
	"""Правит название главы."""
	_автор()
	_должен_существовать("Course Chapter", chapter, ГЛАВА_НЕ_НАЙДЕНА)
	документ = frappe.get_doc("Course Chapter", chapter)
	документ.title = title
	документ.save()
	return {"id": документ.name, "title": документ.title}


@frappe.whitelist(methods=["POST"])
@контракт
def add_chapter(course: str, title: str) -> dict:
	"""Добавляет главу в конец курса."""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	глава = frappe.get_doc({"doctype": "Course Chapter", "course": course, "title": title}).insert()
	structure.привязать(course, "LMS Course", глава.name)
	return {"id": глава.name, "title": глава.title, "course": course}


@frappe.whitelist(methods=["POST"])
@контракт
def add_lesson(chapter: str, title: str, body: str) -> dict:
	"""Добавляет урок в конец главы."""
	_автор()
	_должен_существовать("Course Chapter", chapter, ГЛАВА_НЕ_НАЙДЕНА)
	курс = frappe.db.get_value("Course Chapter", chapter, "course")
	урок = frappe.get_doc(
		{"doctype": "Course Lesson", "title": title, "body": body, "chapter": chapter, "course": курс}
	).insert()
	structure.привязать(chapter, "Course Chapter", урок.name)
	return {"id": урок.name, "title": урок.title, "chapter": chapter, "course": курс}


@frappe.whitelist(methods=["POST"])
@контракт
def update_lesson(
	lesson: str, title: str | None = None, body: str | None = None, lesson_hook: str | None = None
) -> dict:
	"""Правит название, материал или зачин урока.

	`lesson_hook` — зачем эта тема ученику сейчас, две-три фразы; звучит в
	начале непройденного урока. Пустая строка очищает (#238).
	"""
	_автор()
	_должен_существовать("Course Lesson", lesson, УРОК_НЕ_НАЙДЕН)
	документ = frappe.get_doc("Course Lesson", lesson)
	if title is not None:
		документ.title = title
	if body is not None:
		документ.body = body
	if lesson_hook is not None:
		документ.set("lesson_hook", lesson_hook)
	документ.save()
	return {"id": документ.name, "title": документ.title, "lesson_hook": документ.get("lesson_hook") or None}


@frappe.whitelist(methods=["POST"])
@контракт
def move_lesson(lesson: str, chapter: str | None = None, position: int | None = None) -> dict:
	"""Переносит урок в другую главу или на другое место в своей.

	`position` считается с единицы; без него урок встаёт в конец. Без
	`chapter` меняется только место внутри текущей главы.

	`Why:` без этого метода перестановка одного урока требовала передать
	порядок всей главы целиком, а перенос между главами был невозможен вовсе
	— ошибка в структуре чинилась пересборкой курса.
	"""
	_автор()
	_должен_существовать("Course Lesson", lesson, УРОК_НЕ_НАЙДЕН)
	откуда = frappe.db.get_value("Course Lesson", lesson, "chapter")
	куда = chapter or откуда
	_должен_существовать("Course Chapter", куда, ГЛАВА_НЕ_НАЙДЕНА)

	if куда != откуда:
		structure.отвязать(откуда, "Course Chapter", lesson)
		frappe.db.set_value("Course Lesson", lesson, "chapter", куда)
		frappe.db.set_value(
			"Course Lesson", lesson, "course", frappe.db.get_value("Course Chapter", куда, "course")
		)
		structure.привязать(куда, "Course Chapter", lesson)

	порядок = [урок for урок in structure.уроки_главы(куда) if урок != lesson]
	место = len(порядок) if position is None else max(0, min(int(position) - 1, len(порядок)))
	порядок.insert(место, lesson)
	structure.переставить(куда, "Course Chapter", порядок)

	return {"id": lesson, "chapter": куда, "lessons": порядок}


@frappe.whitelist(methods=["POST"])
@контракт
def remove_lesson(lesson: str) -> dict:
	"""Удаляет урок — пока по нему никто не занимался.

	`Why:` собирая курс впервые, агент создаёт лишние уроки, и без удаления
	они остаются в программе навсегда. Но урок, по которому есть прогресс или
	попытки, не удаляется ни при каких условиях: стирание испортило бы
	историю ученика, а курс от лишнего урока не рушится.

	Отвязать вместо удаления нельзя: чтение структуры намеренно подбирает
	уроки без строк-ссылок и показывает их в конце — иначе терялись бы курсы,
	собранные импортом. Отвязанный урок вернулся бы в программу.

	Требует роли `Moderator`: Frappe Learning не даёт `Course Creator` право
	удалять уроки, хотя главу, квиз и директиву — даёт. Асимметрия чужая, но
	обходить её через `ignore_permissions` нельзя: агент получил бы то, чего
	не может тот же человек в браузере.
	"""
	_автор()
	_должен_существовать("Course Lesson", lesson, УРОК_НЕ_НАЙДЕН)
	if следы := _следы_учеников(lesson):
		raise Отказ(
			УРОК_В_РАБОТЕ,
			"По этому уроку уже занимались: его можно только переписать",
			lesson=lesson,
			**следы,
		)

	глава = frappe.db.get_value("Course Lesson", lesson, "chapter")
	structure.отвязать(глава, "Course Chapter", lesson)
	if квиз := quiz._квиз_урока(lesson):
		frappe.delete_doc("LMS Quiz", квиз, ignore_permissions=True)
	for директива in frappe.get_all("Agent Lesson Directive", filters={"lesson": lesson}, pluck="name"):
		frappe.delete_doc("Agent Lesson Directive", директива, ignore_permissions=True)
	# Сдач по уроку нет — проверено следами выше, задание уходит вместе с уроком.
	if задание := _имя_задания(lesson):
		_снять_сроки_назначений(задание)
		frappe.delete_doc(homework.ЗАДАНИЕ, задание, ignore_permissions=True)
	frappe.delete_doc("Course Lesson", lesson)
	return {"removed": lesson, "chapter": глава, "lessons": structure.уроки_главы(глава)}


@frappe.whitelist(methods=["POST"])
@контракт
def remove_chapter(chapter: str) -> dict:
	"""Удаляет пустую главу.

	Непустая отклоняется: уроки удаляются поштучно и с проверкой прогресса,
	и обходить её каскадом нельзя.
	"""
	_автор()
	_должен_существовать("Course Chapter", chapter, ГЛАВА_НЕ_НАЙДЕНА)
	if уроки := structure.уроки_главы(chapter):
		raise Отказ(
			ГЛАВА_НЕ_ПУСТА,
			"В главе есть уроки: удалите их по одному",
			chapter=chapter,
			lessons=уроки,
		)

	курс = frappe.db.get_value("Course Chapter", chapter, "course")
	structure.отвязать(курс, "LMS Course", chapter)
	frappe.delete_doc("Course Chapter", chapter)
	return {"removed": chapter, "course": курс}


@frappe.whitelist(methods=["POST"])
@контракт
def reorder_lessons(chapter: str, lessons) -> dict:
	"""Задаёт порядок уроков главы полным списком."""
	_автор()
	_должен_существовать("Course Chapter", chapter, ГЛАВА_НЕ_НАЙДЕНА)
	порядок = список(lessons)
	structure.переставить(chapter, "Course Chapter", порядок)
	return {"chapter": chapter, "lessons": порядок}


@frappe.whitelist(methods=["POST"])
@контракт
def reorder_chapters(course: str, chapters) -> dict:
	"""Задаёт порядок глав курса полным списком."""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	порядок = список(chapters)
	structure.переставить(course, "LMS Course", порядок)
	return {"course": course, "chapters": порядок}


# --- директива и квиз ---


@frappe.whitelist(methods=["POST"])
@контракт
def set_directive(
	lesson: str,
	teaching_directive: str,
	objectives: str | None = None,
	probing_questions: str | None = None,
	common_misconceptions: str | None = None,
	success_criteria: str | None = None,
) -> dict:
	"""Задаёт директиву преподавателя новой версией.

	Цели — единственное поле директивы, которое видно снаружи, в том числе гостю.
	"""
	_автор()
	_должен_существовать("Course Lesson", lesson, УРОК_НЕ_НАЙДЕН)
	return directives.записать(
		"Agent Lesson Directive",
		{"lesson": lesson},
		{
			"objectives": objectives,
			"teaching_directive": teaching_directive,
			"probing_questions": probing_questions,
			"common_misconceptions": common_misconceptions,
			"success_criteria": success_criteria,
		},
	)


@frappe.whitelist(methods=["POST"])
@контракт
def set_course_directive(
	course: str,
	teaching_directive: str,
	objectives: str | None = None,
	student_profile: str | None = None,
	glossary: str | None = None,
	remember_about_student: str | None = None,
) -> dict:
	"""Задаёт сквозную директиву курса новой версией.

	Сюда идёт то, что одинаково на каждом уроке: роль и тон преподавателя,
	формат занятия, кого учим, как называть вещи. Агент ученика получает её
	вместе с директивой урока, поэтому повторять её в каждом уроке не нужно.

	`remember_about_student` — что в этом курсе стоит помнить об ученике
	между занятиями, по пункту на строку. Заметки агент ведёт сам; здесь
	задаётся, чему в них место.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	return directives.записать(
		"Agent Course Directive",
		{"course": course},
		{
			"objectives": objectives,
			"teaching_directive": teaching_directive,
			"student_profile": student_profile,
			"glossary": glossary,
			"remember_about_student": remember_about_student,
		},
	)


@frappe.whitelist(methods=["POST"])
@контракт
def set_course_artifact(
	course: str,
	artifact: str,
	title: str,
	blocks,
	layout: str = "sections",
	canvas=None,
	purpose: str | None = None,
) -> dict:
	"""Задаёт схему документа курса новой версией.

	`blocks` — список `{key, title, hint, lesson, span, kind, accept, spec}` в том
	порядке, в каком документ читается. Порядок задаётся здесь и нигде больше: ученик
	видит блоки в нём же. Подсказка `hint` адресована агенту: что должно
	оказаться в блоке и когда считать его заполненным.

	Версионируется как директива: содержимое ученика хранится по ключам
	блоков, и правка схемы его не рушит.

	`spec` — поля блока и его колонки в таблице документа (#330):
	`{fields, table, columns, prefix, title, rows, views}`. Форма проверяется
	здесь, до записи: схема, которую не прочесть, сломала бы документ каждого
	ученика курса, а не одного.

	`canvas` — холст документа (learning-services#351): `{grid, labels,
	sketch, summary}`, сетка из ключей блоков. Проверяется так же до записи:
	сетку, которую браузер не разложит, увидел бы каждый ученик.

	Схема целиком отвязывает документ от шаблона: новая версия не хранит ни
	шаблона, ни правок (learning-services#370).

	`purpose` — одна-две фразы ученику: зачем этот документ. Ученик видит их
	в «Моих документах» под названием (learning-services#462). Не назван —
	остаётся прежний; пустая строка убирает.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	версия = записать_схему(course, artifact, title, список(blocks), layout, canvas, purpose=purpose)
	# Наружу ключ документа зовётся `artifact`, как в методах ученика.
	return {"id": версия["id"], "course": course, "artifact": версия["slug"], "version": версия["version"]}


@frappe.whitelist(methods=["POST"])
@контракт
def set_artifact_template(
	template: str,
	title: str,
	blocks=None,
	layout: str = "sections",
	canvas=None,
	note: str | None = None,
	extends: str | None = None,
	extends_version: int | None = None,
	overlay=None,
	renamed=None,
	description: str | None = None,
) -> dict:
	"""Заводит новую версию шаблона документа (learning-services#370).

	Шаблон — схема документа без уроков, общая для курсов: `blocks` и
	`canvas` — как у `set_course_artifact`, но урока у блока нет — урок
	принадлежит курсу и задаётся в правках привязки. Каждый вызов — новая
	версия; прежние не меняются, и курсы, закрепившие их, их и сохраняют.
	`note` — что поменялось в версии: по нему автор курса решает, переходить ли.

	Наследник (learning-services#375): `extends` — ключ родителя,
	`extends_version` — его версия (без неё последняя), `overlay` — правки к
	родителю в формате правок курса, без уроков. Своих `blocks` и `canvas` у
	наследника нет, раскладка — родителя или из `overlay.layout`: `layout`
	наследника не читается. `Why:` MCP шлёт `layout` всегда, и наследник
	шаблона-холста молча стал бы столбцом.

	`renamed` (learning-services#376) — какие ключи прошлой версии этого
	шаблона стали какими: `{blocks, fields, tables, columns: {таблица: …}}`.
	По нему `upgrade_course_artifact` переносит правки курса и данные
	учеников; без него новое имя — удалённое старое и новое пустое.

	`description` (learning-services#387) — для какого документа шаблон, у
	наследника — чем он отличается от базы; у версии своё, от родителя не
	наследуется. `Why:` агент выбирает шаблон из перечня, и по названию и
	заметке к версии «реестр рисков» и «реестр рисков стройки» не различить.
	"""
	_автор()
	return templates.записать_шаблон(
		template,
		title,
		список(blocks),
		layout,
		canvas,
		note,
		extends=extends,
		extends_version=extends_version,
		правки=overlay,
		renamed=renamed,
		description=description,
	)


@frappe.whitelist()
@контракт
def list_artifact_templates() -> dict:
	"""Шаблоны документов: последняя версия каждого с описанием и курсы на нём.

	У курса — версия шаблона, которую он закрепил: новая версия шаблона
	живые курсы не меняет, и автор видит, кто на какой остался.
	"""
	_автор()
	return {"templates": templates.шаблоны()}


@frappe.whitelist()
@контракт
def artifact_template(template: str, version: int | None = None) -> dict:
	"""Версия шаблона целиком — последняя, если номер не назван.

	У наследника — собранная схема, родитель с версией и правки к нему.
	"""
	_автор()
	return templates.шаблон(template, version)


@frappe.whitelist(methods=["POST"])
@контракт
def set_course_artifact_template(
	course: str,
	artifact: str,
	template: str,
	version: int | None = None,
	overlay=None,
	purpose: str | None = None,
) -> dict:
	"""Документ курса из шаблона с правками курса — новой версией схемы.

	`version` не назван — последняя версия шаблона. `overlay` — чем документ
	курса отличается от шаблона: уроки блоков, подсказки, варианты, лишний
	блок, подписи холста; по ключам, а не по позициям. Собранная схема
	проверяется целиком, как у `set_course_artifact`, и пишется тем же путём:
	ученик видит обычную схему документа.

	`purpose` — как у `set_course_artifact`: зачем документ ученику; не назван —
	остаётся прежний. Он у курса, а не у шаблона: один и тот же реестр рисков
	нужен в разных курсах для разного.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	return templates.привязать(course, artifact, template, version, overlay, purpose)


@frappe.whitelist(methods=["POST"])
@контракт
def upgrade_course_artifact(
	course: str, artifact: str, version: int | None = None, dry_run: bool = False
) -> dict:
	"""Документ курса — на новую версию своего шаблона (learning-services#376).

	`version` не назван — последняя. Правки курса переносятся на ключи новой
	версии по её `renamed`, схема собирается заново и проверяется целиком;
	данные учеников переносятся на новые ключи в той же транзакции. Отвечает
	разницей схем: что автор должен проверить, прежде чем публиковать.
	Назад — не переход: откат — `set_course_artifact_template` с версией.

	`dry_run` (learning-services#383) — та же разница и число документов
	учеников, которые поменялись бы, без записи: переход необратим, и
	куратору показывают разницу до него. Отказы — те же.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	return templates.перейти(course, artifact, version, dry_run=dry_run in (True, 1, "1", "true"))


@frappe.whitelist(methods=["POST"])
@контракт
def add_quiz(lesson: str, questions, title: str | None = None, passing_percentage: int = 70) -> dict:
	"""Создаёт квиз урока со всеми вопросами.

	Квиз заводится целиком одним вызовом: квиз без вопросов — состояние, в
	котором ученик упирается в зачёт из ничего, и оставлять его достижимым
	между двумя вызовами незачем.
	"""
	_автор()
	_должен_существовать("Course Lesson", lesson, УРОК_НЕ_НАЙДЕН)
	вопросы = список(questions)
	if not вопросы:
		raise Отказ(course_builder.КВИЗ_БЕЗ_ВОПРОСОВ, "Квизу нужен хотя бы один вопрос", lesson=lesson)

	# Второй квиз на уроке — это не «ещё один квиз», а потерянный первый:
	# урок отдаёт агенту ровно один, остальные становятся невидимым мусором.
	# Правится вопросами существующего квиза, а не созданием нового.
	if существующий := quiz._квиз_урока(lesson):
		raise Отказ(
			КВИЗ_УЖЕ_ЕСТЬ,
			"У урока уже есть квиз: правьте его вопросы",
			lesson=lesson,
			quiz=существующий,
		)

	квиз = frappe.get_doc(
		{
			"doctype": "LMS Quiz",
			"title": title or frappe.db.get_value("Course Lesson", lesson, "title"),
			"lesson": lesson,
			"course": frappe.db.get_value("Course Lesson", lesson, "course"),
			"passing_percentage": passing_percentage,
		}
	)
	созданы = []
	# Точка сохранения, а не откат всей транзакции: сбойный вопрос обязан
	# отменить только уже созданные вопросы этого квиза. `frappe.db.rollback()`
	# без неё сносил и курс, и главы, созданные тем же процессом, — поймано
	# на живом прогоне сборки.
	frappe.db.savepoint("agent_quiz_build")
	for номер, вопрос in enumerate(вопросы, start=1):
		# Словарём, как в `add_question`: список вопросов приезжает строкой
		# JSON, и его элементы разбираются вместе с ним не всегда — вложенная
		# строка роняла бы вызов на `.get` мимо контракта.
		вопрос = _как_словарь(вопрос)
		try:
			идентификатор, тип = course_builder.создать_вопрос(вопрос)
		except Отказ:
			raise
		except frappe.ValidationError as причина:
			# Проверку состава вариантов делает сам Frappe Learning, и её текст
			# полезен — но агенту нужен машинный код, а не HTTP 500.
			frappe.db.rollback(save_point="agent_quiz_build")
			raise Отказ(НЕВЕРНЫЙ_ВОПРОС, str(причина), lesson=lesson, question_index=номер) from причина
		квиз.append("questions", {"question": идентификатор, "type": тип, "marks": вопрос.get("marks") or 1})
		созданы.append(идентификатор)
	квиз.insert()

	# Привязка с обеих сторон: Frappe Learning допускает обе, а урок,
	# связанный только полем квиза, в интерфейсе выглядит без квиза.
	frappe.db.set_value("Course Lesson", lesson, "quiz_id", квиз.name)
	return {"id": квиз.name, "lesson": lesson, "questions": созданы}


@frappe.whitelist(methods=["POST"])
@контракт
def add_question(lesson: str, question: dict | str) -> dict:
	"""Добавляет вопрос в существующий квиз урока."""
	_автор()
	квиз = _квиз_урока_или_отказ(lesson)
	вопрос = _как_словарь(question)
	идентификатор, тип = _создать_вопрос_или_отказ(вопрос, lesson)
	документ = frappe.get_doc("LMS Quiz", квиз)
	документ.append(
		"questions",
		{"question": идентификатор, "type": тип, "marks": вопрос.get("marks") or 1},
	)
	документ.save()
	return {"quiz": квиз, "question": идентификатор, "questions_total": len(документ.questions)}


@frappe.whitelist(methods=["POST"])
@контракт
def update_question(question: str, text: str | None = None, options=None, answers=None) -> dict:
	"""Правит текст вопроса, варианты или образцы ответа.

	Правка разрешена и после того, как по квизу отвечали: блокировать
	исправление опечатки в опубликованном курсе хуже, чем оставить её. Но
	число затронутых попыток возвращается — куратор должен знать, что меняет
	вопрос, который кто-то уже видел.
	"""
	_автор()
	_должен_существовать("LMS Question", question, ВОПРОС_НЕ_НАЙДЕН)
	документ = frappe.get_doc("LMS Question", question)

	if text is not None:
		документ.question = text
	if options is not None:
		course_builder.заменить_варианты(документ, список(options))
	if answers is not None:
		course_builder.заменить_образцы(документ, список(answers))

	frappe.db.savepoint("agent_question_edit")
	try:
		документ.save()
	except Отказ:
		raise
	except frappe.ValidationError as причина:
		# Правка, ломающая состав вариантов, отменяется целиком: иначе вопрос
		# остался бы наполовину переписанным.
		frappe.db.rollback(save_point="agent_question_edit")
		raise Отказ(НЕВЕРНЫЙ_ВОПРОС, str(причина), question=question) from причина

	return {
		"id": question,
		"text": документ.question,
		"affects_attempts": frappe.db.count("Agent Quiz Answer", {"question": question}),
	}


@frappe.whitelist(methods=["POST"])
@контракт
def remove_question(lesson: str, question: str) -> dict:
	"""Убирает вопрос из квиза урока.

	Сам вопрос не удаляется: на него ссылаются ответы прошлых попыток, и
	стирание записи испортило бы историю зачётов.
	"""
	_автор()
	квиз = _квиз_урока_или_отказ(lesson)
	документ = frappe.get_doc("LMS Quiz", квиз)
	осталось = [строка for строка in документ.questions if строка.question != question]
	if len(осталось) == len(документ.questions):
		raise Отказ(ВОПРОС_НЕ_НАЙДЕН, "В этом квизе такого вопроса нет", quiz=квиз, question=question)

	документ.questions = []
	for строка in осталось:
		документ.append("questions", {"question": строка.question, "type": строка.type, "marks": строка.marks})
	документ.save()
	return {"quiz": квиз, "questions_total": len(документ.questions)}


# --- домашнее задание (learning-services#439) ---

ЗАДАНИЕ_УЖЕ_ЕСТЬ = "homework_exists"
ЗАДАНИЯ_У_УРОКА_НЕТ = "homework_missing"
ЗАДАНИЕ_СДАЮТ = "homework_in_use"
ПОЛЯ_ЗАДАНИЯ_АВТОРА = ("title", "description", "answer_mode", "due_mode", "due_days", "due_date")


def _задание_автора(документ) -> dict:
	return {"id": документ.name, "lesson": документ.lesson, **{п: документ.get(п) for п in ПОЛЯ_ЗАДАНИЯ_АВТОРА}}


def _имя_задания(lesson: str) -> str | None:
	return frappe.db.get_value(homework.ЗАДАНИЕ, {"lesson": lesson})


def _снять_сроки_назначений(задание: str) -> None:
	"""Сроки задания в назначениях курса уходят вместе с заданием (learning-services#452).

	`Why:` строка назначения ссылается на задание, и без уборки удаление
	упало бы `LinkExistsError`. Срок назначения — не след ученика: сдач по
	заданию нет, выставлять его больше некому.
	"""
	frappe.db.delete("Course Allocation Homework Due", {"homework": задание})


def _задание_урока(lesson: str):
	if имя := _имя_задания(lesson):
		return frappe.get_doc(homework.ЗАДАНИЕ, имя)
	raise Отказ(ЗАДАНИЯ_У_УРОКА_НЕТ, "У урока нет домашнего задания", lesson=lesson)


@frappe.whitelist(methods=["POST"])
@контракт
def add_homework(
	lesson: str,
	title: str,
	description: str,
	answer_mode: str = "text_and_files",
	due_mode: str = "none",
	due_days: int | None = None,
	due_date: str | None = None,
) -> dict:
	"""Домашнее задание урока: одно на урок, как квиз.

	Задание одинаковое у всех учеников, выдаётся закрытием урока. Необязательное:
	`publish_course` его не проверяет.
	"""
	_автор()
	_должен_существовать("Course Lesson", lesson, УРОК_НЕ_НАЙДЕН)
	if имя := _имя_задания(lesson):
		raise Отказ(ЗАДАНИЕ_УЖЕ_ЕСТЬ, "У урока уже есть домашнее задание: правьте его", lesson=lesson, homework=имя)
	документ = frappe.get_doc(
		{
			"doctype": homework.ЗАДАНИЕ,
			"lesson": lesson,
			"title": title,
			"description": description,
			"answer_mode": answer_mode,
			"due_mode": due_mode,
			"due_days": due_days,
			"due_date": due_date,
		}
	).insert()
	return _задание_автора(документ)


@frappe.whitelist(methods=["POST"])
@контракт
def update_homework(
	lesson: str,
	title: str | None = None,
	description: str | None = None,
	answer_mode: str | None = None,
	due_mode: str | None = None,
	due_days: int | None = None,
	due_date: str | None = None,
) -> dict:
	"""Правка задания. Пустое не затирает.

	Сроки, уже выставленные сдачам, не пересчитываются: ученик не получает
	просрочку задним числом за правку правила.
	"""
	_автор()
	документ = _задание_урока(lesson)
	for поле, значение in (
		("title", title),
		("description", description),
		("answer_mode", answer_mode),
		("due_mode", due_mode),
		("due_days", due_days),
		("due_date", due_date),
	):
		if значение not in (None, ""):
			документ.set(поле, значение)
	документ.save()
	return _задание_автора(документ)


@frappe.whitelist(methods=["POST"])
@контракт
def remove_homework(lesson: str) -> dict:
	"""Удаляет задание, по которому ещё никто не сдавал.

	Сдачи в счёт — и архивные после сброса прогресса: они ссылаются на задание.
	"""
	_автор()
	документ = _задание_урока(lesson)
	if сдач := frappe.db.count("Agent Homework Submission", {"homework": документ.name}):
		raise Отказ(
			ЗАДАНИЕ_СДАЮТ, "По заданию уже есть сдачи: его можно только переписать", lesson=lesson, submissions=сдач
		)
	_снять_сроки_назначений(документ.name)
	frappe.delete_doc(homework.ЗАДАНИЕ, документ.name)
	# Why: удаление записи не двигает ничьего `modified`, и `ревизия` не заметила
	# бы, что задания больше нет, — зеркало автора осталось бы старым. Отметка
	# `modified`, а не `save()` урока: сохранение гоняло бы проверки и хуки
	# Frappe Learning ради одной метки времени.
	frappe.db.set_value("Course Lesson", lesson, "modified", now_datetime())
	return {"lesson": lesson, "removed": True}


# --- карта декомпозиции ---

#: Части карты: хранятся полями JSON и отдаются как есть.
ЧАСТИ_КАРТЫ = ("levels", "nodes", "lessons", "blocks")


@frappe.whitelist(methods=["POST"])
@контракт
def set_course_map(course: str, levels, nodes, lessons=None, blocks=None) -> dict:
	"""Новая версия карты декомпозиции курса — замысла, с которым сверяется
	собранное.

	Уровни слева направо, узлы с родителями на соседнем левом уровне, план
	уроков и план блоков документа; устройство — в `course_map`. С курсом
	карта при записи не сверяется: её согласуют до сборки. Проверить её
	против курса — `course_map_check`; в ответе — счётчики этой проверки.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	карта = course_map.нормализовать(список(levels), список(nodes), список(lessons), список(blocks))
	уроки_курса = set(frappe.get_all("Course Lesson", filters={"course": course}, pluck="name"))
	for пункт in карта["lessons"]:
		if пункт["lesson"] and пункт["lesson"] not in уроки_курса:
			raise Отказ(
				course_map.НЕВЕРНАЯ_КАРТА,
				"Урок не из этого курса",
				where=f"lessons[{пункт['key']}].lesson",
			)
	документ = frappe.get_doc(
		{
			"doctype": "Agent Course Map",
			"course": course,
			"is_active": 1,
			**{часть: json.dumps(карта[часть], ensure_ascii=False) for часть in ЧАСТИ_КАРТЫ},
		}
	).insert()
	return {
		"id": документ.name,
		"course": course,
		"version": документ.version,
		"counts": _сверка_карты(course)["counts"],
	}


@frappe.whitelist()
@контракт
def course_map_check(course: str) -> dict:
	"""Действующая карта против курса на платформе.

	Уроки и блоки — такими, какие они есть, сопоставление пунктов плана с
	уроками и расхождения по группам: план уроков, цели уроков, блоки
	документа, целостность карты. Карты нет — `map: null`, это не ошибка:
	карта необязательна.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	return _сверка_карты(course)


def _сверка_карты(course: str) -> dict:
	запись = directives.запись("Agent Course Map", {"course": course}, (*ЧАСТИ_КАРТЫ, "creation"))
	уроки = _уроки_для_карты(course)
	блоки = [
		{
			"artifact": документ["artifact"],
			"key": блок["key"],
			"title": блок["title"],
			"lesson": блок["lesson"],
			"hint": блок["hint"],
		}
		for документ in _действующие_артефакты(course)
		for блок in документ["blocks"]
	]
	платформа = {"lessons": уроки, "blocks": блоки}
	if not запись:
		return {
			"course": course,
			"map": None,
			"platform": платформа,
			"matches": {},
			"discrepancies": [],
			"counts": None,
			"tags": {},
		}
	карта = {часть: json.loads(запись.get(часть) or "[]") for часть in ЧАСТИ_КАРТЫ}
	return {
		"course": course,
		"map": {
			"id": запись.name,
			"version": запись.version,
			"created_at": запись.creation.isoformat(),
			**карта,
		},
		"platform": платформа,
		**course_map.сверить(карта, уроки, блоки),
	}


def _уроки_для_карты(course: str) -> list[dict]:
	"""Уроки курса в порядке курса, с главой и целями действующей директивы."""
	уроки = []
	for глава in structure.главы_курса(course):
		for урок in structure.уроки_главы(глава["name"]):
			директива = directives.запись("Agent Lesson Directive", {"lesson": урок}, ("objectives",))
			уроки.append(
				{
					"id": урок,
					"number": len(уроки) + 1,
					"title": frappe.db.get_value("Course Lesson", урок, "title"),
					"chapter": глава["title"],
					"objectives": directives.строки(директива.objectives) if директива else [],
				}
			)
	return уроки


# --- замечания автора ---

ЗАМЕЧАНИЕ_НЕ_НАЙДЕНО = "note_not_found"
НЕВЕРНОЕ_ЗАМЕЧАНИЕ = "invalid_note"


@frappe.whitelist(methods=["POST"])
@контракт
def add_note(
	course: str,
	target: str,
	text: str,
	quote: str | None = None,
	lesson: str | None = None,
	via: str = "author",
) -> dict:
	"""Замечание на месте курса — чтобы петля «увидел → агент поправил →
	принял» не шла через пересказ в чате.

	`target` — место: `course`, `course_directive.<поле>`, `lesson`,
	`material`, `directive.<поле>`, `question.<id>`, `block.<документ>/<ключ>`,
	`map.<узел>`; месту внутри урока нужен `lesson`. `quote` — выделенный
	текст. `via` — кто пишет: `author` из кабинета, `agent` — агент куратора
	через MCP; замечание агента — вопрос автору.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	lesson = lesson or None
	адрес = notes.разобрать_адрес(target, lesson, ПОЛЯ_ДИРЕКТИВЫ, ПОЛЯ_ДИРЕКТИВЫ_КУРСА)
	_проверить_место_замечания(course, lesson, адрес)
	_проверить_источник(via)
	место = адрес["kind"] + (f".{адрес['key']}" if адрес["key"] else "")
	документ = frappe.get_doc(
		{
			"doctype": "Agent Author Note",
			"course": course,
			"lesson": lesson,
			"target": место,
			"status": "open",
			"via": via,
			"quote": (quote or "").strip(),
			"text": _текст_замечания(text),
			# Место, каким его видел человек: «как было» для разницы, когда
			# агент отметит «сделано» (#271).
			"baseline": snapshots.в_json(snapshots.снимок(course, lesson, место)),
		}
	).insert()
	return {
		"id": документ.name,
		"course": course,
		"lesson": lesson,
		"target": документ.target,
		"status": документ.status,
		"waiting_on": notes.ждёт(документ.status, via, []),
	}


@frappe.whitelist()
@контракт
def list_notes(course: str, status: str | None = None, lesson: str | None = None) -> dict:
	"""Замечания курса с нитью ответов, старые сверху.

	Место расшифровано подписью — «Урок 4 «Ответ и мера» · директива ·
	teaching_directive»; `missing` — места больше нет: урок удалён, вопрос
	убран, блока нет в схеме, узла — в карте. `waiting_on` — чей ход.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	фильтры = {"course": course}
	if status:
		if status not in notes.СТАТУСЫ:
			raise Отказ(НЕВЕРНОЕ_ЗАМЕЧАНИЕ, "Статус: open, done или accepted", where="status")
		фильтры["status"] = status
	if lesson:
		фильтры["lesson"] = lesson
	return {"course": course, "notes": _замечания(course, фильтры)}


@frappe.whitelist(methods=["POST"])
@контракт
def reply_note(note: str, text: str, via: str = "author") -> dict:
	"""Ответ в нить замечания; статус не меняется. Вопрос агента — тоже
	ответ: ход переходит к автору."""
	_автор()
	документ = _замечание_или_отказ(note)
	_проверить_источник(via)
	_ответить(документ, _текст_замечания(text), via)
	документ.save()
	return _состояние_замечания(документ)


@frappe.whitelist(methods=["POST"])
@контракт
def set_note_status(note: str, status: str, text: str | None = None, via: str = "author") -> dict:
	"""Сменить статус замечания.

	Агент отмечает `done` и пишет в `text`, что поменял. Автор принимает —
	`accepted` — или возвращает в `open` с ответом, что не так; принять можно
	и открытое — снять своё замечание. Прочее — `invalid_transition`.
	"""
	_автор()
	документ = _замечание_или_отказ(note)
	notes.проверить_переход(документ.status, status, via, text)
	if (text or "").strip():
		_ответить(документ, text.strip(), via)
	if status == "open":
		# Вернули — следующее «сделано» сравнивается с тем, что человек видел,
		# когда возвращал: исходная правка уже проверена.
		документ.baseline = snapshots.в_json(snapshots.снимок(документ.course, документ.lesson, документ.target))
	документ.status = status
	документ.save()
	return _состояние_замечания(документ)


def _проверить_место_замечания(course: str, lesson: str | None, адрес: dict) -> None:
	if lesson and frappe.db.get_value("Course Lesson", lesson, "course") != course:
		raise Отказ(notes.НЕВЕРНЫЙ_АДРЕС, "Урок не из этого курса", where="lesson")
	if адрес["kind"] == "question" and адрес["key"] not in _вопросы_урока(lesson):
		raise Отказ(notes.НЕВЕРНЫЙ_АДРЕС, "Такого вопроса в квизе урока нет", where="target")


def _вопросы_урока(lesson: str | None) -> list[str]:
	квиз = quiz._квиз_урока(lesson) if lesson else None
	return frappe.get_all("LMS Quiz Question", filters={"parent": квиз}, pluck="question") if квиз else []


def _проверить_источник(via: str) -> None:
	if via not in notes.ИСТОЧНИКИ:
		raise Отказ(НЕВЕРНОЕ_ЗАМЕЧАНИЕ, "via: author или agent", where="via")


def _текст_замечания(text: str | None) -> str:
	текст = (text or "").strip()
	if not текст:
		raise Отказ(НЕВЕРНОЕ_ЗАМЕЧАНИЕ, "Текст пуст", where="text")
	return текст


def _ответить(документ, текст: str, via: str) -> None:
	документ.append(
		"replies",
		{"via": via, "author": frappe.session.user, "created_at": frappe.utils.now_datetime(), "text": текст},
	)


def _замечание_или_отказ(note: str):
	if not frappe.db.exists("Agent Author Note", note):
		raise Отказ(ЗАМЕЧАНИЕ_НЕ_НАЙДЕНО, "Замечания нет", id=note)
	return frappe.get_doc("Agent Author Note", note)


def _состояние_замечания(документ) -> dict:
	ответы = [{"via": ответ.via} for ответ in документ.replies]
	return {
		"id": документ.name,
		"status": документ.status,
		"waiting_on": notes.ждёт(документ.status, документ.via, ответы),
		"replies": len(ответы),
	}


def _замечания(course: str, фильтры: dict) -> list[dict]:
	записи = frappe.get_all(
		"Agent Author Note",
		filters=фильтры,
		fields=["name", "lesson", "target", "quote", "text", "via", "status", "owner", "creation", "modified"],
		order_by="creation asc",
	)
	if not записи:
		return []
	нити: dict[str, list] = {}
	for ответ in frappe.get_all(
		"Agent Note Reply",
		filters={"parent": ["in", [запись.name for запись in записи]], "parenttype": "Agent Author Note"},
		fields=["parent", "via", "author", "text", "created_at"],
		order_by="idx asc",
	):
		нити.setdefault(ответ.parent, []).append(
			{
				"via": ответ.via,
				"author": ответ.author,
				"author_name": frappe.utils.get_fullname(ответ.author) if ответ.author else "",
				"text": ответ.text,
				"created_at": ответ.created_at.isoformat() if ответ.created_at else None,
			}
		)
	подпись = _подписи_мест(course)
	собранное = []
	for запись in записи:
		нить = нити.get(запись.name, [])
		текст_места, нет_места = подпись(запись.target, запись.lesson)
		собранное.append(
			{
				"id": запись.name,
				"lesson": запись.lesson,
				"target": запись.target,
				"label": текст_места,
				"missing": нет_места,
				"quote": запись.quote or "",
				"text": запись.text,
				"via": запись.via,
				"author": запись.owner,
				"author_name": frappe.utils.get_fullname(запись.owner),
				"status": запись.status,
				"waiting_on": notes.ждёт(запись.status, запись.via, нить),
				"created_at": запись.creation.isoformat(),
				"updated_at": запись.modified.isoformat(),
				"replies": нить,
			}
		)
	return собранное


def _подписи_мест(course: str):
	"""Подпись места замечания по курсу, каким он есть сейчас, и признак,
	что места больше нет."""
	уроки = {}
	for глава in structure.главы_курса(course):
		for урок in structure.уроки_главы(глава["name"]):
			уроки[урок] = (len(уроки) + 1, frappe.db.get_value("Course Lesson", урок, "title"))
	блоки = {
		f"{документ['artifact']}/{блок['key']}": блок["title"]
		for документ in _действующие_артефакты(course)
		for блок in документ["blocks"]
	}
	карта = directives.запись("Agent Course Map", {"course": course}, ("nodes",))
	узлы = {узел["id"]: узел["text"] for узел in json.loads(карта.nodes or "[]")} if карта else {}

	def подпись(target: str, lesson: str | None) -> tuple[str, bool]:
		вид, _, ключ = target.partition(".")
		урок, нет = "", False
		if lesson:
			if lesson in уроки:
				номер, название = уроки[lesson]
				урок = f"Урок {номер} «{название}»"
			else:
				урок, нет = f"Урок «{lesson}» (удалён)", True
		if вид == "course":
			return "Курс", False
		if вид == "course_directive":
			return f"Сквозная директива · {ключ}", False
		if вид == "lesson":
			return урок, нет
		if вид == "material":
			return f"{урок} · материал", нет
		if вид == "directive":
			return f"{урок} · директива · {ключ}", нет
		if вид == "question":
			if ключ in _вопросы_урока(lesson):
				return f"{урок} · вопрос «{_коротко(frappe.db.get_value('LMS Question', ключ, 'question'), 80)}»", нет
			return f"{урок} · вопрос {ключ}", True
		if вид == "block":
			документ, _, блок = ключ.partition("/")
			название = блоки.get(ключ)
			основа = f"Документ {документ} · блок «{название or блок}»"
			return (f"{основа} · {урок}" if урок else основа), нет or название is None
		if вид == "map":
			текст = узлы.get(ключ)
			return (f"Карта · «{_коротко(текст, 60)}»", нет) if текст else (f"Карта · узел {ключ}", True)
		return target, True

	return подпись


def _коротко(текст: str | None, предел: int) -> str:
	текст = " ".join((текст or "").split())
	return текст if len(текст) <= предел else текст[: предел - 1].rstrip() + "…"


def _ждут_агента(course: str) -> int:
	"""Сколько открытых замечаний ждёт агента: последнее слово за автором."""
	открытые = frappe.get_all(
		"Agent Author Note", filters={"course": course, "status": "open"}, fields=["name", "via"]
	)
	if not открытые:
		return 0
	последние: dict[str, str] = {}
	for ответ in frappe.get_all(
		"Agent Note Reply",
		filters={"parent": ["in", [з.name for з in открытые]], "parenttype": "Agent Author Note"},
		fields=["parent", "via"],
		order_by="idx asc",
	):
		последние[ответ.parent] = ответ.via
	return sum(1 for з in открытые if последние.get(з.name, з.via) != "agent")


def ревизия_замечаний(course: str) -> str | None:
	"""Самая свежая отметка замечаний курса: ответ в нить и смена статуса
	сохраняют замечание и двигают её."""
	отметки = frappe.get_all(
		"Agent Author Note", filters={"course": course}, pluck="modified", order_by="modified desc", limit=1
	)
	return отметки[0].isoformat() if отметки else None


# --- обзор и публикация ---


@frappe.whitelist()
@контракт
def course_draft(course: str) -> dict:
	"""Курс целиком, как его собрали, — с эталонами и проблемами.

	Эталоны видит роль, а не эндпоинт: без них куратор не проверит
	собственный квиз. Ученику они не достаются ни здесь, ни где-либо ещё —
	`_автор` отклонит вызов до всякого чтения.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	сведения = frappe.db.get_value(
		"LMS Course", course, ["title", "short_introduction", "published", "upcoming"], as_dict=True
	)
	предел = normalizer.предел_сегмента()
	return {
		"id": course,
		"title": сведения.title,
		"summary": сведения.short_introduction,
		"published": bool(сведения.published),
		"upcoming": bool(сведения.published and сведения.upcoming),
		"chapters": [
			{"id": глава["name"], "title": глава["title"], "lessons": _уроки_главы(глава["name"], предел)}
			for глава in structure.главы_курса(course)
		],
		"directive": _действующая_директива_курса(course),
		"artifacts": _действующие_артефакты(course),
		"readiness": course_builder.проверить_готовность(course),
		"revision": ревизия(course),
		"author_url": frappe.utils.get_url(f"/author?course={quote(course)}"),
		"map_discrepancies": (_сверка_карты(course)["counts"] or {}).get("total"),
		"open_notes": _ждут_агента(course),
	}


@frappe.whitelist(methods=["GET"])
@контракт
def course_revision(course: str) -> dict:
	"""Отметка последнего изменения курса.

	Для опроса зеркалом автора (#261): страница спрашивает её раз в
	несколько секунд, и перечитывать ради этого курс целиком незачем.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	return {"course": course, "revision": ревизия(course), "notes_revision": ревизия_замечаний(course)}


def ревизия(course: str) -> str:
	"""Самая свежая отметка изменения всего, из чего собран курс.

	Удаление урока или вопроса тоже двигает её: убирая строку, сохраняется
	глава или квиз, в которых она стояла.
	"""
	уроки = frappe.get_all("Course Lesson", filters={"course": course}, pluck="name")
	квизы = set(frappe.get_all("LMS Quiz", filters={"lesson": ["in", уроки]}, pluck="name")) if уроки else set()
	квизы |= set(
		frappe.get_all(
			"Course Lesson", filters={"course": course, "quiz_id": ["is", "set"]}, pluck="quiz_id"
		)
	)
	вопросы = (
		frappe.get_all("LMS Quiz Question", filters={"parent": ["in", list(квизы)]}, pluck="question")
		if квизы
		else []
	)
	источники = [
		("LMS Course", {"name": course}),
		("Course Chapter", {"course": course}),
		("Course Lesson", {"course": course}),
		("Agent Course Directive", {"course": course}),
		("Agent Course Artifact", {"course": course}),
		("Agent Course Map", {"course": course}),
	]
	if квизы:
		источники.append(("LMS Quiz", {"name": ["in", list(квизы)]}))
	if вопросы:
		источники.append(("LMS Question", {"name": ["in", вопросы]}))
	if уроки:
		источники.append(("Agent Lesson Directive", {"lesson": ["in", уроки]}))
		источники.append((homework.ЗАДАНИЕ, {"lesson": ["in", уроки]}))
	отметки = [
		отметка
		for doctype, фильтры in источники
		for отметка in frappe.get_all(
			doctype, filters=фильтры, pluck="modified", order_by="modified desc", limit=1
		)
	]
	return max(отметки).isoformat()


@frappe.whitelist()
@контракт
def get_lesson(lesson: str) -> dict:
	"""Урок целиком: материал, действующая директива и квиз с эталонами.

	`Why:` материал попадает на платформу вызовом `add_lesson`, а `course_draft`
	показывает лишь признак `has_body` — сверить, что на платформе лежит ровно
	утверждённый текст, было нечем, кроме как открыть урок глазами на сайте.

	Отдельный инструмент, а не поле черновика: полные тексты всех уроков в
	одном ответе — десятки килобайт на каждый вызов, а сверяют поурочно.
	"""
	_автор()
	_должен_существовать("Course Lesson", lesson, УРОК_НЕ_НАЙДЕН)
	сведения = frappe.db.get_value(
		"Course Lesson", lesson, ["title", "body", "chapter", "course"], as_dict=True
	)
	квиз = quiz._квиз_урока(lesson)
	return {
		"id": lesson,
		"title": сведения.title,
		"chapter": сведения.chapter,
		"course": сведения.course,
		"body": сведения.body,
		"directive": _действующая_директива(lesson),
		"course_directive": _действующая_директива_курса(сведения.course),
		"quiz": _вопросы_с_эталонами(квиз) if квиз else None,
		"homework": _задание_автора(frappe.get_doc(homework.ЗАДАНИЕ, задание))
		if (задание := _имя_задания(lesson))
		else None,
	}


@frappe.whitelist(methods=["POST"])
@контракт
def publish_course(course: str) -> dict:
	"""Открывает курс ученикам, если он к этому готов."""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	готовность = course_builder.проверить_готовность(course)
	if готовность["blocking"]:
		raise Отказ(
			КУРС_НЕ_ГОТОВ,
			"Курс не готов к публикации",
			course=course,
			problems=готовность["blocking"],
		)
	# Анонс выходит той же публикацией: флаг снимается, и тем, кто просил
	# сообщить о выходе, уходит письмо (learning-services#389).
	frappe.db.set_value("LMS Course", course, {"published": 1, "upcoming": 0})
	return {
		"id": course,
		"published": True,
		"warnings": готовность["warnings"],
		"notified": notices.уведомить_о_выходе(course),
	}


@frappe.whitelist(methods=["POST"])
@контракт
def announce_course(course: str) -> dict:
	"""Показывает курс в каталоге как анонс: записаться нельзя, можно
	попросить сообщить о выходе.

	Готовности курса анонс не требует — уроков может ещё не быть. Требует
	целей курса: у анонса наружу выходят только они (learning-services#389).
	Открытый курс анонсом не становится: на него уже записаны ученики.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	сведения = frappe.db.get_value("LMS Course", course, ["published", "upcoming"], as_dict=True)
	if сведения.published and not сведения.upcoming:
		raise Отказ(КУРС_УЖЕ_ОТКРЫТ, "Курс уже открыт ученикам", course=course)
	цели = announcements.цели_курса(course)
	if not цели:
		raise Отказ(
			НЕТ_ЦЕЛЕЙ_КУРСА,
			"У курса нет целей: задайте их в директиве курса (objectives)",
			course=course,
		)
	frappe.db.set_value("LMS Course", course, {"published": 1, "upcoming": 1})
	return {"id": course, "published": True, "upcoming": True, "objectives": цели}


@frappe.whitelist(methods=["POST"])
@контракт
def unpublish_course(course: str) -> dict:
	"""Снимает курс с публикации — и анонс тоже. Прогресс учеников остаётся."""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	frappe.db.set_value("LMS Course", course, {"published": 0, "upcoming": 0})
	return {"id": course, "published": False}


# --- тестеры курса (learning-services#393) ---
#
# Зовёт кабинет автора. Агентам этих действий нет: доступ к черновику выдаёт
# человек, и в MCP методы не выставляются.


@frappe.whitelist()
@контракт
def course_testers(course: str) -> dict:
	"""Тестеры курса: кому автор открыл курс до публикации."""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	return {"course": course, "testers": testers.тестеры(course)}


@frappe.whitelist(methods=["POST"])
@контракт
def add_testers(course: str, users) -> dict:
	"""Записывает тестерами курса людей с учётной записью на платформе.

	`users` — список адресов или текст, где они разделены запятыми, пробелами
	или переводами строк. Каждому записанному уходит приглашение; кого
	записать нельзя — в `skipped` с причиной.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	if isinstance(users, str) and users.strip().startswith("["):
		users = frappe.parse_json(users)
	адреса = testers.адреса(users)
	if not адреса:
		raise Отказ(НЕТ_АДРЕСОВ, "Укажите хотя бы один адрес", course=course)
	итог = testers.добавить(course, адреса)
	return {"course": course, **итог, "testers": testers.тестеры(course)}


@frappe.whitelist(methods=["POST"])
@контракт
def remove_tester(course: str, user: str) -> dict:
	"""Закрывает тестеру доступ: занятия и документ уходят в архив, запись удаляется."""
	кто = _автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	if not testers.убрать(course, user, кто):
		raise Отказ(ТЕСТЕР_НЕ_НАЙДЕН, "Этот человек не тестер курса", course=course, user=user)
	return {"course": course, "user": user, "testers": testers.тестеры(course)}


# --- вспомогательное ---


def _уроки_главы(глава: str, предел: int) -> list[dict]:
	"""Уроки главы с наполненностью: факты, по которым сверяют собранное.

	`body_segments` считает тот же разбор, что режет урок для `start_lesson`:
	число на экране автора обязано совпадать с тем, что получит агент.
	"""
	from lms_frappe_app.agent_learning import quiz

	уроки = structure.уроки_главы(глава)
	# Домашние задания главы — одним запросом, а не по уроку (learning-services#439).
	задания = (
		{
			запись.lesson: {"title": запись.title, "answer_mode": запись.answer_mode, "due_mode": запись.due_mode}
			for запись in frappe.get_all(
				homework.ЗАДАНИЕ,
				filters={"lesson": ("in", уроки)},
				fields=["lesson", "title", "answer_mode", "due_mode"],
			)
		}
		if уроки
		else {}
	)
	собранное = []
	for урок in уроки:
		сведения = frappe.db.get_value(
			"Course Lesson", урок, ["title", "body", "content"], as_dict=True
		)
		квиз = quiz._квиз_урока(урок)
		директива = directives.запись("Agent Lesson Directive", {"lesson": урок}, ("objectives",))
		материал = normalizer.нормализовать(
			title=сведения.title, content=сведения.content, body=сведения.body, предел=предел
		)
		собранное.append(
			{
				"id": урок,
				"title": сведения.title,
				"has_body": bool((сведения.body or "").strip()),
				"body_chars": len((сведения.body or "").strip()),
				"body_segments": материал.total_segments,
				"has_directive": директива is not None,
				"directive_version": директива.version if директива else None,
				"objectives": len(directives.строки(директива.objectives)) if директива else 0,
				"quiz": _вопросы_с_эталонами(квиз) if квиз else None,
				"homework": задания.get(урок),
			}
		)
	return собранное


def _вопросы_с_эталонами(квиз: str) -> dict:
	вопросы = []
	for строка in frappe.get_all(
		"LMS Quiz Question", filters={"parent": квиз}, fields=["question", "type"], order_by="idx asc"
	):
		документ = frappe.get_doc("LMS Question", строка.question)
		вопросы.append(
			{
				"id": документ.name,
				"text": документ.question,
				"type": строка.type,
				"options": [
					{
						"text": текст,
						"correct": bool(документ.get(f"is_correct_{номер}")),
						"explanation": документ.get(f"explanation_{номер}") or "",
					}
					for номер, текст in course_builder.заполненные(документ, "option")
				],
				"answers": [
					эталон for _, эталон in course_builder.заполненные(документ, "possibility")
				],
			}
		)
	return {
		"id": квиз,
		"passing_percentage": frappe.db.get_value("LMS Quiz", квиз, "passing_percentage"),
		"questions": вопросы,
	}


def _действующая_директива(lesson: str) -> dict | None:
	"""Директива, которую сейчас получает агент ученика, со своей версией."""
	return _директива_наружу("Agent Lesson Directive", {"lesson": lesson}, ПОЛЯ_ДИРЕКТИВЫ)


def _действующая_директива_курса(course: str) -> dict | None:
	"""Сквозная директива, которую агент получает на каждом занятии курса."""
	return _директива_наружу("Agent Course Directive", {"course": course}, ПОЛЯ_ДИРЕКТИВЫ_КУРСА)


def _директива_наружу(doctype: str, владелец: dict, поля: tuple[str, ...]) -> dict | None:
	"""Действующая директива куратору: текст как есть, плюс версия.

	Куратор смотрит ровно то, что уедет агенту ученика, поэтому строки не
	разбираются на пункты — этим занят учебный поток, и разбор здесь означал
	бы, что куратор сверяет не исходный текст.
	"""
	запись = directives.запись(doctype, владелец, (*поля, "creation"))
	if not запись:
		return None
	return {
		"id": запись.name,
		"version": запись.version,
		"created_at": запись.creation.isoformat(),
		**{поле: запись.get(поле) for поле in поля},
	}


def _следы_учеников(lesson: str) -> dict:
	"""Чем занимались по уроку. Пусто — значит урок никто не открывал."""
	следы = {
		"progress": frappe.db.count("LMS Course Progress", {"lesson": lesson}),
		"sessions": frappe.db.count("Agent Learning Session", {"lesson": lesson}),
		"attempts": frappe.db.count("Agent Quiz Attempt", {"lesson": lesson}),
		"homework_submissions": frappe.db.count("Agent Homework Submission", {"lesson": lesson}),
	}
	return {ключ: значение for ключ, значение in следы.items() if значение}


def _как_словарь(вопрос) -> dict:
	return json.loads(вопрос) if isinstance(вопрос, str) else dict(вопрос or {})


def _квиз_урока_или_отказ(lesson: str) -> str:
	_должен_существовать("Course Lesson", lesson, УРОК_НЕ_НАЙДЕН)
	квиз = quiz._квиз_урока(lesson)
	if not квиз:
		raise Отказ(КВИЗА_НЕТ, "У урока нет квиза: создайте его целиком", lesson=lesson)
	return квиз


def _создать_вопрос_или_отказ(вопрос: dict, lesson: str) -> tuple[str, str]:
	frappe.db.savepoint("agent_question_build")
	try:
		return course_builder.создать_вопрос(вопрос)
	except Отказ:
		raise
	except frappe.ValidationError as причина:
		frappe.db.rollback(save_point="agent_question_build")
		raise Отказ(НЕВЕРНЫЙ_ВОПРОС, str(причина), lesson=lesson) from причина


def _должен_существовать(doctype: str, имя: str, код: str) -> None:
	if not frappe.db.exists(doctype, имя):
		raise Отказ(код, f"{doctype} не найден", id=имя)


#: Больше полусотни жалоб за раз куратор всё равно не разберёт, а выборка без
#: предела однажды поднимет весь курс целиком.
РЕПОРТОВ_ЗА_РАЗ = 50

РЕПОРТ_НЕ_НАЙДЕН = "report_not_found"
НЕИЗВЕСТНЫЙ_СТАТУС_РЕПОРТА = "unknown_report_status"
НЕДОПУСТИМЫЙ_ПЕРЕХОД_РЕПОРТА = "invalid_report_transition"
НУЖЕН_ОТВЕТ_УЧЕНИКУ = "report_resolution_required"
НУЖЕН_ОРИГИНАЛ = "duplicate_of_required"
НЕВЕРНЫЙ_ОРИГИНАЛ = "invalid_duplicate_of"

#: Фильтр `course_reports` по статусу: всё, что ждёт разбора.
ФИЛЬТР_ОТКРЫТЫХ = "open"

#: Куда можно перевести репорт. Открытый — в любой другой статус; закрытый —
#: только обратно в работу. `Why:` итог уже ушёл ученику, и замена одного
#: итога другим молча переписала бы то, что он прочёл; переоткрытие же честно
#: говорит «разбираемся заново», а новый итог дойдёт до него снова.
ПЕРЕХОДЫ_РЕПОРТА = {
	"new": frozenset({"in_progress", "fixed", "rejected", "duplicate"}),
	"in_progress": frozenset({"new", "fixed", "rejected", "duplicate"}),
	"fixed": frozenset({"in_progress"}),
	"rejected": frozenset({"in_progress"}),
	"duplicate": frozenset({"in_progress"}),
}

#: Итоги, которые ученику нужно объяснить словами: «исправили» и «не будем»
#: без ответа — пустой звук. Дубль отвечает ответом оригинала.
С_ОТВЕТОМ = frozenset({"fixed", "rejected"})


@frappe.whitelist()
@контракт
def course_reports(
	course: str,
	kind: str | None = None,
	lesson: str | None = None,
	limit: int | None = None,
	status: str | None = None,
) -> dict:
	"""Репорты агентов по курсу: что мешает курсу работать.

	`status` — статус наружу или `open`: всё, что ждёт разбора.

	`Why:` без чтения механизм разомкнут — `report_issue` умел только
	записывать, и обратная связь о курсе, который не работает, лежала мёртвым
	грузом. Курс чинит тот, кто его собрал, а видел жалобы только сотрудник
	платформы в desk.

	Кто пожаловался, не отдаётся. Куратору нужно, что не так с курсом, а не
	кто сказал: имя в выдаче превращает обратную связь в донос и отучает
	жаловаться. Занятие и ученик остаются в записи — сотрудник платформы
	разберётся, если дойдёт до разбирательства.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)

	фильтры = {"course": course}
	if kind:
		значение = ВИДЫ_РЕПОРТОВ.get(kind.strip().lower())
		if not значение:
			raise Отказ(
				НЕИЗВЕСТНЫЙ_ВИД_РЕПОРТА,
				"Вид репорта: " + ", ".join(ВИДЫ_РЕПОРТОВ),
				kind=kind,
			)
		фильтры["kind"] = значение
	if lesson:
		фильтры["lesson"] = lesson
	if status:
		фильтры["status"] = ("in", _статусы_фильтра(status))

	записи = frappe.get_all(
		"Agent Course Report",
		filters=фильтры,
		fields=[
			"name",
			"kind",
			"lesson",
			"objective",
			"question",
			"text",
			"creation",
			"lesson_directive",
			"status",
			"resolution",
			"resolved_at",
		],
		order_by="creation desc",
		limit=min(int(limit or РЕПОРТОВ_ЗА_РАЗ), РЕПОРТОВ_ЗА_РАЗ),
	)
	версии = _версии_директив([з.lesson_directive for з in записи if з.lesson_directive])

	return {
		"course": course,
		"reports": [
			{
				"id": з.name,
				"kind": ИМЯ_ВИДА_РЕПОРТА.get(з.kind, з.kind),
				"lesson": з.lesson,
				"objective": з.objective or None,
				"question": з.question or None,
				"text": з.text,
				"reported_at": з.creation.isoformat() if з.creation else None,
				"directive_version": версии.get(з.lesson_directive),
				"status": ИМЯ_СТАТУСА_РЕПОРТА.get(з.status, з.status),
				"resolution": з.resolution or None,
				"resolved_at": з.resolved_at.isoformat() if з.resolved_at else None,
			}
			for з in записи
		],
	}


@frappe.whitelist(methods=["POST"])
@контракт
def resolve_report(
	report: str,
	status: str,
	resolution: str | None = None,
	duplicate_of: str | None = None,
) -> dict:
	"""Сменить статус репорта и ответить ученику.

	`resolution` — ответ, который увидит ученик: в `my_reports` и на ближайшем
	занятии курса. Он пишется заново при каждой смене статуса: не передан —
	ответа нет, и переоткрытый репорт не показывает ученику прежний итог как
	действующий. `duplicate_of` — репорт того же курса, дублем которого
	признан этот; без своего ответа ученик видит ответ оригинала.

	Переходы — `ПЕРЕХОДЫ_РЕПОРТА`; дату итога ставит сама запись.
	"""
	_автор()
	if not frappe.db.exists("Agent Course Report", report):
		raise Отказ(РЕПОРТ_НЕ_НАЙДЕН, "Репорта нет", id=report)
	стало = (status or "").strip().lower()
	if стало not in СТАТУСЫ_РЕПОРТОВ:
		raise Отказ(
			НЕИЗВЕСТНЫЙ_СТАТУС_РЕПОРТА,
			"Статус репорта: " + ", ".join(СТАТУСЫ_РЕПОРТОВ),
			status=status,
		)

	документ = frappe.get_doc("Agent Course Report", report)
	было = ИМЯ_СТАТУСА_РЕПОРТА.get(документ.status, документ.status)
	if стало not in ПЕРЕХОДЫ_РЕПОРТА.get(было, ()):
		raise Отказ(
			НЕДОПУСТИМЫЙ_ПЕРЕХОД_РЕПОРТА,
			f"Из «{было}» в «{стало}» перейти нельзя",
			status=было,
			allowed=sorted(ПЕРЕХОДЫ_РЕПОРТА.get(было, ())),
		)
	ответ = (resolution or "").strip()
	if стало in С_ОТВЕТОМ and not ответ:
		raise Отказ(НУЖЕН_ОТВЕТ_УЧЕНИКУ, "Напишите ученику, что сделали", status=стало)
	оригинал = _оригинал_дубля(документ, duplicate_of) if стало == "duplicate" else None

	документ.status = СТАТУСЫ_РЕПОРТОВ[стало]
	документ.resolution = ответ or None
	документ.duplicate_of = оригинал
	документ.save()
	return {
		"id": документ.name,
		"status": стало,
		"resolution": документ.resolution or None,
		"resolved_at": документ.resolved_at.isoformat() if документ.resolved_at else None,
		"duplicate_of": документ.duplicate_of or None,
	}


def _статусы_фильтра(status: str) -> list[str]:
	"""Значения Select для фильтра по статусу наружу."""
	имя = status.strip().lower()
	if имя == ФИЛЬТР_ОТКРЫТЫХ:
		return list(ОТКРЫТЫЕ_РЕПОРТЫ)
	if имя not in СТАТУСЫ_РЕПОРТОВ:
		raise Отказ(
			НЕИЗВЕСТНЫЙ_СТАТУС_РЕПОРТА,
			"Статус репорта: " + ", ".join((ФИЛЬТР_ОТКРЫТЫХ, *СТАТУСЫ_РЕПОРТОВ)),
			status=status,
		)
	return [СТАТУСЫ_РЕПОРТОВ[имя]]


def _оригинал_дубля(документ, duplicate_of: str | None) -> str:
	"""Репорт, дублем которого признан `документ`, — или отказ.

	Оригинал — из того же курса и не сам репорт: ответ оригинала уходит
	ученику, и ссылка на чужой курс показала бы ему ответ о курсе, которого
	он не проходил.
	"""
	оригинал = (duplicate_of or "").strip()
	if not оригинал:
		raise Отказ(НУЖЕН_ОРИГИНАЛ, "Укажите репорт, дублем которого признан этот")
	if оригинал == документ.name:
		raise Отказ(НЕВЕРНЫЙ_ОРИГИНАЛ, "Репорт не может быть дублем самого себя", duplicate_of=оригинал)
	курс = frappe.db.get_value("Agent Course Report", оригинал, "course")
	if not курс:
		raise Отказ(НЕВЕРНЫЙ_ОРИГИНАЛ, "Такого репорта нет", duplicate_of=оригинал)
	if курс != документ.course:
		raise Отказ(НЕВЕРНЫЙ_ОРИГИНАЛ, "Оригинал — репорт другого курса", duplicate_of=оригинал)
	return оригинал


def _версии_директив(имена: list[str]) -> dict[str, int]:
	"""Номера редакций директив одним запросом на всю выдачу.

	`Why:` претензия «указание не подходит» без редакции нечитаема — курс с
	тех пор переписывали, и непонятно, на что жаловались. Запрос на каждый
	репорт превратил бы чтение полусотни жалоб в полсотни обходов базы.
	"""
	if not имена:
		return {}
	return {
		д.name: д.version
		for д in frappe.get_all(
			"Agent Lesson Directive",
			filters={"name": ("in", list(set(имена)))},
			fields=["name", "version"],
		)
	}
