# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Методы автора курса.

Курс компилируется вне платформы и публикуется целиком релизом
(`publish_release`); здесь — публикация, открытие и снятие курса, анонс,
просмотр релиза, заметки, тестеры и репорты. Чем курс хорош — дело автора и
его агента, не этого файла.

Удаления курса здесь нет намеренно: снятая с публикации ошибка обратима,
удалённый курс с прогрессом учеников — нет.
"""

import json

import frappe

from lms_frappe_app.agent_learning import (
	announcements,
	notes,
	notices,
	structure,
	testers,
)
from lms_frappe_app.agent_learning.releases import checks as проверки_релиза
from lms_frappe_app.agent_learning.releases import index as releases_index
from lms_frappe_app.agent_learning.releases import places
from lms_frappe_app.agent_learning.releases import service as releases
from lms_frappe_app.agent_learning.releases import view as просмотр_релиза
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.agent_learning.constants import (
	ВИДЫ_РЕПОРТОВ,
	ИМЯ_ВИДА_РЕПОРТА,
	ИМЯ_СТАТУСА_РЕПОРТА,
	ОТКРЫТЫЕ_РЕПОРТЫ,
	СТАТУСЫ_РЕПОРТОВ,
)
from lms_frappe_app.agent_learning.errors import (
	КУРС_ИЗ_РЕЛИЗА,
	КУРС_НЕ_В_РЕЛИЗЕ,
	КУРС_НЕ_НАЙДЕН,
	НЕИЗВЕСТНЫЙ_ВИД_РЕПОРТА,
	Отказ,
)
from lms_frappe_app.api import контракт, список, текущий_пользователь

#: Роли, которым разрешены методы автора. Совпадают с административными в
#: `permissions`: там они уже дают полный доступ к учебным записям.
АВТОРСКИЕ_РОЛИ = frozenset({"Course Creator", "Moderator", "System Manager", "Administrator"})

НЕТ_ЦЕЛЕЙ_КУРСА = "course_objectives_missing"
НЕТ_АДРЕСОВ = "users_required"
ТЕСТЕР_НЕ_НАЙДЕН = "tester_not_found"
КУРС_УЖЕ_ОТКРЫТ = "course_already_published"
КУРС_БЕЗ_РЕЛИЗА = КУРС_НЕ_В_РЕЛИЗЕ
ИНСТРУКТОРОВ_НЕТ = "instructors_empty"
ИНСТРУКТОР_НЕ_НАЙДЕН = "instructor_not_found"
ИНСТРУКТОР_НЕ_АВТОР = "instructor_not_author"


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
	"""Заводит курс-анонс: карточку без уроков.

	Программу курсу даёт первый релиз (`publish_release` с `course`), до него
	курс можно анонсировать (`announce_course`).
	"""
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
	"""Курсы платформы: черновики, анонсы и опубликованные — с действующим релизом.

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
		fields=[
			"name",
			"title",
			"short_introduction",
			"published",
			"upcoming",
			"modified",
			"course_key",
			"active_release",
		],
		order_by="modified desc",
	)
	# Число уроков и релизы — одним запросом на весь список, а не на строку.
	уроков = structure.уроков_в_курсах([курс.name for курс in курсы])
	релизы = _действующие_релизы([курс.active_release for курс in курсы if курс.active_release])
	return {
		"courses": [
			{
				"id": курс.name,
				"title": курс.title,
				"summary": курс.short_introduction,
				"published": bool(курс.published),
				"upcoming": bool(курс.published and курс.upcoming),
				"course_key": курс.course_key or None,
				"release": релизы.get(курс.active_release),
				"lessons_total": уроков.get(курс.name, 0),
				"updated_at": курс.modified.isoformat() if курс.modified else None,
			}
			for курс in курсы
		]
	}


def _действующие_релизы(имена: list[str]) -> dict[str, dict]:
	"""Релиз → `{version, published_at}` одним запросом."""
	if not имена:
		return {}
	return {
		релиз.name: {
			"version": релиз.version,
			"published_at": релиз.published_at.isoformat() if релиз.published_at else None,
		}
		for релиз in frappe.get_all(
			releases.РЕЛИЗ,
			filters={"name": ("in", имена)},
			fields=["name", "version", "published_at"],
		)
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
	"""Правит название, описания или обещание анонса — курса без релиза и уроков.

	`promise` — что человек получит к концу курса; звучит ученику на первом
	занятии по курсу. Пустая строка очищает (#238). Карточку курса из релиза
	пишет релиз (`course_from_release`).
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	_не_из_релиза(course)
	if уроки := structure.уроки_курса(course):
		raise Отказ(
			releases.У_КУРСА_ЕСТЬ_УРОКИ,
			"Правится только анонс — курс без уроков: программу и карточку курса даёт релиз",
			course=course,
			lessons=len(уроки),
		)
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


# --- заметки автора ---

ЗАМЕЧАНИЕ_НЕ_НАЙДЕНО = "note_not_found"
НЕВЕРНОЕ_ЗАМЕЧАНИЕ = "invalid_note"
#: Адрес по форме верен, а такого места в действующем релизе курса нет.
МЕСТА_НЕТ_В_РЕЛИЗЕ = "note_target_unknown"


@frappe.whitelist(methods=["POST"])
@контракт
def add_note(
	course: str,
	target: str,
	text: str,
	quote: str | None = None,
	via: str = "author",
) -> dict:
	"""Заметка на месте курса — чтобы петля «увидел → агент поправил →
	принял» не шла через пересказ в чате.

	`target` — место по ключам действующего релиза (`notes.ФОРМЫ_АДРЕСА`);
	заметка помнит этот релиз. `quote` — выделенный текст. `via` — кто
	пишет: `author` из кабинета, `agent` — агент куратора через MCP; заметка
	агента — вопрос автору.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	релиз = frappe.db.get_value("LMS Course", course, "active_release")
	if not релиз:
		raise Отказ(КУРС_БЕЗ_РЕЛИЗА, "У курса нет релиза: заметки пишутся по его ключам", course=course)
	адрес = notes.разобрать_адрес(target)
	_проверить_источник(via)
	текст = _текст_замечания(text)
	место = places.Места(релиз).место(адрес)
	if место["missing"]:
		raise Отказ(
			МЕСТА_НЕТ_В_РЕЛИЗЕ,
			"Такого места нет в действующем релизе курса",
			target=notes.адрес(адрес),
			release=релиз,
		)
	документ = frappe.get_doc(
		{
			"doctype": "Agent Author Note",
			"course": course,
			"release": релиз,
			"lesson": место["lesson"],
			"target": notes.адрес(адрес),
			"status": "open",
			"via": via,
			"quote": (quote or "").strip(),
			"text": текст,
		}
	).insert()
	return {
		"id": документ.name,
		"course": course,
		"target": документ.target,
		"release": релиз,
		"version": frappe.db.get_value(releases.РЕЛИЗ, релиз, "version"),
		"lesson_key": место["lesson_key"],
		"label": место["label"],
		"missing": False,
		"status": документ.status,
		"waiting_on": notes.ждёт(документ.status, via, []),
	}


@frappe.whitelist()
@контракт
def list_notes(course: str, status: str | None = None, lesson: str | None = None) -> dict:
	"""Заметки курса с нитью ответов, старые сверху.

	Место подписано словами по действующему релизу курса; `missing` — ключа
	места в действующем релизе нет. `lesson` — ключ урока: заметки мест этого
	урока. `waiting_on` — чей ход.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	фильтры = {"course": course}
	if status:
		if status not in notes.СТАТУСЫ:
			raise Отказ(НЕВЕРНОЕ_ЗАМЕЧАНИЕ, "Статус: open, done или accepted", where="status")
		фильтры["status"] = status
	известные = None
	if lesson:
		известные = releases_index.известные(course)
		запись = известные["lessons"].get(lesson)
		if not запись:
			return {"course": course, "notes": []}
		фильтры["lesson"] = запись
	return {"course": course, "notes": _замечания(course, фильтры, известные)}


@frappe.whitelist(methods=["POST"])
@контракт
def reply_note(note: str, text: str, via: str = "author") -> dict:
	"""Ответ в нить заметки; статус не меняется. Вопрос агента — тоже
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
	"""Сменить статус заметки.

	Агент отмечает `done` и пишет в `text`, что поменял. Автор принимает —
	`accepted` — или возвращает в `open` с ответом, что не так; принять можно
	и открытую — снять свою заметку. Прочее — `invalid_transition`.
	"""
	_автор()
	документ = _замечание_или_отказ(note)
	notes.проверить_переход(документ.status, status, via, text)
	if (text or "").strip():
		_ответить(документ, text.strip(), via)
	документ.status = status
	документ.save()
	return _состояние_замечания(документ)


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
		raise Отказ(ЗАМЕЧАНИЕ_НЕ_НАЙДЕНО, "Заметки нет", id=note)
	return frappe.get_doc("Agent Author Note", note)


def _состояние_замечания(документ) -> dict:
	ответы = [{"via": ответ.via} for ответ in документ.replies]
	return {
		"id": документ.name,
		"status": документ.status,
		"waiting_on": notes.ждёт(документ.status, документ.via, ответы),
		"replies": len(ответы),
	}


def _замечания(course: str, фильтры: dict, известные: dict | None = None) -> list[dict]:
	"""Заметки по фильтрам с нитями, подписями мест и версиями релизов.

	Число выборок не растёт с числом заметок: ответы, релизы курса, ключи
	уроков и каждая нужная часть индекса действующего релиза — по выборке на
	всю выдачу.
	"""
	записи = frappe.get_all(
		"Agent Author Note",
		filters=фильтры,
		fields=[
			"name",
			"release",
			"lesson",
			"target",
			"quote",
			"text",
			"via",
			"status",
			"owner",
			"creation",
			"modified",
		],
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
	версии = {р.name: р.version for р in releases_index.история(course)}
	if any(запись.lesson for запись in записи):
		известные = известные or releases_index.известные(course)
	ключи_уроков = {запись: ключ for ключ, запись in (известные or {"lessons": {}})["lessons"].items()}
	места = places.Места(frappe.db.get_value("LMS Course", course, "active_release"))
	адреса = [_адрес_или_нет(запись) for запись in записи]
	подписи = iter(места.места([адрес for адрес in адреса if адрес]))
	собранное = []
	for запись, адрес in zip(записи, адреса):
		место = next(подписи) if адрес else {"label": запись.target, "missing": True}
		нить = нити.get(запись.name, [])
		собранное.append(
			{
				"id": запись.name,
				"target": запись.target,
				"release": запись.release or None,
				"version": версии.get(запись.release),
				"lesson_key": ключи_уроков.get(запись.lesson),
				"label": место["label"],
				"missing": место["missing"],
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


def _адрес_или_нет(запись) -> dict | None:
	"""Разобранный адрес заметки; `None` — места у заметки нет.

	Заметка без `release` — архив, места у неё нет: адрес ключом релиза не
	читается, даже если похож на него. Сохранённый адрес, который не
	разбирается, — тоже без места: очередь заметок из-за него не падает."""
	if not запись.release:
		return None
	try:
		return notes.разобрать_адрес(запись.target)
	except Отказ:
		return None


def ревизия_замечаний(course: str) -> str | None:
	"""Самая свежая отметка замечаний курса: ответ в нить и смена статуса
	сохраняют замечание и двигают её."""
	отметки = frappe.get_all(
		"Agent Author Note", filters={"course": course}, pluck="modified", order_by="modified desc", limit=1
	)
	return отметки[0].isoformat() if отметки else None


# --- релиз курса (learning-services#500) ---


@frappe.whitelist(methods=["POST"])
@контракт
def publish_release(release, course: str | None = None, instructors=None) -> dict:
	"""Публикует релиз курса целиком — новой версией.

	`release` — релиз от компилятора курса (объект или строка JSON), формат —
	`CONTRACT.md`, «Релиз курса». Проверяется публичной схемой и правилами
	сервера; по нему строятся главы и уроки Learning, схема документа и
	карточка курса. Курс ищется по ключу из релиза, нет — заводится
	черновиком. `course` — курс без уроков (анонс), к которому привязать первый
	релиз. Признак «опубликован» не меняется: новый релиз опубликованного курса
	действует сразу.

	`instructors` — почты или имена пользователей: список заменяет
	инструкторов курса, и вызвавший без места в нём инструктором не
	становится; не передан — инструкторы не трогаются (новый курс получает
	вызвавшего). Применяется и к неизменному релизу: в дайджест релиза
	инструкторы не входят.
	"""
	автор = _автор()
	инструкторы = None if instructors is None else _инструкторы(instructors)
	return releases.опубликовать(release, course or None, автор, инструкторы)


def _инструкторы(значение) -> list[str]:
	"""Имена пользователей Frappe по списку почт или имён — или отказ.

	Проверка — до первой записи публикации: отказ здесь ничего не оставляет.
	Пустой список — отказ: курс без инструкторов некому вести. Пользователь —
	действующая учётная запись с одной из авторских ролей (`АВТОРСКИЕ_РОЛИ`,
	как у `_автор`). Два запроса на весь список, а не на адрес.
	"""
	if isinstance(значение, str) and значение.strip().startswith("["):
		значение = frappe.parse_json(значение)
	адреса = testers.адреса(значение)
	if not адреса:
		raise Отказ(ИНСТРУКТОРОВ_НЕТ, "Список инструкторов пуст: у курса должен быть инструктор")
	найдены = frappe.get_all(
		"User",
		filters={"enabled": 1},
		or_filters={"name": ("in", адреса), "email": ("in", адреса)},
		fields=["name", "email"],
	)
	по_адресу = {}
	for пользователь in найдены:
		for адрес in (пользователь.name, пользователь.email):
			if адрес:
				по_адресу.setdefault(адрес.lower(), пользователь.name)
	if нет := [адрес for адрес in адреса if адрес not in по_адресу]:
		raise Отказ(ИНСТРУКТОР_НЕ_НАЙДЕН, "Нет таких пользователей", users=нет)
	имена = list(dict.fromkeys(по_адресу[адрес] for адрес in адреса))
	авторы = set(
		frappe.get_all(
			"Has Role",
			filters={"parenttype": "User", "parent": ("in", имена), "role": ("in", list(АВТОРСКИЕ_РОЛИ))},
			pluck="parent",
		)
	)
	if не_авторы := [имя for имя in имена if имя not in авторы]:
		raise Отказ(
			ИНСТРУКТОР_НЕ_АВТОР,
			"Инструктор курса — пользователь с авторской ролью: "
			"Course Creator, Moderator, System Manager или Administrator",
			users=не_авторы,
		)
	return имена


# --- обзор и публикация ---


@frappe.whitelist(methods=["GET"])
@контракт
def course_revision(course: str) -> dict:
	"""Отметки курса и его заметок — для опроса кабинетом автора.

	Страница спрашивает их раз в несколько секунд, и перечитывать ради этого
	курс целиком незачем.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	return {"course": course, "revision": ревизия(course), "notes_revision": ревизия_замечаний(course)}


def ревизия(course: str) -> str:
	"""Самая свежая отметка карточки курса и его действующего релиза.

	`Why:` курс правится только новым релизом, а он пишет и карточку курса, и
	новую запись релиза; правка анонса, открытие и снятие с публикации
	двигают карточку. Главы, уроки, квизы и схемы документа — проекции
	релиза, их отметки ничего к этому не добавляют.
	"""
	сведения = frappe.db.get_value("LMS Course", course, ["modified", "active_release"], as_dict=True)
	отметки = [сведения.modified]
	if сведения.active_release:
		отметки.append(frappe.db.get_value(releases.РЕЛИЗ, сведения.active_release, "modified"))
	return max(отметки).isoformat()


# --- просмотр релиза (learning-services#512) ---


@frappe.whitelist()
@контракт
def course_release(course: str, lesson: str | None = None) -> dict:
	"""Действующий релиз курса — только чтение: главы, уроки с целями, пунктами и
	квизом с ответами, домашки, документ.

	`lesson` — ключ урока: только он, срез пакета агента этого урока и рамка
	пакета. Без `lesson` пакета агента в ответе нет. Ответы квиза видит
	только автор — метод закрыт авторскими ролями (`_автор`).
	"""
	_автор()
	курс, релиз = _курс_с_релизом(course)
	if lesson:
		if урок := просмотр_релиза.урок_релиза(курс, релиз, lesson):
			return урок
		raise прохождения.урок_не_в_релизе(course, lesson, релиз)
	return просмотр_релиза.релиз_целиком(курс, релиз)


@frappe.whitelist()
@контракт
def course_releases(course: str) -> dict:
	"""История релизов курса, свежие вперёд; `active` — действующий."""
	_автор()
	сведения = frappe.db.get_value("LMS Course", course, ["active_release"], as_dict=True)
	if not сведения:
		raise Отказ(КУРС_НЕ_НАЙДЕН, "LMS Course не найден", id=course)
	return {"course": course, "releases": просмотр_релиза.история(course, сведения.active_release)}


def _курс_с_релизом(course: str) -> tuple[dict, str]:
	"""Карточка курса с действующим релизом и сам релиз — или отказ."""
	сведения = frappe.db.get_value("LMS Course", course, ["title", "active_release"], as_dict=True)
	if not сведения:
		raise Отказ(КУРС_НЕ_НАЙДЕН, "LMS Course не найден", id=course)
	if not сведения.active_release:
		raise Отказ(КУРС_БЕЗ_РЕЛИЗА, "У курса нет релиза: его публикует publish_release", course=course)
	return (
		просмотр_релиза.карточка(course, сведения.title, сведения.active_release),
		сведения.active_release,
	)


@frappe.whitelist(methods=["POST"])
@контракт
def publish_course(course: str) -> dict:
	"""Открывает ученикам курс с действующим релизом.

	Релиз проверен целиком при публикации (`publish_release`); `warnings` —
	предупреждения проверок его снимка, те же, что вернула его публикация.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	релиз = frappe.db.get_value("LMS Course", course, "active_release")
	if not релиз:
		raise Отказ(
			КУРС_БЕЗ_РЕЛИЗА,
			"У курса нет релиза: откройте его ученикам после публикации релиза",
			course=course,
		)
	_, предупреждения = проверки_релиза.проблемы(
		json.loads(frappe.db.get_value(releases.РЕЛИЗ, релиз, "snapshot"))
	)
	# Анонс выходит той же публикацией: флаг снимается, и тем, кто просил
	# сообщить о выходе, уходит письмо (learning-services#389).
	frappe.db.set_value("LMS Course", course, {"published": 1, "upcoming": 0})
	return {
		"id": course,
		"published": True,
		"warnings": предупреждения,
		"notified": notices.уведомить_о_выходе(course),
	}


@frappe.whitelist(methods=["POST"])
@контракт
def announce_course(course: str, objectives=None) -> dict:
	"""Показывает курс в каталоге как анонс: записаться нельзя, можно
	попросить сообщить о выходе.

	Готовности курса анонс не требует — уроков может ещё не быть. Требует
	целей курса: у анонса наружу выходят только они (learning-services#389).
	У курса из релиза цели — названия глав действующего релиза, `objectives`
	к нему не передаются. Курс без релиза анонсируется только без уроков
	(`course_has_content`), как правится `update_course`: программу курсу
	даёт релиз. У курса без релиза `objectives` (список или текст по
	строке на цель) пишутся в поле курса; не переданы — действуют записанные
	раньше (learning-services#512). Открытый курс анонсом не становится: на
	него уже записаны ученики.
	"""
	_автор()
	_должен_существовать("LMS Course", course, КУРС_НЕ_НАЙДЕН)
	сведения = frappe.db.get_value(
		"LMS Course",
		course,
		["published", "upcoming", "active_release", announcements.ПОЛЕ_ЦЕЛЕЙ],
		as_dict=True,
	)
	if сведения.active_release and objectives is not None:
		raise Отказ(
			КУРС_ИЗ_РЕЛИЗА,
			"Цели курса из релиза — названия его глав: правьте курс вне платформы и публикуйте новый релиз",
			course=course,
		)
	if not сведения.active_release and (уроки := structure.уроки_курса(course)):
		raise Отказ(
			releases.У_КУРСА_ЕСТЬ_УРОКИ,
			"Анонсируется курс без уроков или курс из релиза: программу курсу даёт релиз",
			course=course,
			lessons=len(уроки),
		)
	if сведения.published and not сведения.upcoming:
		raise Отказ(КУРС_УЖЕ_ОТКРЫТ, "Курс уже открыт ученикам", course=course)
	поля = {"published": 1, "upcoming": 1}
	if сведения.active_release:
		цели = announcements.цели_курса(course)
	elif objectives is not None:
		цели = announcements.строки(objectives)
		поля[announcements.ПОЛЕ_ЦЕЛЕЙ] = "\n".join(цели)
	else:
		цели = announcements.строки(сведения.get(announcements.ПОЛЕ_ЦЕЛЕЙ))
	if not цели:
		raise Отказ(
			НЕТ_ЦЕЛЕЙ_КУРСА,
			"У курса нет целей: передайте их в objectives, по строке на цель",
			course=course,
		)
	frappe.db.set_value("LMS Course", course, поля)
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


def _должен_существовать(doctype: str, имя: str, код: str) -> None:
	if not frappe.db.exists(doctype, имя):
		raise Отказ(код, f"{doctype} не найден", id=имя)


def _не_из_релиза(курс: str) -> None:
	"""Курс, собранный релизом, правится только новым релизом (learning-services#500).

	`Why:` правка мимо релиза разошлась бы с ним: следующая публикация молча
	переписала бы её, а индекс релиза — то, по чему учат агент и квиз, —
	правки не увидел бы вовсе.
	"""
	if frappe.db.get_value("LMS Course", курс, "active_release"):
		raise Отказ(
			КУРС_ИЗ_РЕЛИЗА,
			"Курс собран из релиза: правьте курс вне платформы и публикуйте новый релиз",
			course=курс,
		)


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

	`status` — статус наружу или `open`: всё, что ждёт разбора. `lesson` —
	ключ урока: он ищется по всей истории релизов курса, так что находятся и
	репорты урока, снятого из релиза. Ключ урока в репорте не хранится:
	`lesson_key` выводится по релизу репорта и его уроку одной выборкой на
	всю страницу.

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
	if status:
		фильтры["status"] = ("in", _статусы_фильтра(status))
	if lesson:
		запись = releases_index.известные(course)["lessons"].get(lesson)
		if not запись:
			return {"course": course, "reports": []}
		фильтры["lesson"] = запись

	записи = frappe.get_all(
		"Agent Course Report",
		filters=фильтры,
		fields=[
			"name",
			"kind",
			"lesson",
			"objective",
			"question",
			"question_key",
			"text",
			"creation",
			"lesson_directive",
			"release",
			"status",
			"resolution",
			"resolved_at",
		],
		order_by="creation desc",
		limit=min(int(limit or РЕПОРТОВ_ЗА_РАЗ), РЕПОРТОВ_ЗА_РАЗ),
	)
	версии = _версии_директив([з.lesson_directive for з in записи if з.lesson_directive])
	ключи_уроков = releases_index.ключи_уроков([з.release for з in записи if з.release])

	return {
		"course": course,
		"reports": [
			{
				"id": з.name,
				"kind": ИМЯ_ВИДА_РЕПОРТА.get(з.kind, з.kind),
				"lesson": з.lesson,
				"lesson_key": ключи_уроков.get((з.release, з.lesson)),
				"objective": з.objective or None,
				"question": з.question or None,
				"question_key": з.question_key or None,
				"text": з.text,
				"reported_at": з.creation.isoformat() if з.creation else None,
				"directive_version": версии.get(з.lesson_directive),
				"release": з.release or None,
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
