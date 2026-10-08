# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Кабинет автора: действующий релиз курса, заметки по его ключам, тестеры.

Курс компилируется вне платформы и правится только новым релизом, поэтому
кабинет — зеркало, а не редактор: человек смотрит релиз и ставит заметки на
его места, агент куратора правит курс и публикует новый релиз. Данные — из
тех же авторских методов, что отвечают агенту (`course_release`,
`list_notes`, `course_releases`, `course_testers`): второй путь к ним
разошёлся бы с первым молча.

Здесь только раскладка для отрисовки.
"""

import json
from collections import Counter
from urllib.parse import quote

import frappe

from lms_frappe_app.agent_learning import announcements, notes
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases.places import коротко
from lms_frappe_app.api import authoring
from lms_frappe_app.site_navigation import шапка_платформы

no_cache = 1

СТРАНИЦА = "/author"
МЕТОД_РЕВИЗИИ = "lms_frappe_app.api.authoring.course_revision"
МЕТОДЫ_ЗАМЕТОК = {
	"add": "lms_frappe_app.api.authoring.add_note",
	"reply": "lms_frappe_app.api.authoring.reply_note",
	"status": "lms_frappe_app.api.authoring.set_note_status",
}
МЕТОДЫ_ТЕСТЕРОВ = {
	"add": "lms_frappe_app.api.authoring.add_testers",
	"remove": "lms_frappe_app.api.authoring.remove_tester",
}

#: Цветов глав в палитре страницы; дальше они идут по кругу.
ЦВЕТОВ_ГЛАВ = 5

#: Вкладки экрана курса: релиз, очередь заметок, история релизов, тестеры.
РЕЛИЗ, ЗАМЕТКИ, ИСТОРИЯ, ТЕСТЕРЫ = "release", "notes", "history", "testers"

#: Порядок групп очереди: сначала то, что ждёт человека.
ГРУППЫ_ОЧЕРЕДИ = ("check", "question", "agent", "accepted")

#: Места заметок на экране курса — вне уроков.
МЕСТА_КУРСА = frozenset({"course", "chapter", "section"})
#: Места заметок на странице урока: урок, его цели, пункты, вопросы и пакет агента.
МЕСТА_УРОКА = frozenset({"lesson", "objective", "goal", "question", "agent.lesson", "agent.item"})

#: Части пакета агента словами; часть, которой здесь нет, подписана своим ключом.
ЧАСТИ_ПАКЕТА = {
	"frame": "Рамка курса",
	"learn_about_student": "Что выяснять об ученике",
	"directive": "Директива урока",
	"material": "Материал урока",
	"items": "Пункты",
	"sections": "Разделы документа",
}

РЕЖИМЫ_ОТВЕТА = {"text": "текстом", "files": "файлами", "text_and_files": "текстом и файлами"}


def сведения(
	пользователь: str, course: str | None = None, lesson: str | None = None, view: str | None = None
) -> dict:
	"""Что показать на странице этому пользователю.

	Без `course` — все курсы: они общие, видимость по роли, а не по авторству.
	С `course` — экран курса: релиз, очередь заметок (`view=notes`), история
	релизов (`view=history`) или тестеры (`view=testers`); с `lesson` — урок
	релиза по ключу с пакетом агента. У курса без релиза — только карточка
	анонса. Неизвестный курс или ключ урока не из релиза дают пометку, а не
	ошибку: ссылку могли прислать до нового релиза.
	"""
	основа = {
		"is_guest": пользователь == "Guest",
		"login_url": f"/login?redirect-to={СТРАНИЦА}",
		"page_url": СТРАНИЦА,
		"revision_method": МЕТОД_РЕВИЗИИ,
		"allowed": True,
		"courses": [],
		"course": None,
		"release": None,
		"lesson": None,
		"view": РЕЛИЗ,
		"notes_queue": None,
		"history": None,
		"testers": None,
		"tester_methods": МЕТОДЫ_ТЕСТЕРОВ,
		"note_methods": МЕТОДЫ_ЗАМЕТОК,
		"missing": False,
	}
	if основа["is_guest"]:
		return основа
	if not set(frappe.get_roles(пользователь)) & authoring.АВТОРСКИЕ_РОЛИ:
		основа["allowed"] = False
		return основа
	if not course:
		основа["courses"] = _курсы(authoring.list_courses()["data"]["courses"])
		return основа

	карточка = frappe.db.get_value(
		"LMS Course",
		course,
		["title", "short_introduction", "published", "upcoming", "active_release", announcements.ПОЛЕ_ЦЕЛЕЙ],
		as_dict=True,
	)
	if not карточка:
		основа["missing"] = True
		return основа
	курс = основа["course"] = _карточка(course, карточка)
	if not карточка.active_release:
		return основа

	релиз = основа["release"] = _релиз(course, authoring.course_release(course=course)["data"])
	заметки = _заметки(course)
	_заметки_релиза(релиз, заметки)
	курс["notes_attention"] = sum(з["waiting_on"] == "author" for з in заметки)
	# Число тестеров — на ярлыке вкладки, поэтому и на других вкладках.
	курс["testers_count"] = frappe.db.count("LMS Enrollment", {"course": course, "agent_tester": 1})
	if lesson:
		основа["lesson"] = _урок_целиком(course, lesson, релиз, заметки)
		основа["missing"] = основа["lesson"] is None
	elif view == ЗАМЕТКИ:
		основа["view"] = ЗАМЕТКИ
		основа["notes_queue"] = _очередь(заметки)
	elif view == ИСТОРИЯ:
		основа["view"] = ИСТОРИЯ
		основа["history"] = _история(authoring.course_releases(course=course)["data"]["releases"])
	elif view == ТЕСТЕРЫ:
		основа["view"] = ТЕСТЕРЫ
		основа["testers"] = authoring.course_testers(course=course)["data"]["testers"]
	return основа


# --- список курсов ---


def _курсы(курсы: list[dict]) -> list[dict]:
	"""Курсы со сводкой: версия действующего релиза, открытые заметки, тестеры.

	`Why:` сводка — выборками на весь список, а не на курс: курсов на
	платформе десятки, и запрос на строку рос бы вместе с ними.
	"""
	ид = [курс["id"] for курс in курсы]
	заметки = _заметки_списка(ид)
	тестеров = (
		Counter(
			frappe.get_all(
				"LMS Enrollment", filters={"course": ["in", ид], "agent_tester": 1}, pluck="course"
			)
		)
		if ид
		else Counter()
	)
	for курс in курсы:
		курс["url"] = адрес(курс["id"])
		курс["status"] = _статус(курс["published"], курс["upcoming"])
		курс["open_notes"], курс["notes_attention"] = заметки.get(курс["id"], (0, 0))
		курс["testers_count"] = тестеров[курс["id"]]
	return курсы


def _заметки_списка(курсы: list[str]) -> dict[str, tuple[int, int]]:
	"""Курс → (открытых заметок, из них ждут человека) — двумя выборками на весь список."""
	if not курсы:
		return {}
	открытые = frappe.get_all(
		"Agent Author Note",
		filters={"course": ["in", курсы], "status": ["in", ["open", "done"]]},
		fields=["name", "course", "status", "via"],
	)
	if not открытые:
		return {}
	последние: dict[str, str] = {}
	for ответ in frappe.get_all(
		"Agent Note Reply",
		filters={"parent": ["in", [з.name for з in открытые]], "parenttype": "Agent Author Note"},
		fields=["parent", "via"],
		order_by="idx asc",
	):
		последние[ответ.parent] = ответ.via
	счёт: dict[str, tuple[int, int]] = {}
	for з in открытые:
		ответы = [{"via": последние[з.name]}] if з.name in последние else []
		всего, ждут = счёт.get(з.course, (0, 0))
		счёт[з.course] = (всего + 1, ждут + (notes.ждёт(з.status, з.via, ответы) == "author"))
	return счёт


# --- курс и релиз ---


def адрес(course: str, lesson: str | None = None) -> str:
	"""Адрес экрана курса; с `lesson` — урока релиза по ключу."""
	путь = f"{СТРАНИЦА}?course={quote(course)}"
	return f"{путь}&lesson={quote(lesson, safe='')}" if lesson else путь


def якорь(target: str) -> str:
	"""Якорь места заметки на странице: `section.log` → `note-section-log`."""
	return "note-" + target.replace(".", "-").replace("/", "-")


def _статус(опубликован: bool, анонс: bool) -> str:
	return "анонс" if анонс else ("опубликован" if опубликован else "черновик")


def _карточка(course: str, карточка) -> dict:
	"""Карточка курса: название, состояние, отметки для опроса; у курса без
	релиза — цели анонса."""
	анонс = bool(карточка.published and карточка.upcoming)
	отметки = authoring.course_revision(course=course)["data"]
	return {
		"id": course,
		"title": карточка.title,
		"summary": карточка.short_introduction,
		"published": bool(карточка.published),
		"upcoming": анонс,
		"status": _статус(bool(карточка.published), анонс),
		"url": адрес(course),
		"revision": отметки["revision"],
		"notes_revision": отметки["notes_revision"],
		"announce_objectives": None
		if карточка.active_release
		else announcements.строки(карточка.get(announcements.ПОЛЕ_ЦЕЛЕЙ)),
		"notes_attention": 0,
		"testers_count": 0,
	}


def _релиз(course: str, данные: dict) -> dict:
	"""Релиз из `course_release`, разложенный для отрисовки: номера и цвета
	глав, уроки внутри глав, место заметки и его подпись у каждой части."""
	уроки = {у["key"]: у for у in данные["lessons"]}
	документ = данные["document"]
	разделы = {р["key"]: р for р in (документ or {}).get("sections", [])}
	номер = 0
	for индекс, глава in enumerate(данные["chapters"]):
		глава["number"] = индекс + 1
		глава["color"] = индекс % ЦВЕТОВ_ГЛАВ + 1
		_место(глава, f"chapter.{глава['key']}", f"Глава {глава['number']} «{глава['title']}»")
		глава["lessons"] = [уроки[ключ] for ключ in глава["lessons"]]
		for урок in глава["lessons"]:
			номер += 1
			_урок(course, урок, номер, глава, разделы)
	for раздел in разделы.values():
		_место(раздел, f"section.{раздел['key']}", f"Документ · раздел «{раздел['title']}»")
		раздел["lessons"] = [
			{"number": у["number"], "title": у["title"], "url": у["url"]}
			for у in данные["lessons"]
			if раздел["key"] in у["sections"]
		]
	карточка = данные["course"]
	return {
		**карточка,
		"published_by_name": _имя(карточка["published_by"]),
		"chapters": данные["chapters"],
		"lessons": данные["lessons"],
		"document": документ,
		"notes": {},
	}


def _урок(course: str, урок: dict, номер: int, глава: dict, разделы: dict) -> None:
	"""Урок релиза для отрисовки: номер, адрес, места заметок целей, пунктов и вопросов."""
	подпись = f"Урок {номер} «{урок['title']}»"
	урок.update(
		number=номер, url=адрес(course, урок["key"]), chapter_title=глава["title"], color=глава["color"]
	)
	_место(урок, f"lesson.{урок['key']}", подпись)
	цели = {}
	for цель in урок["objectives"]:
		цели[цель["key"]] = цель["text"]
		_место(цель, f"objective.{цель['key']}", f"{подпись} · цель «{коротко(цель['text'])}»")
		for пункт in цель["goals"]:
			_место(пункт, f"goal.{урок['key']}/{пункт['key']}", f"{подпись} · пункт «{пункт['title']}»")
	for вопрос in урок["questions"]:
		_место(вопрос, f"question.{вопрос['key']}", f"{подпись} · вопрос «{коротко(вопрос['text'])}»")
		вопрос["objective_text"] = цели.get(вопрос["objective"])
		вопрос["answer"] = next(
			(вариант["text"] for вариант in вопрос["options"] if вариант["key"] == вопрос["correct"]), None
		)
	урок["section_titles"] = [
		разделы[ключ]["title"] if ключ in разделы else ключ for ключ in урок["sections"]
	]
	if урок["homework"]:
		урок["homework"]["answer_mode_text"] = РЕЖИМЫ_ОТВЕТА.get(
			урок["homework"]["answer_mode"], урок["homework"]["answer_mode"]
		)
	урок["open_notes"] = 0


def _место(часть: dict, target: str, подпись: str) -> None:
	часть.update(target=target, label=подпись, anchor=якорь(target))


def _урок_целиком(course: str, ключ: str, релиз: dict, заметки: list[dict]) -> dict | None:
	"""Урок релиза с пакетом агента и соседями; ключа нет в релизе — `None`.

	Пакет агента — только здесь: `course_release` отдаёт его лишь по уроку.
	"""
	уроки = релиз["lessons"]
	место = next((индекс for индекс, у in enumerate(уроки) if у["key"] == ключ), None)
	if место is None:
		return None
	урок = уроки[место]
	агент = authoring.course_release(course=course, lesson=ключ)["data"]["agent"]
	пункты = {п["key"]: п["title"] for ц in урок["objectives"] for п in ц["goals"]}
	разделы = {р["key"]: р["title"] for р in (релиз["document"] or {}).get("sections", [])}
	рамка = {часть: значение for часть, значение in агент.items() if часть != "lesson"}
	return {
		**урок,
		"agent": _части(агент["lesson"], урок, пункты, разделы),
		"agent_target": f"agent.lesson.{ключ}",
		"agent_label": f"{урок['label']} · пакет агента",
		"agent_anchor": якорь(f"agent.lesson.{ключ}"),
		"frame": _части(рамка, None, {}, {}),
		"notes_index": _указатель(заметки, ключ),
		"prev": _сосед(уроки, место - 1),
		"next": _сосед(уроки, место + 1),
	}


def _части(пакет: dict, урок: dict | None, пункты: dict[str, str], разделы: dict[str, str]) -> list[dict]:
	"""Части пакета агента для отрисовки — как есть, по порядку пакета.

	Форму пакета задаёт компилятор курса, и страница её не проверяет: строка
	— текстом, словарь — записями «ключ — значение», список — записью на
	элемент, прочее — JSON. У пунктов (`items`) — место заметки
	`agent.item.<урок>/<пункт>` и название пункта цели, у разделов —
	название раздела документа. `урок` — `None` у рамки курса: мест пунктов
	у неё нет.
	"""
	собранное = []
	for имя, значение in пакет.items():
		часть = {"name": имя, "title": ЧАСТИ_ПАКЕТА.get(имя, имя), "text": None, "entries": None}
		if isinstance(значение, str):
			часть["text"] = значение
		elif isinstance(значение, dict):
			названия = пункты if имя == "items" else разделы if имя == "sections" else {}
			часть["entries"] = [
				_запись(ключ, з, названия.get(ключ), урок if имя == "items" else None)
				for ключ, з in значение.items()
			]
		elif isinstance(значение, list):
			часть["entries"] = [_запись(None, з, None, None) for з in значение]
		else:
			часть["text"] = _json(значение)
		собранное.append(часть)
	return собранное


def _запись(ключ: str | None, значение, название: str | None, урок: dict | None) -> dict:
	"""Запись части пакета; `урок` — у пункта пакета: место его заметки."""
	запись = {
		"key": ключ,
		"title": название,
		"text": значение if isinstance(значение, str) else _json(значение),
		"target": None,
	}
	if урок is not None:
		_место(
			запись,
			f"agent.item.{урок['key']}/{ключ}",
			f"{урок['label']} · пакет агента · пункт «{название or ключ}»",
		)
	return запись


def _json(значение) -> str:
	return json.dumps(значение, ensure_ascii=False, indent=2)


def _сосед(уроки: list[dict], индекс: int) -> dict | None:
	if 0 <= индекс < len(уроки):
		урок = уроки[индекс]
		return {"key": урок["key"], "title": урок["title"], "number": урок["number"], "url": урок["url"]}
	return None


def _история(релизы: list[dict]) -> list[dict]:
	"""История релизов из `course_releases` с именами публиковавших."""
	for релиз in релизы:
		релиз["published_by_name"] = _имя(релиз["published_by"])
	return релизы


def _имя(пользователь: str | None) -> str | None:
	return frappe.utils.get_fullname(пользователь) if пользователь else None


# --- заметки ---


def _вид(target: str) -> str | None:
	"""Вид места заметки; адрес не разбирается — `None`."""
	try:
		return notes.разобрать_адрес(target)["kind"]
	except Отказ:
		return None


def _заметки(course: str) -> list[dict]:
	"""Заметки курса — ровно то, что получает агент в `list_notes`, плюс
	ссылка на место: экран курса, страница урока или карточка в очереди."""
	заметки = authoring.list_notes(course=course)["data"]["notes"]
	for з in заметки:
		з["anchor"] = якорь(з["target"])
		вид = None if з["missing"] else _вид(з["target"])
		if вид in МЕСТА_КУРСА:
			з["url"] = f"{адрес(course)}#{з['anchor']}"
		elif вид in МЕСТА_УРОКА and з["lesson_key"]:
			з["url"] = f"{адрес(course, з['lesson_key'])}#{з['anchor']}"
		else:
			# Места нет в релизе, или его нет ни на одной странице кабинета:
			# рамка пакета и узел карты.
			з["url"] = f"{адрес(course)}&view={ЗАМЕТКИ}#note-card-{з['id']}"
	return заметки


def _заметки_релиза(релиз: dict, заметки: list[dict]) -> None:
	"""Заметки по местам и открытые у каждого урока."""
	по_местам: dict[str, list[dict]] = {}
	for з in заметки:
		if not з["missing"]:
			по_местам.setdefault(з["target"], []).append(з)
	релиз["notes"] = по_местам
	открытые = Counter(з["lesson_key"] for з in заметки if з["status"] != "accepted" and з["lesson_key"])
	for урок in релиз["lessons"]:
		урок["open_notes"] = открытые[урок["key"]]


def _указатель(заметки: list[dict], ключ: str) -> list[dict]:
	"""Заметки мест урока, сначала то, что ждёт человека."""
	указатель = [з for з in заметки if з["lesson_key"] == ключ]
	указатель.sort(key=lambda з: ГРУППЫ_ОЧЕРЕДИ.index(notes.группа(з["status"], з["waiting_on"])))
	return указатель


def _очередь(заметки: list[dict]) -> dict[str, list[dict]]:
	"""Очередь по тому, что ждёт человека: проверить сделанное, ответить
	агенту, ждать агента, принятые."""
	очередь: dict[str, list[dict]] = {группа: [] for группа in ГРУППЫ_ОЧЕРЕДИ}
	for з in заметки:
		очередь[notes.группа(з["status"], з["waiting_on"])].append(з)
	return очередь


def get_context(context):
	context.no_breadcrumbs = True
	шапка_платформы(context)
	context.update(
		сведения(
			frappe.session.user,
			frappe.form_dict.get("course"),
			frappe.form_dict.get("lesson"),
			frappe.form_dict.get("view"),
		)
	)
	подписи = {ЗАМЕТКИ: "заметки", ИСТОРИЯ: "история релизов", ТЕСТЕРЫ: "тестеры"}
	if context.lesson:
		context.title = f"{context.lesson['title']} — кабинет автора"
	elif context.course and context.view in подписи:
		context.title = f"{context.course['title']} — {подписи[context.view]} — кабинет автора"
	elif context.course:
		context.title = f"{context.course['title']} — кабинет автора"
	else:
		context.title = "Кабинет автора"
	if context.release:
		# Заметки и тестеры пишутся с этой страницы whitelisted-методами, а
		# POST без токена Frappe отклоняет.
		context.csrf_token = frappe.sessions.get_csrf_token()
	return context
