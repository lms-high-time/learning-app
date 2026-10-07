# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Публикация релиза курса (learning-services#500).

Порядок: разобрать → схема → проверки сервера → курс → без изменений? →
записи под точкой сохранения: проекция глав и уроков, документ, релиз с
индексом, карточка курса и действующий релиз. Признак «опубликован» не
трогается: новый курс выходит черновиком, новый релиз опубликованного курса
действует сразу (решение владельца, #497).

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

from lms_frappe_app.agent_learning import structure
from lms_frappe_app.agent_learning.doctype.agent_course_release.agent_course_release import УДАЛЯЕТСЯ_КУРС
from lms_frappe_app.agent_learning.errors import КУРС_НЕ_НАЙДЕН, Отказ
from lms_frappe_app.agent_learning.releases import checks, document, index, projection, schema
from lms_frappe_app.agent_learning.releases.course_guard import ИЗ_РЕЛИЗА

РЕЛИЗ = index.РЕЛИЗ
ТОЧКА = "publish_release"

РЕЛИЗ_НЕВЕРЕН = "release_invalid"
ФОРМАТ_НЕ_ТОТ = "release_format_unsupported"
РЕЛИЗ_НЕ_СХОДИТСЯ = "release_inconsistent"
КЛЮЧ_НЕ_ТОТ = "course_key_mismatch"
КЛЮЧ_ЗАНЯТ = "course_key_taken"
У_КУРСА_ЕСТЬ_УРОКИ = "course_has_content"


def опубликовать(релиз, course: str | None, автор: str) -> dict:
	"""Релиз — новой версией курса; тот же релиз ещё раз — `unchanged`, без записей."""
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
			return _ответ(курс, действующий, None, None, предупреждения, создан=False, без_изменений=True)

	frappe.db.savepoint(ТОЧКА)
	try:
		создан = курс is None
		if создан:
			курс = _завести_курс(релиз["course"], автор, ключ)
		прежний = frappe.db.get_value("LMS Course", курс, "active_release")
		прежний_документ = frappe.db.get_value(РЕЛИЗ, прежний, "document_key") if прежний else None
		итог = projection.спроецировать(курс, релиз, index.известные(курс), index.ключи(прежний))
		схема_документа = document.спроецировать(
			курс, релиз["document"], релиз["lessons"], итог.уроки, прежний_документ
		)
		запись = _записать_релиз(курс, релиз, дайджест, итог, автор)
		_карточка(курс, релиз["course"], запись.name)
	except Отказ:
		frappe.db.rollback(save_point=ТОЧКА)
		raise
	frappe.db.release_savepoint(ТОЧКА)
	return _ответ(
		курс, запись.name, итог, схема_документа, предупреждения, создан=создан, без_изменений=False
	)


def _не_json(константа: str):
	raise ValueError(f"{константа} — не значение JSON")


def _разобрать(релиз) -> dict:
	"""Релиз — объект JSON без `NaN` и бесконечностей в любой части.

	`Why:` `json.loads` по умолчанию принимает `NaN` и `Infinity`, которых в
	JSON нет; в непрозрачных `agent` и `map` их не поймала бы схема, а снимок
	с ними не прочитал бы ни один строгий разборщик.
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


def _завести_курс(данные: dict, автор: str, ключ: str) -> str:
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
			"instructors": [{"instructor": автор}],
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


def _записать_релиз(курс: str, релиз: dict, дайджест: str, итог, автор: str):
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
			"document_key": (релиз["document"] or {}).get("key"),
			"published_by": автор,
			"published_at": now_datetime(),
			"snapshot": json.dumps(релиз, ensure_ascii=False, allow_nan=False),
			**index.строки(релиз, итог.главы, итог.уроки),
		}
	).insert(ignore_permissions=True)


def _карточка(курс: str, данные: dict, релиз: str) -> None:
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
	документ.flags[ИЗ_РЕЛИЗА] = True
	документ.save()


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
	return {
		"course": курс,
		"course_key": сведения.course_key,
		"release": релиз,
		"version": frappe.db.get_value(РЕЛИЗ, релиз, "version"),
		"unchanged": без_изменений,
		"course_created": создан,
		"published": bool(сведения.published),
		"chapters": изменения("chapters"),
		"lessons": изменения("lessons"),
		"document": схема_документа,
		"warnings": предупреждения,
	}


def удалить_курс(курс: str) -> None:
	"""Курс из релиза целиком: релизы, схемы документа, главы, уроки и сам курс.

	Для курсов, по которым учиться больше не будут (решение владельца: старые
	курсы удаляются вместе с историей). Релизы и схемы документа — проекции
	релиза, их удаление здесь; остальное удаляет Learning (`delete_course`).
	Записи учеников по курсу Learning не трогает: курс с ними удаление
	остановит ссылками.
	"""
	from lms.lms.api import delete_course

	frappe.db.set_value("LMS Course", курс, "active_release", None)
	frappe.clear_document_cache("LMS Course", курс)
	frappe.flags[УДАЛЯЕТСЯ_КУРС] = курс
	try:
		for имя in frappe.get_all(РЕЛИЗ, filters={"course": курс}, pluck="name"):
			frappe.delete_doc(РЕЛИЗ, имя, ignore_permissions=True)
	finally:
		frappe.flags[УДАЛЯЕТСЯ_КУРС] = None
	for имя in frappe.get_all("Agent Course Artifact", filters={"course": курс}, pluck="name"):
		frappe.delete_doc("Agent Course Artifact", имя, ignore_permissions=True)
	delete_course(курс)
