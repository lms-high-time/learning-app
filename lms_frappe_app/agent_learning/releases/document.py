# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Схема документа курса из релиза (learning-services#500).

Раздел документа релиза — блок схемы с теми же ключом, названием, описанием,
`rows` и колонками. Ключи те же, что в релизе: данные учеников живут между
версиями схемы по ключам, и правка релиза их не рушит. Тот же документ — та
же версия схемы: новая пишется, только когда схема изменилась.
"""

import frappe

from lms_frappe_app.agent_learning.artifacts.course import записать_схему, проверить_схему
from lms_frappe_app.agent_learning.artifacts.schema import колонки
from lms_frappe_app.agent_learning.releases.course_guard import ИЗ_РЕЛИЗА

ДОКУМЕНТ = "Agent Course Artifact"
БЛОК = "Agent Artifact Block"
#: Поля блока, по которым схема из релиза сверяется с действующей версией.
ПОЛЯ_БЛОКА = ("block_key", "title", "description", "lesson", "rows", "columns")
#: Флаги записи схемы публикацией. `Why:` у Course Creator на схемы только
#: чтение, а мимо публикации схему курса из релиза не правит никто
#: (`course_guard.проверить_документ`, learning-services#526).
ФЛАГИ_ЗАПИСИ = {ИЗ_РЕЛИЗА: True, "ignore_permissions": True}


def спроецировать(
	курс: str,
	документ: dict | None,
	уроки_релиза: list[dict],
	уроки: dict[str, str],
	прежний_ключ: str | None,
) -> dict | None:
	"""Действующая схема документа курса — по релизу; `{artifact, version}` или `None`.

	`уроки` — ключ урока → `Course Lesson`; `прежний_ключ` — ключ документа
	действующего релиза: документ сменил ключ или ушёл — прежняя схема
	перестаёт действовать.
	"""
	if прежний_ключ and (not документ or документ["key"] != прежний_ключ):
		_снять(курс, прежний_ключ)
	if not документ:
		return None
	блоки = _блоки(документ, уроки_релиза, уроки)
	действующая = frappe.db.get_value(
		ДОКУМЕНТ,
		{"course": курс, "slug": документ["key"], "is_active": 1},
		["name", "version", "title", "purpose"],
		as_dict=True,
	)
	if действующая and _та_же(действующая, документ, блоки):
		return {"artifact": документ["key"], "version": действующая.version}
	версия = записать_схему(
		курс,
		документ["key"],
		документ["title"],
		блоки,
		purpose=документ["purpose"],
		флаги=ФЛАГИ_ЗАПИСИ,
	)
	return {"artifact": версия["slug"], "version": версия["version"]}


def _блоки(документ: dict, уроки_релиза: list[dict], уроки: dict[str, str]) -> list[dict]:
	"""Блок раздела показывается на уроке, который первым в него пишет."""
	первый: dict[str, str] = {}
	for урок in уроки_релиза:
		for раздел in урок["sections"]:
			первый.setdefault(раздел, уроки[урок["key"]])
	return [
		{
			"key": раздел["key"],
			"title": раздел["title"],
			"description": раздел["description"] or None,
			"lesson": первый.get(раздел["key"]),
			"rows": раздел["rows"],
			"columns": раздел["columns"],
		}
		for раздел in документ["sections"]
	]


def _та_же(действующая, документ: dict, блоки: list[dict]) -> bool:
	"""Схема из релиза совпадает с действующей версией — новая версия не нужна."""
	if (действующая.title, действующая.purpose or None) != (документ["title"], документ["purpose"] or None):
		return False
	новые = проверить_схему(блоки)
	старые = frappe.get_all(
		БЛОК,
		filters={"parenttype": ДОКУМЕНТ, "parent": действующая.name},
		fields=list(ПОЛЯ_БЛОКА),
		order_by="idx asc",
	)
	return [_как_есть(с) for с in новые] == [_как_есть(с) for с in старые]


def _как_есть(строка) -> tuple:
	return (*(строка.get(п) or None for п in ПОЛЯ_БЛОКА[:-1]), колонки(строка))


def _снять(курс: str, ключ: str) -> None:
	"""Документа с этим ключом в релизе больше нет: его схема перестаёт действовать.
	Документы учеников остаются — они хранятся по ключу и не удаляются."""
	for имя in frappe.get_all(ДОКУМЕНТ, filters={"course": курс, "slug": ключ, "is_active": 1}, pluck="name"):
		frappe.db.set_value(ДОКУМЕНТ, имя, "is_active", 0)
