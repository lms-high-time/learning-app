# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Публикация релиза курса (learning-services#500).

Порядок: разобрать → схема → проверки сервера → курс → без изменений? →
ключи на записях действующего релиза → блокировка открытых попыток квиза
курса → записи под точкой сохранения: проекция глав и уроков, шаблоны
домашек, документ, релиз с индексом, карточка курса, инструкторы и
действующий релиз, перенос или аннулирование открытых попыток
(`release_quiz.перенести_попытки`).
Признак «опубликован» не трогается: новый курс выходит черновиком, новый релиз
опубликованного курса действует сразу (решение владельца, #497).

`Why:` точка сохранения — потому что `@контракт` превращает `Отказ` в
успешный HTTP-ответ, и Frappe фиксирует всё, что записано до отказа. Все
проверки — до первой записи; `Отказ` посреди записей откатывает к точке.
Непредвиденная ошибка уходит HTTP-ошибкой, и запрос откатывается целиком.
"""

import hashlib
import json
import math

import frappe
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning import release_quiz, structure
from lms_frappe_app.agent_learning.doctype.agent_course_release.agent_course_release import УДАЛЯЕТСЯ_КУРС
from lms_frappe_app.agent_learning.errors import КУРС_НЕ_НАЙДЕН, Отказ
from lms_frappe_app.agent_learning.releases import (
	checks,
	document,
	homework,
	index,
	places,
	projection,
	schema,
)
from lms_frappe_app.agent_learning.releases.course_guard import ИЗ_РЕЛИЗА

РЕЛИЗ = index.РЕЛИЗ
ТОЧКА = "publish_release"

РЕЛИЗ_НЕВЕРЕН = "release_invalid"
ФОРМАТ_НЕ_ТОТ = "release_format_unsupported"
РЕЛИЗ_НЕ_СХОДИТСЯ = "release_inconsistent"
КЛЮЧ_НЕ_ТОТ = "course_key_mismatch"
КЛЮЧ_ЗАНЯТ = "course_key_taken"
У_КУРСА_ЕСТЬ_УРОКИ = "course_has_content"
КУРС_С_ПРОХОЖДЕНИЯМИ = "course_has_lesson_runs"
КЛЮЧЕЙ_НЕТ = "course_keys_missing"


def опубликовать(
	релиз,
	course: str | None,
	автор: str,
	инструкторы: list[str] | None = None,
	коммит: str | None = None,
) -> dict:
	"""Релиз — новой версией курса; тот же релиз ещё раз — `unchanged`, без записей релиза.

	`инструкторы` — проверенные имена пользователей: заменяют инструкторов
	курса, и на `unchanged` тоже — в дайджест они не входят. `None` — не
	трогать; новый курс тогда получает инструктором `автор`.

	`коммит` — проверенный хеш коммита git, из которого собран релиз:
	пишется в новую версию (`source_commit`). На `unchanged` не пишется —
	запись релиза неизменяема, и у версии остаётся коммит её первой
	публикации. `Why:` в дайджест коммит не входит — тот же релиз из другого
	коммита перестал бы быть `unchanged`.

	Открытые попытки квиза курса блокируются только на пути с записью — тот
	же релиз ещё раз попыток не трогает — и переносятся или аннулируются
	после смены действующего релиза: сравнению нужен индекс их релиза.
	Взаимоблокировку с ответом агента (`frappe.QueryDeadlockError`) метод
	контракта отдаёт как `busy`.
	"""
	релиз = _разобрать(релиз)
	предупреждения = _проверить(релиз)
	ключ = релиз["course"]["key"]
	курс = _курс(ключ, course)
	дайджест = _дайджест(релиз)
	if курс:
		# Две публикации одного курса идут по очереди: версия «следующая» не двоится.
		frappe.db.get_value("LMS Course", курс, "name", for_update=True)
		действующий = frappe.db.get_value("LMS Course", курс, "active_release")
		if действующий and frappe.db.get_value(РЕЛИЗ, действующий, "digest") == дайджест:
			if инструкторы is not None:
				документ = frappe.get_doc("LMS Course", курс)
				if _назначить_инструкторов(документ, инструкторы):
					документ.flags[ИЗ_РЕЛИЗА] = True
					документ.save()
			return _ответ(курс, действующий, None, None, предупреждения, создан=False, без_изменений=True)
		if действующий:
			_проверить_ключи(курс, действующий)
	попытки = release_quiz.заблокировать_открытые(курс) if курс else []

	frappe.db.savepoint(ТОЧКА)
	try:
		создан = курс is None
		if создан:
			курс = _завести_курс(релиз["course"], инструкторы or [автор], ключ)
		прежний = frappe.db.get_value("LMS Course", курс, "active_release")
		прежний_документ = frappe.db.get_value(РЕЛИЗ, прежний, "document_key") if прежний else None
		итог = projection.спроецировать(курс, релиз, projection.известные(курс), index.ключи(прежний))
		homework.спроецировать(курс, {итог.уроки[у["key"]]: у["homework"] for у in релиз["lessons"]})
		схема_документа = document.спроецировать(
			курс, релиз["document"], релиз["lessons"], итог.уроки, прежний_документ
		)
		строки = index.строки(релиз, итог.главы, итог.уроки)
		запись = _записать_релиз(курс, релиз, дайджест, строки, автор, коммит)
		_карточка(курс, релиз["course"], запись.name, инструкторы)
		release_quiz.перенести_попытки(попытки, запись.name, строки)
	except Отказ:
		frappe.db.rollback(save_point=ТОЧКА)
		raise
	frappe.db.release_savepoint(ТОЧКА)
	if not создан:
		_сверить_прохождения(курс)
	return _ответ(
		курс, запись.name, итог, схема_документа, предупреждения, создан=создан, без_изменений=False
	)


def _проверить_ключи(курс: str, действующий: str) -> None:
	"""Отказ `course_keys_missing`, если у глав или уроков действующего релиза
	курса нет ключа на записи Learning. Одна выборка.

	`Why:` проекция находит запись Learning только по ключу на ней
	(`projection.известные`). Без ключей — сайт не прошёл патч
	`release_record_keys` — публикация завела бы новые записи под все ключи
	релиза, и следы учеников остались бы у прежних. Смотрятся только записи
	действующего релиза: патч пишет ключ по свежей версии, и они получают его
	всегда, а запись курса вне релиза может остаться без ключа и после патча
	(глава анонса; запись, чей ключ в свежей версии вёл уже к другой) — отказ
	из-за неё был бы вечным.
	"""
	части = [
		f"""(select count(*) from `tab{строка}` r join `tab{doctype}` z on z.name = r.`{ссылка}`
		where r.parenttype = %(parenttype)s and r.parent = %(release)s
		and ifnull(z.`{projection.ПОЛЕ_КЛЮЧА[doctype]}`, '') = '')"""
		for строка, ссылка, doctype in (
			(index.ГЛАВА, "chapter", projection.ГЛАВА),
			(index.УРОК, "lesson", projection.УРОК),
		)
	]
	запрос = "select " + ", ".join(части)
	[(главы, уроки)] = frappe.db.sql(запрос, {"parenttype": РЕЛИЗ, "release": действующий})
	if главы or уроки:
		raise Отказ(
			КЛЮЧЕЙ_НЕТ,
			"У глав или уроков курса нет ключей релиза: сайту нужна миграция с патчем release_record_keys",
			course=курс,
			chapters=главы,
			lessons=уроки,
		)


def _сверить_прохождения(курс: str) -> None:
	"""Прохождения курса — с новым релизом: фоном, после коммита публикации.

	`Why:` статусы целей и уроков хранятся (learning-services#504): без сверки
	при публикации отчёты читали бы их по прошлому релизу. Фоном — сверка
	всех учеников курса не держит транзакцию публикации. Сбой постановки
	публикацию не срывает: прохождение сверится при обращении. В тестах —
	сразу: `now` Frappe выполняет вызов синхронно, мимо очереди.
	"""
	try:
		frappe.enqueue(
			"lms_frappe_app.agent_learning.runs.service.сверить_курс",
			queue="long",
			enqueue_after_commit=True,
			now=frappe.in_test,
			курс=курс,
		)
	except Exception:
		# В тестах сверка идёт сразу, и её ошибка — ошибка теста, а не сбой очереди.
		if frappe.in_test:
			raise
		frappe.log_error(title="Сверка прохождений не поставлена в очередь (learning-services#504)")


def _не_json(константа: str):
	raise ValueError(f"{константа} — не значение JSON")


def _разобрать(релиз) -> dict:
	"""Релиз — объект JSON без `NaN` и бесконечностей в любой части.

	`Why:` `json.loads` по умолчанию принимает `NaN` и `Infinity`, которых в
	JSON нет; в `agent` и `map`, которые схема не описывает, их не поймала бы
	схема, а снимок с ними не прочитал бы ни один строгий разборщик.
	"""
	if isinstance(релиз, str):
		try:
			релиз = json.loads(релиз, parse_constant=_не_json)
		except ValueError as причина:
			raise Отказ(
				РЕЛИЗ_НЕВЕРЕН, "Релиз — не JSON", errors=[{"path": "$", "message": str(причина)}], total=1
			) from причина
	if not isinstance(релиз, dict):
		raise Отказ(
			РЕЛИЗ_НЕВЕРЕН,
			"Релиз — объект JSON",
			errors=[{"path": "$", "message": "ожидается объект"}],
			total=1,
		)
	if найдено := _не_конечные(релиз, "$"):
		raise Отказ(
			РЕЛИЗ_НЕВЕРЕН,
			"В релизе — не значения JSON",
			errors=найдено[: schema.ОШИБОК_НЕ_БОЛЬШЕ],
			total=len(найдено),
		)
	return релиз


def _не_конечные(значение, путь: str) -> list[dict]:
	"""`NaN` и бесконечности где угодно в релизе, включая `agent` и `map`."""
	if isinstance(значение, float) and not math.isfinite(значение):
		return [{"path": путь, "message": "не конечное число"}]
	if isinstance(значение, dict):
		return [о for ключ, вложенное in значение.items() for о in _не_конечные(вложенное, f"{путь}.{ключ}")]
	if isinstance(значение, list):
		return [
			о for номер, вложенное in enumerate(значение) for о in _не_конечные(вложенное, f"{путь}[{номер}]")
		]
	return []


def _проверить(релиз: dict) -> list[dict]:
	if релиз.get("format") != schema.ФОРМАТ:
		raise Отказ(
			ФОРМАТ_НЕ_ТОТ,
			f"Формат релиза — {schema.ФОРМАТ}",
			format=релиз.get("format"),
			supported=[schema.ФОРМАТ],
		)
	if найдено := schema.ошибки(релиз):
		raise Отказ(
			РЕЛИЗ_НЕВЕРЕН,
			"Релиз не проходит публичную схему",
			errors=найдено[: schema.ОШИБОК_НЕ_БОЛЬШЕ],
			total=len(найдено),
		)
	критичные, предупреждения = checks.проблемы(релиз)
	if критичные:
		raise Отказ(
			РЕЛИЗ_НЕ_СХОДИТСЯ,
			"Части релиза не сходятся",
			problems=критичные[: schema.ОШИБОК_НЕ_БОЛЬШЕ],
			total=len(критичные),
		)
	return предупреждения


def _курс(ключ: str, course: str | None) -> str | None:
	"""Курс под релиз; `None` — завести новый."""
	по_ключу = frappe.db.get_value("LMS Course", {"course_key": ключ})
	if not course:
		return по_ключу
	if not frappe.db.exists("LMS Course", course):
		raise Отказ(КУРС_НЕ_НАЙДЕН, "LMS Course не найден", id=course)
	сведения = frappe.db.get_value("LMS Course", course, ["course_key"], as_dict=True)
	if сведения.course_key:
		if сведения.course_key != ключ:
			raise Отказ(
				КЛЮЧ_НЕ_ТОТ,
				"У курса другой ключ",
				course=course,
				course_key=сведения.course_key,
				release_key=ключ,
			)
		return course
	if по_ключу:
		raise Отказ(КЛЮЧ_ЗАНЯТ, "Ключ релиза уже у другого курса", course=по_ключу, release_key=ключ)
	# Релиз ложится только на курс без уроков — анонс (решение владельца, #500):
	# прогресс по старым урокам к ключам не привязан и всё равно потерялся бы.
	if уроки := structure.уроки_курса(course):
		raise Отказ(
			У_КУРСА_ЕСТЬ_УРОКИ,
			"Релиз ложится только на курс без уроков: опубликуйте его без `course` — новым курсом",
			course=course,
			lessons=len(уроки),
		)
	return course


def _дайджест(релиз: dict) -> str:
	"""sha256 канонического JSON: порядок ключей и пробелы не меняют дайджест."""
	канон = json.dumps(релиз, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
	return hashlib.sha256(канон.encode("utf-8")).hexdigest()


def _завести_курс(данные: dict, инструкторы: list[str], ключ: str) -> str:
	"""Новый курс-черновик под ключ релиза.

	`Why:` две одновременные первые публикации одного ключа обе не находят
	курса; вторая упирается в уникальный `course_key` — и получает код
	контракта, а не ошибку сервера. Повтор вызова найдёт курс по ключу.
	"""
	курс = frappe.get_doc(
		{
			"doctype": "LMS Course",
			"title": данные["title"],
			"short_introduction": данные["summary"] or данные["title"],
			"description": данные["description"] or данные["summary"] or данные["title"],
			"published": 0,
			"course_key": данные["key"],
			"instructors": [{"instructor": имя} for имя in инструкторы],
		}
	)
	курс.flags[ИЗ_РЕЛИЗА] = True
	try:
		return курс.insert().name
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError) as причина:
		frappe.clear_last_message()
		raise Отказ(
			КЛЮЧ_ЗАНЯТ,
			"Курс с этим ключом заводит другая публикация: повторите вызов",
			course=frappe.db.get_value("LMS Course", {"course_key": ключ}),
			release_key=ключ,
		) from причина


def _записать_релиз(курс: str, релиз: dict, дайджест: str, строки: dict, автор: str, коммит: str | None):
	"""Запись релиза новой версией; `строки` — его индекс (`index.строки`),
	`коммит` — коммит источника или `None`."""
	последняя = frappe.db.sql("select max(version) from `tabAgent Course Release` where course=%s", курс)[0][
		0
	]
	# Без проверки прав: метод закрыт авторскими ролями (`_автор`), а создавать
	# релиз мимо сервиса не может никто — у доктайпа нет права `create`.
	return frappe.get_doc(
		{
			"doctype": РЕЛИЗ,
			"course": курс,
			"course_key": релиз["course"]["key"],
			"version": (последняя or 0) + 1,
			"release_format": schema.ФОРМАТ,
			"digest": дайджест,
			"source_commit": коммит,
			"document_key": (релиз["document"] or {}).get("key"),
			"published_by": автор,
			"published_at": now_datetime(),
			"snapshot": json.dumps(релиз, ensure_ascii=False, allow_nan=False),
			**строки,
		}
	).insert(ignore_permissions=True)


def _карточка(курс: str, данные: dict, релиз: str, инструкторы: list[str] | None) -> None:
	"""Карточка курса — из релиза; пустые тексты Learning не принимает (`reqd`) —
	на их месте название, о чём сказано в предупреждениях проверки.
	`description` пишется как есть, как у `update_course`: отрисовку решает этап 6.

	Сохранением документа, а не `db.set_value`: порядок курса читает
	действующий релиз из кэша документа, и сохранение его сбрасывает."""
	документ = frappe.get_doc("LMS Course", курс)
	документ.update(
		{
			"title": данные["title"],
			"short_introduction": данные["summary"] or данные["title"],
			"description": данные["description"] or данные["summary"] or данные["title"],
			"course_promise": данные["promise"] or None,
			"course_attribution": json.dumps(данные["attribution"], ensure_ascii=False)
			if данные["attribution"]
			else None,
			"course_key": данные["key"],
			"active_release": релиз,
		}
	)
	if инструкторы is not None:
		_назначить_инструкторов(документ, инструкторы)
	документ.flags[ИЗ_РЕЛИЗА] = True
	документ.save()


def _назначить_инструкторов(документ, инструкторы: list[str]) -> bool:
	"""Инструкторы курса — ровно этот список, по порядку. `True` — если набор поменялся."""
	if [строка.instructor for строка in документ.instructors] == инструкторы:
		return False
	документ.set("instructors", [{"instructor": имя} for имя in инструкторы])
	return True


def _ответ(курс, релиз, итог, схема_документа, предупреждения, *, создан: bool, без_изменений: bool) -> dict:
	def изменения(вид: str) -> dict:
		if not итог:
			return {"created": [], "updated": [], "removed": [], "restored": []}
		return {
			"created": итог.создано[вид],
			"updated": итог.обновлено[вид],
			"removed": итог.снято[вид],
			"restored": итог.возвращено[вид],
		}

	if без_изменений:
		ключ_документа = frappe.db.get_value(РЕЛИЗ, релиз, "document_key")
		версия = (
			frappe.db.get_value(
				"Agent Course Artifact", {"course": курс, "slug": ключ_документа, "is_active": 1}, "version"
			)
			if ключ_документа
			else None
		)
		схема_документа = {"artifact": ключ_документа, "version": версия} if ключ_документа else None
	сведения = frappe.db.get_value("LMS Course", курс, ["course_key", "published"], as_dict=True)
	запись = frappe.db.get_value(РЕЛИЗ, релиз, ["version", "source_commit"], as_dict=True)
	return {
		"course": курс,
		"course_key": сведения.course_key,
		"release": релиз,
		"version": запись.version,
		"commit": запись.source_commit or None,
		"unchanged": без_изменений,
		"course_created": создан,
		"published": bool(сведения.published),
		"chapters": изменения("chapters"),
		"lessons": изменения("lessons"),
		"document": схема_документа,
		"instructors": frappe.get_all(
			"Course Instructor",
			filters={"parenttype": "LMS Course", "parent": курс, "parentfield": "instructors"},
			pluck="instructor",
			order_by="idx asc",
		),
		"warnings": предупреждения,
	}


def удалить_курс(курс: str) -> None:
	"""Курс из релиза целиком: заметки автора, релизы с кэшем узлов карты, схемы документа,
	домашки, главы, уроки и сам курс.

	Для курсов, по которым учиться больше не будут (решение владельца: старые
	курсы удаляются вместе с историей). Релизы, схемы документа и шаблоны
	домашек — проекции релиза, а заметки написаны по его ключам: их удаление
	здесь; остальное удаляет Learning (`delete_course`).
	Записи учеников по курсу не трогает: курс с прохождениями уроков
	(`Agent Lesson Run`) — отказ `course_has_lesson_runs` до первой записи,
	с записями на курс Learning удаление остановит ссылками.
	"""
	from lms.lms.api import delete_course

	# Why: прохождения ссылаются на релизы курса, и удаление встало бы на
	# релизе — уже после того, как курс потерял действующий релиз.
	if прохождений := frappe.db.count("Agent Lesson Run", {"course": курс}):
		raise Отказ(
			КУРС_С_ПРОХОЖДЕНИЯМИ,
			"У курса есть прохождения уроков учеников: курс с ними не удаляется",
			course=курс,
			lesson_runs=прохождений,
		)
	frappe.db.set_value("LMS Course", курс, "active_release", None)
	frappe.clear_document_cache("LMS Course", курс)
	# Флаг — до конца `delete_course`: релизы курса и его главы с уроками
	# удаляются только вместе с курсом (`course_guard`).
	frappe.flags[УДАЛЯЕТСЯ_КУРС] = курс
	try:
		# Заметки ссылаются на релиз, к которому написаны, — уходят раньше релизов.
		for имя in frappe.get_all("Agent Author Note", filters={"course": курс}, pluck="name"):
			frappe.delete_doc("Agent Author Note", имя, ignore_permissions=True)
		for релиз in frappe.get_all(РЕЛИЗ, filters={"course": курс}, fields=["name", "digest"]):
			frappe.delete_doc(РЕЛИЗ, релиз.name, ignore_permissions=True)
			frappe.cache.delete_value(places.ключ_кэша(релиз.name, релиз.digest))
		for имя in frappe.get_all("Agent Course Artifact", filters={"course": курс}, pluck="name"):
			frappe.delete_doc("Agent Course Artifact", имя, ignore_permissions=True)
		homework.удалить_шаблоны(курс)
		delete_course(курс)
	finally:
		frappe.flags[УДАЛЯЕТСЯ_КУРС] = None
