# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Кабинет автора: действующий релиз курса, заметки по его ключам, тестеры.

Курс компилируется вне платформы и правится только новым релизом, поэтому
кабинет — зеркало, а не редактор: человек смотрит релиз и ставит заметки на
его места, агент куратора правит курс и публикует новый релиз. Данные — из
тех же авторских методов, что отвечают агенту (`course_release`,
`list_notes`, `course_releases`, `course_testers`): второй путь к ним
разошёлся бы с первым молча.

Исключение — счёт заметок в списке курсов (`_заметки_списка`): осознанный
второй путь ради бюджета запросов. `list_notes` на каждый курс стоил бы
выборок на строку; здесь — две выборки на весь список, а правило «чей ход» —
то же `notes.ждёт`. Считаются заметки только у курсов с релизом: у курса без
релиза вкладки «Заметки» нет, и архивные заметки в число не входят.

Второе исключение — карта урока (learning-services#560): авторского метода,
который отдаёт карту, нет, и срез урока читается из кэша снимка релиза
(`releases.lesson_map`). Показывается она тем же авторским ролям, что и
весь кабинет.

Здесь только раскладка для отрисовки. Шаблон кабинета экранирует каждый
вывод: ключи релиза и пакета агента — произвольные строки.
"""

import hashlib
import json
from collections import Counter
from functools import cached_property
from urllib.parse import quote

import frappe

from lms_frappe_app.agent_learning import announcements, notes
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases import lesson_map, places
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
	"diagrams": "Схемы урока",
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
		"course_anchor": якорь("course"),
		"frame_anchor": якорь("agent.frame"),
		"frame": [],
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
	карта = _Карта(релиз)
	_проверка_публикации(релиз, карта)
	заметки = _заметки(course, карта)
	_заметки_релиза(релиз, заметки)
	курс["notes_attention"] = sum(з["waiting_on"] == "author" for з in заметки)
	# Число тестеров — на ярлыке вкладки, поэтому и на других вкладках.
	курс["testers_count"] = frappe.db.count("LMS Enrollment", {"course": course, "agent_tester": 1})
	if lesson:
		основа["lesson"] = _урок_целиком(course, lesson, релиз, заметки, карта)
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
	else:
		основа["frame"] = _рамка(course, релиз)
	return основа


# --- список курсов ---


def _курсы(курсы: list[dict]) -> list[dict]:
	"""Курсы со сводкой: версия действующего релиза, открытые заметки, тестеры.

	`Why:` сводка — выборками на весь список, а не на курс: курсов на
	платформе десятки, и запрос на строку рос бы вместе с ними.
	"""
	ид = [курс["id"] for курс in курсы]
	заметки = _заметки_списка([курс["id"] for курс in курсы if курс["release"]])
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
	"""Якорь места заметки на странице: `n-` и начало sha1 адреса места.

	`Why:` ключи релиза — произвольные строки. Якорь из них самих понёс бы в
	`id` и во фрагмент ссылки любые символы, а замена точек и косых сводила бы
	разные места в один `id` (`objective.a.b` и `objective.a-b`). Хеш
	безопасен по символам и различает места.
	"""
	return "n-" + hashlib.sha1(target.encode()).hexdigest()[:16]


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
		"findings": данные["findings"],
		"changes": данные["changes"],
		"findings_elsewhere": [],
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
	урок["findings"] = []
	урок["changed_paths"] = []


def _место(часть: dict, target: str, подпись: str) -> None:
	часть.update(target=target, label=подпись, anchor=якорь(target))


def _урок_целиком(course: str, ключ: str, релиз: dict, заметки: list[dict], карта: "_Карта") -> dict | None:
	"""Урок релиза с пакетом агента, картой урока и соседями; ключа нет в релизе — `None`.

	Пакет агента — только здесь: `course_release` отдаёт его лишь по уроку.
	Карта урока — тоже: её срез читается из снимка (`lesson_map`).
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
	карта_урока = _карта_урока(карта.срезы.get(ключ))
	места = _места_карты(карта_урока)
	находки = [{**н, "anchor": _якорь_находки(н["target"], урок, места)} for н in урок["findings"]]
	у_узлов: dict[str, list[dict]] = {}
	for н in находки:
		if н["target"] != ключ:
			у_узлов.setdefault(н["target"], []).append(н)
	return {
		**урок,
		"findings": находки,
		"findings_by_node": у_узлов,
		"agent": _части(агент["lesson"], урок, пункты, разделы),
		"agent_target": f"agent.lesson.{ключ}",
		"agent_label": f"{урок['label']} · пакет агента",
		"agent_anchor": якорь(f"agent.lesson.{ключ}"),
		"frame": _части(рамка, None, {}, {}),
		"map": карта_урока,
		# Версию сменила новая публикация, пока страница собиралась.
		"map_unavailable": not карта.доступна,
		"notes_index": _указатель(заметки, ключ),
		"prev": _сосед(уроки, место - 1),
		"next": _сосед(уроки, место + 1),
	}


def _рамка(course: str, релиз: dict) -> list[dict]:
	"""Рамка курса для вкладки «Релиз»: та же, что на странице урока.

	`Why:` рамка общая для всех уроков, а показывалась только внутри
	страницы какого-нибудь урока — с курса её было не найти
	(learning-services#559). `course_release` отдаёт пакет агента только по
	уроку, поэтому берётся по первому уроку релиза, без части урока.
	"""
	if not релиз["lessons"]:
		return []
	агент = authoring.course_release(course=course, lesson=релиз["lessons"][0]["key"])["data"]["agent"]
	return _части({часть: значение for часть, значение in агент.items() if часть != "lesson"}, None, {}, {})


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
		elif имя == "diagrams" and isinstance(значение, list):
			# HTML схемы — до 100 КБ: на странице — что видит агент, без страницы автора.
			часть["entries"] = [_схема(з) for з in значение]
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


def _схема(схема) -> dict:
	"""Схема урока без HTML: название, описание, когда показать и размер страницы."""
	if not isinstance(схема, dict):
		return _запись(None, схема, None, None)
	строки = [схема.get("description") or ""]
	if схема.get("when"):
		строки.append(f"Когда показать: {схема['when']}")
	строки.append(f"HTML: {len(str(схема.get('html') or '').encode('utf-8'))} байт")
	return _запись(схема.get("key"), "\n".join(строки), схема.get("title"), None)


def _json(значение) -> str:
	return json.dumps(значение, ensure_ascii=False, indent=2)


# --- карта урока ---


def _проверка_публикации(релиз: dict, карта: "_Карта") -> None:
	"""Находки проверки карты и изменённые пути публикации — по урокам.

	Находка урока — с целью-уроком или узлом его среза карты; находки без
	цели или с целью вне уроков — `findings_elsewhere` релиза. Путь урока —
	`lesson_map.путь_урока`. Срезы карты читаются, только если есть что
	раскладывать.
	"""
	находки = релиз["findings"] or []
	пути = (релиз["changes"] or {}).get("paths") or []
	if not находки and not пути:
		return
	разложенные = set()
	for урок in релиз["lessons"]:
		ключ, узлы = урок["key"], карта.узлы[урок["key"]]
		урок["findings"] = [н for н in находки if н["target"] == ключ or н["target"] in узлы]
		разложенные |= {id(н) for н in урок["findings"]}
		срез = карта.срезы.get(ключ)
		урок["changed_paths"] = [п for п in пути if lesson_map.путь_урока(п["path"], ключ, срез, карта.ключи)]
	релиз["findings_elsewhere"] = [н for н in находки if id(н) not in разложенные]


class _Карта:
	"""Срезы карты релиза по урокам (`lesson_map`) — читаются, только когда
	понадобились: на странице урока, для заметок на узлах карты."""

	def __init__(self, релиз: dict):
		self.релиз = релиз

	@cached_property
	def данные(self) -> dict | None:
		"""`lesson_map.срезы` релиза; `None` — содержимое версии освобождено."""
		return lesson_map.срезы(self.релиз["release"], self.релиз["digest"])

	@property
	def доступна(self) -> bool:
		return self.данные is not None

	@property
	def срезы(self) -> dict[str, dict]:
		return (self.данные or {}).get("lessons", {})

	@property
	def ключи(self) -> dict[str, list[str]]:
		return (self.данные or {}).get("keys", {})

	@cached_property
	def узлы(self) -> dict[str, set[str]]:
		"""Ключ урока релиза → ключи узлов его среза карты, по порядку релиза."""
		return {у["key"]: lesson_map.узлы(self.срезы.get(у["key"])) for у in self.релиз["lessons"]}

	def уроки_узла(self, узел: str) -> list[str]:
		"""Уроки релиза, в срезе карты которых есть узел, — по порядку релиза."""
		return [урок for урок, узлы in self.узлы.items() if узел in узлы]


#: Поля узлов карты, которые страница раскладывает сама; прочие поля узла —
#: записями «ключ — значение»: форму карты задаёт компилятор курса.
ПОЛЯ_РЕШЕНИЯ = frozenset({"text", "serves", "relies_on", "variants", "result", "uses", "note"})
ПОЛЯ_ВАРИАНТА = frozenset({"text", "gives", "costs", "tempts", "unfit_when", "never", "errors"})
ПОЛЯ_ОШИБКИ = frozenset({"when", "causes", "consequence"})
ПОЛЯ_РЕЗУЛЬТАТА = frozenset({"text", "sample", "requirements"})
ПОЛЯ_ТРЕБОВАНИЯ = frozenset({"text", "relies_on", "violation"})
ПОЛЯ_СИТУАЦИИ = frozenset({"text", "role", "conditions", "sets", "traps"})
ПОЛЯ_ТЕЗИСА = frozenset({"text", "kind", "origin", "refutes", "relies_on"})
ПОЛЯ_ЗАБЛУЖДЕНИЯ = frozenset({"text", "note"})

#: Виды причин ошибки словами; вид, которого здесь нет, подписан своим кодом.
ПРИЧИНЫ = {"thinks_otherwise": "думает иначе", "not_know": "не знает", "skill": "не хватает навыка"}


def _карта_урока(срез: dict | None) -> dict | None:
	"""Срез карты урока для отрисовки: решения с вариантами, ошибками и
	результатами, ситуации практики и зачёта, тезисы и заблуждения. У каждого
	узла с текстом — место заметки `map.<узел>`. Урока в карте нет — `None`."""
	if срез is None:
		return None
	урок = срез["lesson"]
	здесь = lesson_map.узлы(срез)
	тексты = {}
	for коллекция in ("refs", "goals", "theses", "misconceptions"):
		тексты.update({ключ: places.текст_узла(узел) for ключ, узел in срез[коллекция].items()})
	for решение in срез["decisions"].values():
		тексты.update({к: places.текст_узла(в) for к, в in _словари(решение.get("variants")).items()})
	# Ссылка на узел этой страницы — с якорем его места.
	тексты = {к: {"text": т, "here": к in здесь and bool(т)} for к, т in тексты.items()}
	return {
		"decisions": [_решение(к, р, срез, тексты) for к, р in срез["decisions"].items()],
		"situations": [_ситуация(к, срез["situations"][к], тексты) for к in _ключи(урок.get("situations"), срез)],
		"quiz": [_ситуация(к, срез["situations"][к], тексты) for к in _ключи(урок.get("quiz"), срез)],
		"theses": [_тезис(к, т, тексты) for к, т in срез["theses"].items()],
		"misconceptions": [
			{**_узел(к, з, ПОЛЯ_ЗАБЛУЖДЕНИЯ), "note": _строка(з.get("note"))}
			for к, з in срез["misconceptions"].items()
		],
	}


def _якорь_находки(цель: str, урок: dict, места: set[str]) -> str | None:
	"""Куда ведёт находка на странице урока: к уроку или к месту узла карты;
	узла без места на странице нет — никуда."""
	if цель == урок["key"]:
		return урок["anchor"]
	return якорь(f"map.{цель}") if f"map.{цель}" in места else None


def _места_карты(карта: dict | None) -> set[str]:
	"""Места заметок узлов карты урока, которые есть на странице."""
	if not карта:
		return set()
	узлы = [*карта["situations"], *карта["quiz"], *карта["theses"], *карта["misconceptions"]]
	for решение in карта["decisions"]:
		узлы.append(решение)
		for вариант in решение["variants"]:
			узлы += [вариант, *вариант["errors"]]
		if решение["result"]:
			узлы += [решение["result"], *решение["result"]["requirements"]]
	return {у["target"] for у in узлы if у["target"]}


def _ключи(значение, срез: dict) -> list[str]:
	"""Ключи ситуаций урока карты, которые есть в срезе."""
	return [к for к in dict.fromkeys(значение if isinstance(значение, list) else []) if к in срез["situations"]]


def _узел(ключ: str, узел: dict, известные: frozenset) -> dict:
	"""Общее у узла карты: ключ, текст, место заметки (у узла с текстом) и
	прочие поля записями."""
	текст = places.текст_узла(узел)
	собранное = {
		"key": ключ,
		"text": _строка(узел.get("text")),
		"target": None,
		"extra": [{"key": к, "text": _строка(з)} for к, з in узел.items() if к not in известные],
	}
	if текст:
		_место(собранное, f"map.{ключ}", places.подпись_узла(текст))
	return собранное


def _решение(ключ: str, решение: dict, срез: dict, тексты: dict) -> dict:
	результат = решение.get("result")
	return {
		**_узел(ключ, решение, ПОЛЯ_РЕШЕНИЯ),
		"goal": _ссылка(решение.get("serves"), тексты),
		"note": _строка(решение.get("note")),
		"relies_on": _ссылки(решение.get("relies_on"), тексты),
		"uses": [т for т in map(_строка, решение["uses"]) if т] if isinstance(решение.get("uses"), list) else [],
		"variants": [_вариант(к, в, тексты) for к, в in _словари(решение.get("variants")).items()],
		"result": _результат(результат, срез["results"][результат], тексты)
		if isinstance(результат, str) and результат in срез["results"]
		else None,
		"result_key": _строка(результат),
	}


def _вариант(ключ: str, вариант: dict, тексты: dict) -> dict:
	return {
		**_узел(ключ, вариант, ПОЛЯ_ВАРИАНТА),
		**{поле: _строка(вариант.get(поле)) for поле in ("gives", "costs", "tempts", "unfit_when")},
		"never": вариант.get("never") is True,
		"errors": [
			{
				**_узел(к, о, ПОЛЯ_ОШИБКИ),
				"when": _строка(о.get("when")),
				"consequence": _строка(о.get("consequence")),
				"causes": _причины(о.get("causes"), тексты),
			}
			for к, о in _словари(вариант.get("errors")).items()
		],
	}


def _результат(ключ: str, результат: dict, тексты: dict) -> dict:
	требования = []
	for к, т in _словари(результат.get("requirements")).items():
		нарушение = т.get("violation") if isinstance(т.get("violation"), dict) else {}
		требования.append(
			{
				**_узел(к, т, ПОЛЯ_ТРЕБОВАНИЯ),
				"relies_on": _ссылки(т.get("relies_on"), тексты),
				"violation": _строка(нарушение.get("text")),
				"consequence": _строка(нарушение.get("consequence")),
				"causes": _причины(нарушение.get("causes"), тексты),
			}
		)
	return {**_узел(ключ, результат, ПОЛЯ_РЕЗУЛЬТАТА), "sample": _строка(результат.get("sample")), "requirements": требования}


def _ситуация(ключ: str, ситуация: dict, тексты: dict) -> dict:
	условия = ситуация.get("conditions")
	наборы = []
	for набор in ситуация.get("sets") if isinstance(ситуация.get("sets"), list) else []:
		набор = набор if isinstance(набор, dict) else {"": набор}
		наборы.append(
			{
				"decision": _ссылка(набор.get("decision"), тексты),
				"expected": _ссылка(набор.get("expected"), тексты),
				"about": _строка(набор.get("about")),
				"deciding": _строка(набор.get("deciding")),
				"extra": [
					{"key": к, "text": _строка(з)}
					for к, з in набор.items()
					if к not in ("decision", "expected", "about", "deciding", "key")
				],
			}
		)
	return {
		**_узел(ключ, ситуация, ПОЛЯ_СИТУАЦИИ),
		"role": _строка(ситуация.get("role")),
		"conditions": [_строка(у) for у in условия] if isinstance(условия, list) else [_строка(условия)] if условия else [],
		"sets": наборы,
		"traps": _ссылки(ситуация.get("traps"), тексты),
	}


def _тезис(ключ: str, тезис: dict, тексты: dict) -> dict:
	происхождение = тезис.get("origin") if isinstance(тезис.get("origin"), dict) else {}
	цитаты = [происхождение] if "quote" in происхождение else []
	if isinstance(происхождение.get("quotes"), list):
		цитаты += [ц for ц in происхождение["quotes"] if isinstance(ц, dict)]
	return {
		**_узел(ключ, тезис, ПОЛЯ_ТЕЗИСА),
		"kind": _строка(тезис.get("kind")),
		"origin": _строка(происхождение.get("kind")),
		"quotes": [{"quote": _строка(ц.get("quote")), "source": _строка(ц.get("page"))} for ц in цитаты],
		"origin_extra": [
			{"key": к, "text": _строка(з)}
			for к, з in происхождение.items()
			if к not in ("kind", "quote", "page", "quotes")
		]
		+ ([{"key": "origin", "text": _строка(тезис["origin"])}] if "origin" in тезис and not происхождение else []),
		"refutes": _ссылки(тезис.get("refutes"), тексты),
		"relies_on": _ссылки(тезис.get("relies_on"), тексты),
	}


def _причины(причины, тексты: dict) -> list[dict]:
	"""Причины ошибки или нарушения: вид словами и узел, на который она указывает."""
	собранное = []
	for причина in причины if isinstance(причины, list) else []:
		if not isinstance(причина, dict):
			собранное.append({"kind": _строка(причина), "ref": None})
			continue
		вид = причина.get("kind")
		узел = next((причина[п] for п in ("misconception", "thesis") if isinstance(причина.get(п), str)), None)
		собранное.append(
			{"kind": ПРИЧИНЫ.get(вид, _строка(вид)) if вид is not None else None, "ref": _ссылка(узел, тексты)}
		)
	return собранное


def _ссылки(значение, тексты: dict) -> list[dict]:
	"""Ссылки списком — тексты коротко: узел целиком — в своём блоке или уроке."""
	ссылки = [_ссылка(к, тексты) for к in значение if isinstance(к, str)] if isinstance(значение, list) else []
	return [{**с, "text": коротко(с["text"]) if с["text"] else None} for с in ссылки]


def _ссылка(ключ, тексты: dict) -> dict | None:
	"""Ссылка на узел карты: ключ, текст (узла нет в срезе — `None`) и якорь
	его места, если узел на этой странице."""
	if not isinstance(ключ, str):
		return None
	узел = тексты.get(ключ) or {"text": None, "here": False}
	return {"key": ключ, "text": узел["text"], "anchor": якорь(f"map.{ключ}") if узел["here"] else None}


def _словари(значение) -> dict:
	return {к: в for к, в in значение.items() if isinstance(в, dict)} if isinstance(значение, dict) else {}


def _строка(значение) -> str | None:
	"""Значение поля узла карты текстом: строка как есть, список строк — через
	запятую, прочее — JSON; пусто — `None`."""
	if значение is None or значение == "":
		return None
	if isinstance(значение, str):
		return значение
	if isinstance(значение, list) and all(isinstance(з, str) for з in значение):
		return ", ".join(значение)
	if isinstance(значение, bool):
		return "да" if значение else "нет"
	return _json(значение)


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


def _заметки(course: str, карта: _Карта) -> list[dict]:
	"""Заметки курса — ровно то, что получает агент в `list_notes`, плюс
	ссылка на место: экран курса, страница урока или карточка в очереди — и
	уроки, на страницах которых место показано (`lessons`).

	Узел карты ключа урока не хранит: он бывает в срезах нескольких уроков
	(тезис, заблуждение). Его уроки — по срезам карты, ссылка — на первый.
	"""
	заметки = authoring.list_notes(course=course)["data"]["notes"]
	for з in заметки:
		з["anchor"] = якорь(з["target"])
		вид = None if з["missing"] else _вид(з["target"])
		з["lessons"] = [з["lesson_key"]] if з["lesson_key"] else []
		if вид == "map":
			з["lessons"] = карта.уроки_узла(notes.разобрать_адрес(з["target"])["key"])
		if вид in МЕСТА_КУРСА:
			з["url"] = f"{адрес(course)}#{з['anchor']}"
		elif (вид in МЕСТА_УРОКА and з["lesson_key"]) or (вид == "map" and з["lessons"]):
			з["url"] = f"{адрес(course, з['lessons'][0])}#{з['anchor']}"
		else:
			# Места нет в релизе, или его нет ни на одной странице кабинета:
			# рамка пакета и узел карты вне уроков.
			з["url"] = f"{адрес(course)}&view={ЗАМЕТКИ}#note-card-{з['id']}"
	return заметки


def _заметки_релиза(релиз: dict, заметки: list[dict]) -> None:
	"""Заметки по местам и открытые у каждого урока."""
	по_местам: dict[str, list[dict]] = {}
	for з in заметки:
		if not з["missing"]:
			по_местам.setdefault(з["target"], []).append(з)
	релиз["notes"] = по_местам
	открытые = Counter(урок for з in заметки if з["status"] != "accepted" for урок in з["lessons"])
	for урок in релиз["lessons"]:
		урок["open_notes"] = открытые[урок["key"]]


def _указатель(заметки: list[dict], ключ: str) -> list[dict]:
	"""Заметки мест урока, сначала то, что ждёт человека."""
	указатель = [з for з in заметки if ключ in з["lessons"]]
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
	# Кабинет автора — русский при любом языке, и шапка с подвалом тоже.
	шапка_платформы(context, "ru")
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
