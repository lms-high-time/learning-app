# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Кабинет автора: курс таким, каким его собрал агент куратора.

Зеркало, а не редактор: пишет только агент, через авторские методы, а человек
здесь смотрит собранное — структуру и наполненность, урок целиком, документ
курса — и говорит правки агенту. Данные берутся из тех же методов, что
отвечают агенту (`course_draft`, `get_lesson`): второй путь к ним разошёлся
бы с первым молча.

Здесь только раскладка для отрисовки. Норм методики — сколько знаков, сколько
вопросов — страница не знает и не показывает: это закрытая часть (спека, §3).
"""

from datetime import timedelta
from urllib.parse import quote

import frappe
from frappe.utils import get_datetime, md_to_html, now_datetime, sanitize_html

from lms_frappe_app.agent_learning import diffs, directives, normalizer, notes, snapshots
from lms_frappe_app.agent_learning.artifacts import overlay
from lms_frappe_app.agent_learning.errors import УРОК_НЕ_НАЙДЕН, Отказ
from lms_frappe_app.api import authoring, контракт
from lms_frappe_app.site_navigation import шапка_платформы

no_cache = 1

СТРАНИЦА = "/author"
МЕТОД_РЕВИЗИИ = "lms_frappe_app.api.authoring.course_revision"
МЕТОДЫ_ЗАМЕЧАНИЙ = {
	"add": "lms_frappe_app.api.authoring.add_note",
	"reply": "lms_frappe_app.api.authoring.reply_note",
	"status": "lms_frappe_app.api.authoring.set_note_status",
}

#: Цветов глав в палитре страницы; дальше они идут по кругу.
ЦВЕТОВ_ГЛАВ = 5

#: Вкладки экрана курса: собранное, сверка с картой декомпозиции, замечания.
СБОРКА, КАРТА, ЗАМЕЧАНИЯ = "build", "map", "notes"

#: Порядок групп очереди: сначала то, что ждёт человека.
ГРУППЫ_ОЧЕРЕДИ = ("check", "question", "agent", "accepted")

#: Сеанс чтения: заход в урок позже этого после прошлого — новый визит.
#: Внутри сеанса отметки «изменено» считаются от визита до него, так что
#: перезагрузка и возврат к уроку их не сбрасывают.
СЕАНС = timedelta(minutes=30)

#: Поля директив по-человечески — в разделах урока и в разнице по местам.
ПОДПИСИ_ПОЛЕЙ = {
	"teaching_directive": "Как вести занятие",
	"objectives": "Цели",
	"probing_questions": "Вопросы к проекту",
	"success_criteria": "Признаки успеха",
	"common_misconceptions": "Частые заблуждения",
	"student_profile": "Кто ученик",
	"glossary": "Глоссарий",
	"remember_about_student": "Что запоминать об ученике",
}

#: Поля директивы урока в порядке, в каком их читает агент ученика; второе
#: значение — список ли это по строке на пункт.
ПОЛЯ_УРОКА = (
	("teaching_directive", False),
	("objectives", True),
	("probing_questions", True),
	("success_criteria", True),
	("common_misconceptions", True),
)
ПОЛЯ_КУРСА = (
	("teaching_directive", False),
	("objectives", True),
	("student_profile", False),
	("glossary", True),
	("remember_about_student", True),
)


def сведения(
	пользователь: str, course: str | None = None, lesson: str | None = None, view: str | None = None
) -> dict:
	"""Что показать на странице этому пользователю.

	Без `course` — все курсы: они общие, видимость по роли, а не по
	авторству. С `course` — экран курса: вкладка сборки или, с `view=map`,
	сверка с картой декомпозиции; с `lesson` — урок. Неизвестный курс или урок
	не из этого курса дают пометку, а не ошибку: ссылку могли прислать до того,
	как агент урок перенёс или удалил.
	"""
	основа = {
		"is_guest": пользователь == "Guest",
		"login_url": f"/login?redirect-to={СТРАНИЦА}",
		"page_url": СТРАНИЦА,
		"revision_method": МЕТОД_РЕВИЗИИ,
		"allowed": True,
		"courses": [],
		"course": None,
		"lesson": None,
		"view": СБОРКА,
		"map_check": None,
		"map_notes": {},
		"notes_queue": None,
		"note_methods": МЕТОДЫ_ЗАМЕЧАНИЙ,
		"field_labels": ПОДПИСИ_ПОЛЕЙ,
		"seen_method": "lms_frappe_app.www.author.mark_lesson_seen",
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

	черновик = authoring.course_draft(course=course)
	if not черновик["ok"]:
		основа["missing"] = True
		return основа
	основа["course"] = _курс(черновик["data"])
	замечания = _замечания(course)
	_замечания_курса(основа["course"], замечания)
	сверка = _сверка(course)
	изменены = _изменены_после_визита(пользователь, course)
	for глава in основа["course"]["chapters"]:
		for урок in глава["lessons"]:
			урок["map_issues"] = len(_расхождения_урока(сверка, урок["id"]))
			урок["changed_since_visit"] = урок["id"] in изменены
	if lesson:
		основа["lesson"] = _урок(основа["course"], lesson)
		основа["missing"] = основа["lesson"] is None
		if основа["lesson"]:
			_замечания_урока(основа["lesson"], замечания)
			основа["lesson"]["changes"] = _изменения_урока(пользователь, course, lesson)
			основа["lesson"]["map_issues"] = _расхождения_урока(сверка, lesson)
			основа["lesson"]["map_url"] = (
				f"{адрес(course)}&view={КАРТА}&node={quote('lesson:' + lesson, safe='')}" if сверка["map"] else None
			)
	elif view == КАРТА:
		основа["view"] = КАРТА
		основа["map_check"] = сверка
		основа["map_notes"] = _по_местам(
			(з for з in замечания if з["target"].startswith("map.") and з["status"] != "accepted"),
			lambda з: з["target"].partition(".")[2],
		)
	elif view == ЗАМЕЧАНИЯ:
		основа["view"] = ЗАМЕЧАНИЯ
		основа["notes_queue"] = _очередь(замечания)
	return основа


def _курсы(курсы: list[dict]) -> list[dict]:
	"""Список курсов со сводкой внимания: сколько замечаний ждёт человека,
	сколько расхождений с картой, когда курс менялся. Два автора и много
	курсов — список сразу говорит, куда идти.

	Сверка с картой — только у курсов, у которых карта есть: она дороже
	остального, а без карты считать нечего.
	"""
	курсы_ид = [курс["id"] for курс in курсы]
	ждут = _ждут_автора(курсы_ид)
	с_картой = (
		set(frappe.get_all("Agent Course Map", filters={"course": ["in", курсы_ид]}, pluck="course"))
		if курсы_ид
		else set()
	)
	for курс in курсы:
		курс["notes_attention"] = ждут.get(курс["id"], 0)
		курс["map_discrepancies"] = (
			authoring.course_map_check(course=курс["id"])["data"]["counts"]["total"]
			if курс["id"] in с_картой
			else None
		)
		курс["revision"] = authoring.ревизия(курс["id"])
	return курсы


def _ждут_автора(курсы: list[str]) -> dict[str, int]:
	"""Сколько замечаний каждого курса ждёт человека — двумя запросами на
	весь список, а не парой на курс."""
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
	счёт: dict[str, int] = {}
	for з in открытые:
		ответы = [{"via": последние[з.name]}] if з.name in последние else []
		if notes.ждёт(з.status, з.via, ответы) == "author":
			счёт[з.course] = счёт.get(з.course, 0) + 1
	return счёт


@frappe.whitelist(methods=["POST"])
@контракт
def mark_lesson_seen(lesson: str) -> dict:
	"""Автор открыл урок: снимок его мест — база отметок «изменено» в
	следующий визит (lms-high-time/learning-services#271).

	Зовёт страница урока после загрузки: GET-страница в базу не пишет. Снимок
	снимает сервер. Заход после перерыва больше `СЕАНС` сдвигает базу: прошлый
	визит становится «визитом до сеанса».
	"""
	authoring._автор()
	course = frappe.db.get_value("Course Lesson", lesson, "course")
	if not course:
		raise Отказ(УРОК_НЕ_НАЙДЕН, "Урока нет", lesson=lesson)
	сейчас = now_datetime()
	снимок = snapshots.в_json(snapshots.снимок(course, lesson, "lesson"))
	имя = frappe.db.get_value("Agent Author Visit", {"author": frappe.session.user, "lesson": lesson})
	if имя:
		визит = frappe.get_doc("Agent Author Visit", имя)
		if сейчас - get_datetime(визит.last_at) > СЕАНС:
			визит.baseline, визит.baseline_at = визит.last, визит.last_at
		визит.last, визит.last_at = снимок, сейчас
		визит.save(ignore_permissions=True)
	else:
		frappe.get_doc(
			{
				"doctype": "Agent Author Visit",
				"author": frappe.session.user,
				"course": course,
				"lesson": lesson,
				"last": снимок,
				"last_at": сейчас,
			}
		).insert(ignore_permissions=True)
	return {"lesson": lesson, "seen_at": сейчас.isoformat()}


def _изменения_урока(пользователь: str, course: str, lesson: str) -> dict | None:
	"""Что изменилось в уроке с прошлого визита автора: разница по местам и
	сводка по разделам. Первый визит — без отметок."""
	визит = frappe.db.get_value(
		"Agent Author Visit",
		{"author": пользователь, "lesson": lesson},
		["last", "last_at", "baseline", "baseline_at"],
		as_dict=True,
	)
	if not визит or not визит.last_at:
		return None
	if now_datetime() - get_datetime(визит.last_at) > СЕАНС:
		база, когда = визит.last, визит.last_at
	else:
		база, когда = визит.baseline, визит.baseline_at
	if not база:
		return None
	стало = snapshots.снимок(course, lesson, "lesson")
	итог = diffs.сравнить(snapshots.из_json(база), стало)
	if итог["state"] != "changed":
		return None
	прежние = snapshots.из_json(база)["places"]
	места = {
		м["target"]: {
			**м,
			"label": _подпись_места(м["target"], стало["places"].get(м["target"]) or прежние.get(м["target"])),
			# Места больше нет на странице — его разница показывается в сводке.
			"gone": м["target"] not in стало["places"],
		}
		for м in итог["places"]
	}
	return {"since": когда, "places": места, "summary": _сводка_изменений(места)}


def _сводка_изменений(места: dict) -> list[dict]:
	"""«материал, директива — 2 поля, квиз — 1 вопрос» — со ссылками на разделы."""
	сводка = []
	for вид, раздел, подпись, формы in (
		("material", "section-material", "материал", None),
		("directive", "section-directive", "директива", ("поле", "поля", "полей")),
		("question", "section-quiz", "квиз", ("вопрос", "вопроса", "вопросов")),
		("block", "section-blocks", "блоки документа", ("блок", "блока", "блоков")),
	):
		сколько = sum(адрес.partition(".")[0] == вид for адрес in места)
		if сколько:
			текст = подпись if not формы else f"{подпись} — {сколько} {_множ(сколько, формы)}"
			сводка.append({"anchor": раздел, "text": текст})
	return сводка


def _множ(n: int, формы: tuple[str, str, str]) -> str:
	if n % 10 == 1 and n % 100 != 11:
		return формы[0]
	if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
		return формы[1]
	return формы[2]


def _изменены_после_визита(пользователь: str, course: str) -> set[str]:
	"""Уроки, в которых что-то поменялось после последнего визита автора.
	Сравниваются снимки, а не время правок: отметка в таблице совпадает с тем,
	что покажет сам урок. Уроки, которые автор не открывал, не отмечаются —
	иначе новый курс весь стал бы «изменён»."""
	return {
		визит.lesson
		for визит in frappe.get_all(
			"Agent Author Visit", filters={"author": пользователь, "course": course}, fields=["lesson", "last"]
		)
		if snapshots.снимок(course, визит.lesson, "lesson") != snapshots.из_json(визит.last)
	}


def _расхождения_урока(сверка: dict, урок: str) -> list[dict]:
	"""Расхождения карты, которые касаются урока: про сам урок, про блок,
	который он собирает по плану или на деле, про узел карты с этим уроком.
	Блок, собираемый не тем уроком, касается обоих."""
	if not сверка["map"]:
		return []
	пары = сверка["matches"]
	урок_узла = {у["id"]: пары.get(у["lesson"]) for у in сверка["map"]["nodes"] if у["lesson"]}
	на_деле = {(б["artifact"], б["key"]): б["lesson"] for б in сверка["platform"]["blocks"]}
	по_плану = {(б["artifact"], б["key"]): пары.get(б["lesson"]) for б in сверка["map"]["blocks"] if б["lesson"]}

	def касается(р: dict) -> bool:
		if р.get("lesson") == урок:
			return True
		if р["group"] == "blocks":
			блок = (р["artifact"], р["key"])
			return урок in (р.get("expected"), р.get("actual"), на_деле.get(блок), по_плану.get(блок))
		if р["group"] == "integrity":
			return урок_узла.get(р.get("node")) == урок
		return False

	return [р for р in сверка["discrepancies"] if касается(р)]


def _замечания(course: str) -> list[dict]:
	"""Замечания курса — ровно то, что получает агент в `list_notes`, плюс
	ссылка на место: по ней из очереди открывается урок на нужном разделе.

	У сделанного — `changes`: разница места со снимком, который замечание
	запомнило, когда его ставили или возвращали. По ней «сделано» проверяют,
	не перечитывая раздел (lms-high-time/learning-services#271).
	"""
	замечания = authoring.list_notes(course=course)["data"]["notes"]
	снимки = dict(
		frappe.get_all(
			"Agent Author Note", filters={"course": course, "status": "done"}, fields=["name", "baseline"], as_list=True
		)
	)
	for з in замечания:
		if з["status"] == "done" and snapshots.бывает_снимок(з["target"]):
			з["changes"] = _правки(course, з, snapshots.из_json(снимки.get(з["id"])))
		з["anchor"] = якорь(з["target"])
		if з["target"].startswith("map."):
			з["url"] = f"{адрес(course)}&view={КАРТА}&node={quote(з['target'].partition('.')[2])}"
		elif з["lesson"] and not з["missing"]:
			з["url"] = f"{адрес(course, з['lesson'])}#{з['anchor']}"
		else:
			з["url"] = f"{адрес(course)}#{з['anchor']}"
	return замечания


def _правки(course: str, замечание: dict, было: dict | None) -> dict:
	стало = None if замечание["missing"] else snapshots.снимок(course, замечание["lesson"], замечание["target"])
	итог = diffs.сравнить(было, стало)
	for место in итог.get("places", []):
		снимок_места = ((стало or {}).get("places") or {}).get(место["target"]) or (
			((было or {}).get("places") or {}).get(место["target"])
		)
		место["label"] = _подпись_места(место["target"], снимок_места)
	return итог


def _подпись_места(target: str, снимок_места: dict | None) -> str:
	"""Место урока словами — для разницы замечания ко всему уроку."""
	вид, _, ключ = target.partition(".")
	первая = ((снимок_места or {}).get("text") or "").strip().split("\n")[0]
	if вид == "material":
		return "Материал"
	if вид == "directive":
		return "Директива · " + ПОДПИСИ_ПОЛЕЙ.get(ключ, ключ)
	if вид == "question":
		return f"Вопрос «{первая[:60]}{'…' if len(первая) > 60 else ''}»"
	if вид == "block":
		return f"Блок «{первая}»"
	return target


def якорь(target: str) -> str:
	"""Якорь места на странице: `directive.teaching_directive` →
	`note-directive-teaching_directive`."""
	return "note-" + target.replace(".", "-").replace("/", "-")


def _по_местам(замечания, ключ) -> dict[str, list[dict]]:
	собранное: dict[str, list[dict]] = {}
	for з in замечания:
		собранное.setdefault(ключ(з), []).append(з)
	return собранное


def _замечания_курса(курс: dict, замечания: list[dict]) -> None:
	"""Счётчики для вкладки и таблицы, замечания мест курса вне уроков."""
	курс["notes_attention"] = sum(з["waiting_on"] == "author" for з in замечания)
	курс["notes_revision"] = authoring.ревизия_замечаний(курс["id"])
	открытые = [з for з in замечания if з["status"] != "accepted"]
	блоки = _блоки_уроков(курс)
	for глава in курс["chapters"]:
		for урок in глава["lessons"]:
			урок["open_notes"] = sum(_на_уроке(з, урок["id"], блоки.get(урок["id"], set())) for з in открытые)
	курс["notes"] = _по_местам(filter(_на_курсе, замечания), lambda з: з["target"])


def _на_курсе(замечание: dict) -> bool:
	"""Место замечания — на экране курса: курс, сквозная директива, блок.

	Блок стоит у своего блока, даже если агент указал при нём урок: у блока
	одно место, и на странице урока его рисует тот же макрос, что и в сборке.
	"""
	вид = замечание["target"].partition(".")[0]
	return вид == "block" or (not замечание["lesson"] and вид != "map")


def _блоки_уроков(курс: dict) -> dict[str, set[str]]:
	"""Адреса блоков документа, которые собирает урок: `block.<документ>/<ключ>`."""
	блоки: dict[str, set[str]] = {}
	for документ in курс["artifacts"]:
		for блок in документ["blocks"]:
			if блок["lesson"]:
				блоки.setdefault(блок["lesson"], set()).add(f"block.{документ['artifact']}/{блок['key']}")
	return блоки


def _на_уроке(замечание: dict, урок: str, блоки: set[str]) -> bool:
	"""Замечание относится к странице урока: к его разделу или к блоку,
	который урок собирает."""
	вид = замечание["target"].partition(".")[0]
	return замечание["target"] in блоки or (замечание["lesson"] == урок and вид in notes.С_УРОКОМ)


def _замечания_урока(урок: dict, замечания: list[dict]) -> None:
	"""Замечания урока по местам и указатель: всё, что относится к уроку,
	сначала то, что ждёт человека.

	`here` — место замечания есть на странице. Нет его, когда агент убрал
	вопрос или поле директивы: такое замечание указатель ведёт в очередь.
	"""
	места = {"lesson", "material"}
	if урок["directive"]:
		места |= {f"directive.{поле['name']}" for поле in урок["directive"]["fields"]}
	if урок["quiz"]:
		места |= {f"question.{вопрос['id']}" for вопрос in урок["quiz"]["questions"]}
	блоки = {f"block.{блок['artifact']}/{блок['key']}" for блок in урок["blocks"]}
	места |= блоки

	урок["notes"] = _по_местам((з for з in замечания if з["lesson"] == урок["id"]), lambda з: з["target"])
	# Подпись места — без названия урока: на его странице оно в каждой
	# строке лишнее.
	название = f"Урок {урок['number']} «{урок['title']}»"

	def место(подпись: str) -> str:
		if подпись == название:
			return "Урок целиком"
		if подпись.startswith(название + " · "):
			остаток = подпись[len(название) + 3 :]
			return остаток[:1].upper() + остаток[1:]
		return подпись

	указатель = [
		dict(з, here=з["target"] in места, place=место(з["label"]))
		for з in замечания
		if _на_уроке(з, урок["id"], блоки)
	]
	указатель.sort(key=lambda з: ГРУППЫ_ОЧЕРЕДИ.index(notes.группа(з["status"], з["waiting_on"])))
	урок["notes_index"] = указатель
	урок["open_notes"] = sum(з["status"] != "accepted" for з in указатель)


def _очередь(замечания: list[dict]) -> dict[str, list[dict]]:
	"""Очередь по тому, что ждёт человека: проверить сделанное, ответить
	агенту, ждать агента, принятые."""
	очередь: dict[str, list[dict]] = {группа: [] for группа in ГРУППЫ_ОЧЕРЕДИ}
	for з in замечания:
		очередь[notes.группа(з["status"], з["waiting_on"])].append(з)
	return очередь


def _сверка(course: str) -> dict:
	"""Сверка с картой — ровно то, что отдаёт агенту `course_map_check`, плюс
	ссылки на уроки кабинета: из карточки узла урок открывается целиком."""
	сверка = authoring.course_map_check(course=course)["data"]
	for урок in сверка["platform"]["lessons"]:
		урок["url"] = адрес(course, урок["id"])
	return сверка


def адрес(course: str, lesson: str | None = None) -> str:
	путь = f"{СТРАНИЦА}?course={quote(course)}"
	return f"{путь}&lesson={quote(lesson)}" if lesson else путь


def _курс(курс: dict) -> dict:
	"""Черновик, разложенный для отрисовки: номера, цвета глав, блоки уроков."""
	блоки_урока: dict[str, list[str]] = {}
	for документ in курс["artifacts"]:
		for блок in документ["blocks"]:
			if блок["lesson"]:
				блоки_урока.setdefault(блок["lesson"], []).append(блок["key"])

	номер = 0
	названия = {}
	for индекс, глава in enumerate(курс["chapters"]):
		глава["color"] = индекс % ЦВЕТОВ_ГЛАВ + 1
		for урок in глава["lessons"]:
			номер += 1
			урок["number"] = номер
			урок["url"] = адрес(курс["id"], урок["id"])
			урок["blocks"] = блоки_урока.get(урок["id"], [])
			урок["chapter_title"] = глава["title"]
			названия[урок["id"]] = урок["title"]

	уроки = [урок for глава in курс["chapters"] for урок in глава["lessons"]]
	курс["counts"] = {
		"lessons": len(уроки),
		"with_body": sum(у["has_body"] for у in уроки),
		"with_directive": sum(у["has_directive"] for у in уроки),
		"with_quiz": sum(у["quiz"] is not None for у in уроки),
	}
	for вид in ("blocking", "warnings"):
		for пункт in курс["readiness"][вид]:
			if пункт.get("lesson"):
				пункт["lesson_title"] = названия.get(пункт["lesson"])
				пункт["url"] = адрес(курс["id"], пункт["lesson"])
	for документ in курс["artifacts"]:
		for блок in документ["blocks"]:
			блок["artifact"] = документ["artifact"]
			блок["lesson_title"] = названия.get(блок["lesson"]) if блок["lesson"] else None
			блок["lesson_url"] = адрес(курс["id"], блок["lesson"]) if блок["lesson"] else None
	for документ in курс["artifacts"]:
		документ["binding"] = _привязка(документ)
	курс["directive_fields"] = _поля(курс["directive"], ПОЛЯ_КУРСА)
	курс["url"] = адрес(курс["id"])
	return курс


def _привязка(документ: dict) -> dict | None:
	"""Шаблон документа, его версия у курса и правки курса словами; схема целиком — `None`.

	`Why:` у документа куратор видел только «ключ · v4»: ни шаблона, ни того,
	что вышла его новая версия, ни того, чем курс от шаблона отличается
	(learning-services#384). Шаблон берётся тем же методом, что у агента.
	Описание — закреплённой версии (learning-services#387): куратор читает то
	же, по чему агент шаблон выбрал.
	"""
	if not документ["template"]:
		return None
	шаблон = authoring.artifact_template(template=документ["template"], version=документ["template_version"])
	шаблон = шаблон["data"] if шаблон["ok"] else None
	последняя = документ["template_latest"]
	return {
		"template": документ["template"],
		"version": документ["template_version"],
		"description": шаблон["description"] if шаблон else None,
		"latest": последняя if последняя and последняя > документ["template_version"] else None,
		"extends": шаблон["extends"] if шаблон else None,
		"edits": overlay.описать_правки(шаблон, документ["overlay"]) if шаблон else [],
	}


def _урок(курс: dict, lesson: str) -> dict | None:
	уроки = [урок for глава in курс["chapters"] for урок in глава["lessons"]]
	место = next((индекс for индекс, урок in enumerate(уроки) if урок["id"] == lesson), None)
	if место is None:
		return None
	сводка = уроки[место]
	полный = authoring.get_lesson(lesson=lesson)["data"]
	сегменты = normalizer.нормализовать_урок(lesson).segments
	return {
		"id": lesson,
		"title": полный["title"],
		"number": сводка["number"],
		"chapter_title": сводка["chapter_title"],
		"facts": сводка,
		"segments_html": [sanitize_html(md_to_html(сегмент)) for сегмент in сегменты],
		"directive": _директива(полный["directive"]),
		"quiz": _квиз(полный["quiz"]),
		"blocks": [
			блок
			for документ in курс["artifacts"]
			for блок in документ["blocks"]
			if блок["lesson"] == lesson
		],
		"prev": _сосед(уроки, место - 1),
		"next": _сосед(уроки, место + 1),
	}


def _директива(директива: dict | None) -> dict | None:
	if not директива:
		return None
	return {
		"version": директива["version"],
		"created_at": директива["created_at"],
		"objectives": directives.строки(директива.get("objectives")),
		"fields": _поля(директива, ПОЛЯ_УРОКА),
	}


def _поля(директива: dict | None, поля: tuple) -> list[dict]:
	"""Поля директивы для отрисовки: текст — разметкой, список — пунктами."""
	if not директива:
		return []
	собранное = []
	for имя, списком in поля:
		значение = директива.get(имя)
		if not значение:
			continue
		собранное.append(
			{
				"name": имя,
				"items": directives.строки(значение) if списком else None,
				"html": None if списком else sanitize_html(md_to_html(значение)),
			}
		)
	return собранное


def _квиз(квиз: dict | None) -> dict | None:
	"""Квиз с эталонами. Вопрос без текста или без верного варианта помечен —
	это те же беды, что блокируют публикацию."""
	if not квиз:
		return None
	for вопрос in квиз["questions"]:
		без_эталона = not any(вариант["correct"] for вариант in вопрос["options"]) and not вопрос["answers"]
		вопрос["broken"] = not (вопрос["text"] or "").strip() or без_эталона
	return квиз


def _сосед(уроки: list[dict], индекс: int) -> dict | None:
	if 0 <= индекс < len(уроки):
		урок = уроки[индекс]
		return {"id": урок["id"], "title": урок["title"], "number": урок["number"], "url": урок["url"]}
	return None


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
	if context.lesson:
		context.title = f"{context.lesson['title']} — кабинет автора"
	elif context.course and context.view == КАРТА:
		context.title = f"{context.course['title']} — карта — кабинет автора"
	elif context.course and context.view == ЗАМЕЧАНИЯ:
		context.title = f"{context.course['title']} — замечания — кабинет автора"
	elif context.course:
		context.title = f"{context.course['title']} — кабинет автора"
	else:
		context.title = "Кабинет автора"
	if context.course:
		# Замечания пишутся с этой страницы whitelisted-методами, а POST без
		# токена Frappe отклоняет.
		context.csrf_token = frappe.sessions.get_csrf_token()
	return context
