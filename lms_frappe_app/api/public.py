# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Витрина курса — методы, которые зовут страницы Learning, а не агент.

`Why:` карту курса показывают и гостю, которому курс открывают до
регистрации. Прочие методы ученика требуют входа: у них есть ученик, чьи
данные они отдают, а здесь наружу идёт только состав курса, цели его уроков
и то, куда вести ученика с урока.
"""

from collections.abc import Collection
from urllib.parse import quote

import frappe

from lms_frappe_app.agent_learning import announcements, testers
from lms_frappe_app.agent_learning import programs as программы
from lms_frappe_app.agent_learning import spaces as пространства
from lms_frappe_app.agent_learning.artifacts import data
from lms_frappe_app.agent_learning.artifacts.course import _схемы_курса
from lms_frappe_app.agent_learning.artifacts.document import _данные_курса, _заполненность, кратко
from lms_frappe_app.agent_learning.constants import ПРОЙДЕН
from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	ПУТЬ_ЧАТА,
	адрес_сервиса,
	пробных_уроков,
	веб_уроки_ученика,
)
from lms_frappe_app.agent_learning.errors import КУРС_НЕ_НАЙДЕН, УРОК_НЕ_НАЙДЕН, Отказ
from lms_frappe_app.agent_learning.releases import index
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.agent_learning.structure import уроки_курса, уроки_по_главам
from lms_frappe_app.api import контракт, текущий_пользователь
from lms_frappe_app.api.student import _пройденные, _следующий_урок

#: Куда вести ученика, когда веб-чат недоступен: там шаги подключения агента.
СТРАНИЦА_АГЕНТА = "/lms/agent"


@frappe.whitelist(allow_guest=True, methods=["GET"])
@контракт
def course_map(course: str, space: str | None = None) -> dict:
	"""Карта курса: главы, уроки, цели уроков и покрытие целей.

	Покрытие приходит только зачисленному на курс из релиза и только его
	собственное, из прохождений уроков: у начатой цели появляется поле
	`status`. Прочим поля нет вовсе — `null` был бы неотличим от «цель не
	разобрана», а отсутствие ключа спутать не с чем.

	Цели урока курса из релиза — тексты целей релиза, без пунктов. У курса
	без релиза целей у уроков нет — пустые списки.

	Зачин урока (`hook`) обращён к ученику и виден всем. Зачисленному — ещё
	пройденность урока (`completed`) и следующий незакрытый урок курса
	(`next_lesson`): из них страница курса рисует программу с отметкой статуса
	(learning-services#322). Прочим этих ключей нет — по тому же правилу, что
	у `status` цели.

	`documents` — документы курса, которые ученик собирает по ходу, и у урока
	`blocks` — какие их блоки собирают на нём (learning-services#340). Видны
	всем: что курс оставит после себя, — его обещание; у блока — описание
	для ученика. Зачисленному — ещё заполненность документа и блока.

	У главы — описание из действующего релиза; зачисленному — ещё прогресс
	главы, как у `course_outline` (learning-services#547).
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
	релиз = прохождения.действующий(course)
	уроки_релиза = index.уроки(релиз) if релиз else []
	цели_уроков = _цели_уроков(course, порядок, frappe.session.user if зачислен else None, релиз, уроки_релиза)
	главы = _главы_карты(course, структура, релиз, уроки_релиза, frappe.session.user if зачислен else None)
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
			"objectives": цели_уроков.get(урок, []),
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
				**главы[глава["name"]],
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
	(`lesson_hook`) — единственное в уроке, что обращено к ученику. Цели урока
	— как у урока в `course_map` (learning-services#547).

	Урок курса вне витрины — неопубликованного или анонса — для незачисленного
	не существует, как курс в `course_map` (learning-services#692).
	"""
	ученик = текущий_пользователь()
	if not lesson and course:
		lesson = _урок_для_продолжения(ученик, course)
	урок = lesson and frappe.db.get_value(
		"Course Lesson", lesson, ["name", "title", "course", "lesson_hook"], as_dict=True
	)
	зачислен = bool(урок) and _зачислен(урок.course)
	if not урок or (not зачислен and not _на_витрине(урок.course)):
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
		# Цели урока — как у урока в `course_map`: статус только зачисленному.
		"objectives": _цели_уроков(урок.course, [урок.name], ученик if зачислен else None)[урок.name],
		"study": _куда_на_занятие(ученик, lesson),
		# Курс заперт программой — страница урока ведёт к предыдущему курсу, а
		# не на занятие, которое откажет (#405).
		"program_lock": программы.замок(ученик, урок.course),
		# Что из документа курса собирают на этом занятии (#340); ученику
		# курса — с отметкой, готов ли блок.
		"blocks": _документы_курса(урок.course, ученик if зачислен else None, space)[1].get(
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
	"""Веб-чат на урок, если он ответит, иначе страница подключения агента (`дорога_на_занятие`)."""
	сервис = адрес_сервиса()
	использовано = веб_уроки_ученика(ученик)
	return дорога_на_занятие(lesson, сервис, использовано, пробных_уроков())


def дорога_на_занятие(lesson: str, сервис: str, использовано: Collection[str], пробных: int) -> dict:
	"""`{channel, url, demo_left}` — без базы.

	`сервис` — адрес сервиса агента (`адрес_сервиса`, пусто — сервиса нет),
	`использовано` — уроки, начатые в веб-чате, `пробных` — число пробных
	уроков. Веб-чат — когда сервис есть и урок уже начат в чате или пробные
	остались. Урок, уже начатый в веб-чате, пробного не тратит — туда чат
	пустит всегда, тем же правилом, что и `start_lesson` с каналом `web`.
	"""
	осталось = max(пробных - len(использовано), 0)
	if сервис and (lesson in использовано or осталось):
		return {
			"channel": "web",
			"url": f"{сервис}{ПУТЬ_ЧАТА}?lesson={quote(lesson, safe='')}",
			"demo_left": осталось,
		}
	return {"channel": "agent", "url": СТРАНИЦА_АГЕНТА, "demo_left": осталось}


def _на_витрине(course: str) -> bool:
	"""Опубликован и не анонс (`announcements.анонсирован`): такой курс открыт и
	тому, кто на него не записан."""
	курс = frappe.db.get_value("LMS Course", course, ["published", "upcoming"], as_dict=True)
	return bool(курс and курс.published and not курс.upcoming)


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


def _главы_карты(
	course: str, структура: list[dict], релиз: str | None, уроки_релиза: list, ученик: str | None
) -> dict[str, dict]:
	"""Глава карты → `{description}` из индекса действующего релиза; `ученик`
	(зачисленный) получает ещё `status`, `lessons_total` и `lessons_passed` —
	те же, что у `course_outline` (`runs.прогресс_глав`).

	Глава записи узнаётся по своему ключу (`Course Chapter.chapter_key`,
	learning-services#514) — одной выборкой на курс. Курс без релиза —
	`description: null`, без прогресса: его считают прохождения уроков релиза.
	"""
	if not релиз:
		return {глава["name"]: {"description": None} for глава in структура}
	ключ_главы = dict(
		frappe.get_all(
			"Course Chapter",
			filters={"name": ("in", [г["name"] for г in структура] or [""])},
			fields=["name", "chapter_key"],
			as_list=True,
		)
	)
	главы_релиза = {г["key"]: г for г in index.главы(релиз)}
	прогресс = {}
	if ученик:
		уроки_глав = {ключ: [] for ключ in главы_релиза}
		for у in уроки_релиза:
			уроки_глав.setdefault(у.chapter_key, []).append(у.lesson_key)
		прогресс = {
			п["key"]: {к: п[к] for к in ("status", "lessons_total", "lessons_passed")}
			for п in прохождения.прогресс_глав(уроки_глав, прохождения.статусы_уроков(ученик, course))
		}
	итог = {}
	for глава in структура:
		ключ = ключ_главы.get(глава["name"])
		описание = (главы_релиза.get(ключ) or {}).get("description")
		итог[глава["name"]] = {"description": описание or None, **прогресс.get(ключ, {})}
	return итог


#: Действующий релиз ещё не прочитан: `None` у релиза — «у курса его нет».
_НЕ_ПРОЧИТАН = object()


def _цели_уроков(
	course: str,
	уроки: list[str],
	ученик: str | None,
	релиз=_НЕ_ПРОЧИТАН,
	уроки_релиза: list | None = None,
) -> dict[str, list[dict]]:
	"""Цели уроков карты: урок → `[{text, status?}]`.

	Курс из релиза — цели уроков действующего релиза; `ученик` (зачисленный)
	получает `status` у цели, начатой в его прохождении (`touched`,
	`covered`): не начатая приходит без ключа, как «не дошли». Статусы —
	сохранённые, простым чтением, без сверки (`runs.статусы_целей`).

	Курс без релиза — пустые списки: цели урока задаёт только релиз.
	`релиз` и `уроки_релиза` — когда вызывающий их уже прочёл.
	"""
	if релиз is _НЕ_ПРОЧИТАН:
		релиз = прохождения.действующий(course)
	if not релиз:
		return {урок: [] for урок in уроки}
	if уроки_релиза is None:
		уроки_релиза = index.уроки(релиз)
	ключи = {у.lesson: у.lesson_key for у in уроки_релиза}
	цели = index.цели(релиз)
	статусы = прохождения.статусы_целей(ученик, course) if ученик else {}
	итог = {}
	for урок in уроки:
		ключ = ключи.get(урок)
		итог[урок] = []
		for цель in цели.get(ключ, []):
			статус = статусы.get((ключ, цель["key"]), прохождения.НЕ_НАЧАТО)
			if статус == прохождения.НЕ_НАЧАТО:
				итог[урок].append({"text": цель["text"]})
			else:
				итог[урок].append({"text": цель["text"], "status": статус})
	return итог


def _документы_курса(
	course: str, ученик: str | None, space: str | None
) -> tuple[list[dict], dict[str, list[dict]]]:
	"""Документы курса и их блоки по урокам; ученику — с заполненностью в пространстве.

	Один проход на курс: схемы документов и, если есть ученик, его данные
	по всем документам сразу — так же, как перечень у `artifact`. `space` —
	чей документ показывать; без него — по правилу `spaces.пространство_курса`.
	"""
	схемы = _схемы_курса(course)
	if not схемы:
		return [], {}
	данные = (
		_данные_курса(ученик, course, пространства.пространство_курса(ученик, course, space)) if ученик else {}
	)
	документы: list[dict] = []
	по_урокам: dict[str, list[dict]] = {}
	for схема in схемы:
		документ = {"artifact": схема.slug, "title": схема.title}
		свои = (данные.get(схема.slug) or data.данные(None)) if ученик else None
		if ученик:
			документ.update(_заполненность(схема, свои))
		документы.append(документ)
		for блок in схема.blocks:
			if блок.lesson:
				по_урокам.setdefault(блок.lesson, []).append(кратко(схема, блок, свои))
	return документы, по_урокам
