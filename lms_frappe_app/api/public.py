# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Витрина курса — методы, которые зовут страницы Learning, а не агент.

`Why:` карту курса показывают и гостю, которому курс открывают до
регистрации. Прочие методы ученика требуют входа: у них есть ученик, чьи
данные они отдают, а здесь наружу идёт только состав курса, цели его уроков
и то, куда вести ученика с урока.
"""

from urllib.parse import quote

import frappe

from lms_frappe_app.agent_learning import announcements, directives, testers
from lms_frappe_app.agent_learning import programs as программы
from lms_frappe_app.agent_learning import spaces as пространства
from lms_frappe_app.agent_learning.artifacts import data
from lms_frappe_app.agent_learning.artifacts.course import _схемы_курса
from lms_frappe_app.agent_learning.artifacts.document import _блок_заполнен, _заполненность, _содержимое_курса
from lms_frappe_app.agent_learning.constants import ПРОЙДЕН
from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	ПУТЬ_ЧАТА,
	адрес_сервиса,
	пробных_уроков,
	веб_уроки_ученика,
)
from lms_frappe_app.agent_learning.errors import УРОК_НЕ_НАЙДЕН, Отказ
from lms_frappe_app.agent_learning.structure import уроки_курса, уроки_по_главам
from lms_frappe_app.api import контракт, текущий_пользователь
from lms_frappe_app.api.authoring import КУРС_НЕ_НАЙДЕН
from lms_frappe_app.api.student import _пройденные, _следующий_урок

#: Куда вести ученика, когда веб-чат недоступен: там шаги подключения агента.
СТРАНИЦА_АГЕНТА = "/agent"


@frappe.whitelist(allow_guest=True, methods=["GET"])
@контракт
def course_map(course: str, space: str | None = None) -> dict:
	"""Карта курса: главы, уроки, цели уроков и покрытие целей.

	Покрытие приходит только зачисленному и только его собственное: у цели
	появляется поле `status`. Прочим поля нет вовсе — `null` был бы
	неотличим от «цель не разобрана», а отсутствие ключа спутать не с чем.

	Из директивы наружу выходят ровно две вещи: цели и иконка. Всё
	остальное — как вести урок, проверочные вопросы, заблуждения, критерии —
	остаётся на сервере.

	Зачин урока (`hook`) обращён к ученику и виден всем. Зачисленному — ещё
	пройденность урока (`completed`) и следующий незакрытый урок курса
	(`next_lesson`): из них страница курса рисует программу с отметкой статуса
	(learning-services#322). Прочим этих ключей нет — по тому же правилу, что
	у `status` цели.

	`documents` — документы курса, которые ученик собирает по ходу, и у урока
	`blocks` — какие их блоки собирают на нём (learning-services#340). Видны
	всем: что курс оставит после себя, — его обещание. Зачисленному — ещё
	заполненность документа и блока. Подсказки автора наружу не выходят: они
	адресованы агенту.
	"""
	зачислен = _зачислен(course)
	if not зачислен and not frappe.db.get_value("LMS Course", course, "published"):
		# Непубликованный курс для постороннего не существует. Отказ доменный,
		# а не 404: тем же кодом отвечают методы авторинга на чужой курс.
		raise Отказ(КУРС_НЕ_НАЙДЕН, "Курс не найден", id=course)
	if not зачислен and announcements.анонсирован(course):
		return _анонс(course)

	структура = уроки_по_главам(course)
	порядок = [урок for глава in структура for урок in глава["lessons"]]
	названия = _названия(порядок)
	зачины = _зачины(порядок)
	из_директив = {урок: _директива_карты(урок) for урок in порядок}
	покрытие = _покрытие(порядок) if зачислен else {}
	номера = {урок: номер for номер, урок in enumerate(порядок, start=1)}
	документы, блоки_уроков = _документы_курса(course, frappe.session.user if зачислен else None, space)
	ученику = {}
	if зачислен:
		пройдены = _пройденные(frappe.session.user, course)
		следующий = _следующий_урок(frappe.session.user, course)
		ученику = {"next_lesson": следующий["id"] if следующий else None}
		# Тестер проверяет курс до публикации: страница говорит ему об этом
		# (learning-services#393). У остальных ключа нет.
		if testers.тестер(frappe.session.user, course):
			ученику["tester"] = True

	def _урок(урок: str) -> dict:
		данные = {
			"id": урок,
			"number": номера[урок],
			"title": названия.get(урок),
			"hook": зачины.get(урок),
			"icon": из_директив[урок]["icon"],
			"objectives": [
				_цель(цель, покрытие.get(урок, {})) for цель in из_директив[урок]["objectives"]
			],
			"blocks": блоки_уроков.get(урок, []),
		}
		if зачислен:
			данные["completed"] = урок in пройдены
		return данные

	return {
		"course": course,
		"title": frappe.db.get_value("LMS Course", course, "title"),
		**ученику,
		"documents": документы,
		"chapters": [
			{
				"title": глава["title"],
				"lessons": [_урок(урок) for урок in глава["lessons"]],
			}
			for глава in структура
		],
	}


@frappe.whitelist(allow_guest=True, methods=["GET"])
@контракт
def course_programs(course: str) -> dict:
	"""В какие программы входит курс и заперт ли он программой (learning-services#405).

	Зовёт страница курса: «курс 2 из 3 программы …», а у запертого курса —
	«сначала пройдите …» вместо записи. Видны опубликованные программы и те,
	где вызывающий участник. `locked_by` — курс, который пройти раньше: только
	участнику программы с обязательным порядком, пока предыдущий курс не
	пройден целиком; прочим — `null`.
	"""
	if not _зачислен(course) and not frappe.db.get_value("LMS Course", course, "published"):
		raise Отказ(КУРС_НЕ_НАЙДЕН, "Курс не найден", id=course)
	return {"course": course, "programs": программы.программы_курса(course, frappe.session.user)}


@frappe.whitelist(methods=["GET"])
@контракт
def lesson_entry(lesson: str | None = None, course: str | None = None, space: str | None = None) -> dict:
	"""Вход в урок: зачин, пройден ли урок и куда вести на занятие.

	Зовёт страница урока в браузере. Урок проходится с агентом, а не
	чтением, поэтому страница показывает не материал, а дорогу на занятие:
	в веб-чат на этот урок, пока у ученика остались пробные уроки, иначе — на
	страницу подключения своего агента.

	Только `course` — вход в следующий незакрытый урок этого курса: так кнопка
	«Продолжить» на странице курса ведёт прямо на занятие
	(learning-services#301). `Why:` «текущий урок» Learning двигает таймер
	просмотра, а его мы отключили (#305), и по нему кнопка всегда вела бы на
	первый урок. Курс пройден целиком — первый урок, для повтора.

	Материал и директива сюда не выходят: материал написан для агента, а зачин
	(`lesson_hook`) — единственное в уроке, что обращено к ученику.
	"""
	ученик = текущий_пользователь()
	if not lesson and course:
		lesson = _урок_для_продолжения(ученик, course)
	урок = lesson and frappe.db.get_value(
		"Course Lesson", lesson, ["name", "title", "course", "lesson_hook"], as_dict=True
	)
	if not урок:
		raise Отказ(УРОК_НЕ_НАЙДЕН, "Урок не найден", id=lesson or course)

	пройден = frappe.db.exists(
		"LMS Course Progress", {"member": ученик, "lesson": lesson, "status": ПРОЙДЕН}
	)
	return {
		"lesson": урок.name,
		"course": урок.course,
		"title": урок.title,
		"hook": (урок.lesson_hook or "").strip() or None,
		"completed": bool(пройден),
		"study": _куда_на_занятие(ученик, lesson),
		# Курс заперт программой — страница урока ведёт к предыдущему курсу, а
		# не на занятие, которое откажет (#405).
		"program_lock": программы.замок(ученик, урок.course),
		# Что из документа курса собирают на этом занятии (#340); ученику
		# курса — с отметкой, готов ли блок.
		"blocks": _документы_курса(урок.course, ученик if _зачислен(урок.course) else None, space)[1].get(
			урок.name, []
		),
	}


def _урок_для_продолжения(ученик: str, course: str) -> str | None:
	"""Первый незакрытый урок курса, а у пройденного курса — первый урок."""
	следующий = _следующий_урок(ученик, course)
	if следующий:
		return следующий["id"]
	уроки = уроки_курса(course)
	return уроки[0] if уроки else None


def _куда_на_занятие(ученик: str, lesson: str) -> dict:
	"""Веб-чат на урок, если он ответит, иначе страница подключения агента.

	Урок, уже начатый в веб-чате, пробного не тратит — туда чат пустит всегда,
	тем же правилом, что и `start_lesson` с каналом `web`.
	"""
	сервис = адрес_сервиса()
	использовано = веб_уроки_ученика(ученик)
	осталось = max(пробных_уроков() - len(использовано), 0)
	if сервис and (lesson in использовано or осталось):
		return {
			"channel": "web",
			"url": f"{сервис}{ПУТЬ_ЧАТА}?lesson={quote(lesson, safe='')}",
			"demo_left": осталось,
		}
	return {"channel": "agent", "url": СТРАНИЦА_АГЕНТА, "demo_left": осталось}


def _цель(цель: str, покрытие_урока: dict[str, str]) -> dict:
	"""Цель карты: текст всегда, статус — только если по ней был отчёт."""
	если_есть = {"status": покрытие_урока[цель]} if цель in покрытие_урока else {}
	return {"text": цель, **если_есть}


def _зачислен(course: str) -> bool:
	"""Записан ли вызывающий на курс. Гость — никогда."""
	пользователь = frappe.session.user
	if not пользователь or пользователь == "Guest":
		return False
	return bool(frappe.db.exists("LMS Enrollment", {"member": пользователь, "course": course}))


def _названия(уроки: list[str]) -> dict[str, str]:
	"""Названия уроков одним запросом на весь курс."""
	if not уроки:
		return {}
	return {
		урок.name: урок.title
		for урок in frappe.get_all(
			"Course Lesson", filters={"name": ("in", уроки)}, fields=["name", "title"]
		)
	}


def _зачины(уроки: list[str]) -> dict[str, str | None]:
	"""Зачины уроков одним запросом; пустой — `None`, как у `lesson_entry`."""
	if not уроки:
		return {}
	return {
		урок.name: (урок.lesson_hook or "").strip() or None
		for урок in frappe.get_all(
			"Course Lesson", filters={"name": ("in", уроки)}, fields=["name", "lesson_hook"]
		)
	}


def _анонс(course: str) -> dict:
	"""Карта анонсированного курса: только цели курса, без программы.

	Главы, уроки и документы анонса остаются закрытыми: курс ещё собирается,
	и его состав меняется (learning-services#389). `notify` — подписан ли
	вызывающий на письмо о выходе; гостю ключа нет, как `status` у цели.
	"""
	данные = {
		"course": course,
		"title": frappe.db.get_value("LMS Course", course, "title"),
		"upcoming": True,
		"objectives": announcements.цели_курса(course),
		"documents": [],
		"chapters": [],
	}
	if frappe.session.user != "Guest":
		данные["notify"] = announcements.подписан(frappe.session.user, course)
	return данные


def _директива_карты(урок: str) -> dict:
	"""Цели и иконка действующей директивы урока — то немногое, что видно снаружи."""
	найденная = directives.запись(
		"Agent Lesson Directive", {"lesson": урок}, ("objectives", "map_icon")
	)
	if not найденная:
		return {"objectives": [], "icon": None}
	return {
		"objectives": directives.строки(найденная.objectives),
		"icon": найденная.map_icon or None,
	}


def _покрытие(уроки: list[str]) -> dict[str, dict[str, str]]:
	"""Покрытие целей вызывающего по урокам курса: `{урок: {цель: статус}}`.

	Занятий по одному уроку может быть несколько — урок открывают повторно.
	Побеждает более позднее: карта показывает, как дела обстоят сейчас, а не
	как обстояли на первом заходе.
	"""
	if not уроки:
		return {}
	занятия = frappe.get_all(
		"Agent Learning Session",
		filters={"student": frappe.session.user, "lesson": ("in", уроки)},
		fields=["name", "lesson"],
		order_by="creation asc",
	)
	if not занятия:
		return {}

	урок_занятия = {занятие.name: занятие.lesson for занятие in занятия}
	порядок = {занятие.name: номер for номер, занятие in enumerate(занятия)}
	строки = frappe.get_all(
		"Agent Objective Outcome",
		filters={"parent": ("in", list(урок_занятия)), "parenttype": "Agent Learning Session"},
		fields=["parent", "objective", "status"],
	)

	покрытие: dict[str, dict[str, str]] = {}
	# Сортировка по времени занятия, а не по имени: имена — хеши, и порядок по
	# ним случаен. От него зависит, чей статус останется последним.
	for строка in sorted(строки, key=lambda строка: порядок[строка.parent]):
		покрытие.setdefault(урок_занятия[строка.parent], {})[строка.objective] = строка.status
	return покрытие


def _документы_курса(
	course: str, ученик: str | None, space: str | None
) -> tuple[list[dict], dict[str, list[dict]]]:
	"""Документы курса и их блоки по урокам; ученику — с заполненностью в пространстве.

	Один проход на курс: схемы документов и, если есть ученик, его содержимое
	по всем документам сразу — так же, как перечень у `artifact`. `space` —
	чей документ показывать; без него — по правилу `spaces.пространство_курса`.
	"""
	схемы = _схемы_курса(course)
	if not схемы:
		return [], {}
	содержимое, вложения, данные = (
		_содержимое_курса(ученик, course, пространства.пространство_курса(ученик, course, space))
		if ученик
		else ({}, {}, {})
	)
	документы: list[dict] = []
	по_урокам: dict[str, list[dict]] = {}
	for схема in схемы:
		документ = {"artifact": схема.slug, "title": схема.title}
		свои = (содержимое.get(схема.slug, {}), вложения.get(схема.slug, {}), данные.get(схема.slug))
		if ученик:
			документ.update(_заполненность(схема, *свои))
		документы.append(документ)
		for блок in схема.blocks:
			if not блок.lesson:
				continue
			описание = {"artifact": схема.slug, "key": блок.block_key, "title": блок.title}
			if ученик:
				описание["filled"] = _блок_заполнен(
					схема,
					блок,
					свои[0],
					свои[1],
					свои[2] or data.данные(None),
				)
			по_урокам.setdefault(блок.lesson, []).append(описание)
	return документы, по_урокам
