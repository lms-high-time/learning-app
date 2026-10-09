# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Ключи глав и уроков из релиза — на записях Learning (learning-services#514).

Соответствие «ключ → запись Learning» держала история индексов всех версий
курса; теперь оно на самой записи (`Course Chapter.chapter_key`,
`Course Lesson.lesson_key`, `projection.известные`). Патч заполняет ключи по
истории индексов. Ключ, который в разных версиях вёл к разным записям,
получает запись свежей версии, прочие записи остаются без ключа. Пишет только
в пустое поле, только запись того же курса и только ключ, которого ещё нет ни
на одной записи курса: повторный запуск ничего не меняет.

`Why:` поля патч заводит сам — патчи `post_model_sync` идут раньше
синхронизации фикстур, и без этого запись ушла бы в несуществующую колонку
(как в `announce_objectives`). Уникальный индекс `(course, ключ)` заводит
`after_migrate` уже после патча (`install.обеспечить_индекс_ключей_проекции`):
ключ курса патч пишет не больше чем на одну запись, и индекс встаёт без
дублей.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

from lms_frappe_app.agent_learning.releases import index, projection

#: Поля — как в фикстуре (`fixtures/custom_field.json`): синхронизация фикстур
#: потом лишь сверит их.
ПОЛЯ = {
	projection.ГЛАВА: {
		"fieldname": "chapter_key",
		"fieldtype": "Data",
		"label": "Ключ главы",
		"read_only": 1,
		"no_copy": 1,
		"description": "Ключ главы из релиза: по нему публикация находит главу. "
		"Ставится при создании и не меняется.",
		"insert_after": "chapter_description",
		"module": "Agent Learning",
	},
	projection.УРОК: {
		"fieldname": "lesson_key",
		"fieldtype": "Data",
		"label": "Ключ урока",
		"read_only": 1,
		"no_copy": 1,
		"description": "Ключ урока из релиза: по нему публикация находит урок. "
		"Ставится при создании и не меняется.",
		"insert_after": "lesson_hook",
		"module": "Agent Learning",
	},
}

#: Вид → строка индекса релиза, её ключ и ссылка на запись, запись Learning.
ВИДЫ = (
	("chapters", index.ГЛАВА, "chapter_key", "chapter", projection.ГЛАВА),
	("lessons", index.УРОК, "lesson_key", "lesson", projection.УРОК),
)


def по_истории(курс: str) -> dict[str, dict[str, str]]:
	"""Ключ → запись Learning по индексам всех версий курса; свежая версия главнее."""
	версии = {
		р.name: р.version
		for р in frappe.get_all(index.РЕЛИЗ, filters={"course": курс}, fields=["name", "version"])
	}
	итог: dict[str, dict[str, str]] = {"chapters": {}, "lessons": {}}
	if not версии:
		return итог
	for вид, строка, ключ, запись, _ in ВИДЫ:
		найдено = frappe.get_all(
			строка,
			filters={"parenttype": index.РЕЛИЗ, "parent": ("in", list(версии))},
			fields=["parent", ключ, запись],
		)
		for с in sorted(найдено, key=lambda с: версии[с.parent]):
			итог[вид][с[ключ]] = с[запись]
	return итог


def execute():
	for doctype, поле in ПОЛЯ.items():
		create_custom_field(doctype, поле)
	for курс in sorted(set(frappe.get_all(index.РЕЛИЗ, pluck="course"))):
		записано = заполнить(курс)
		print(
			f"release_record_keys: {курс} — ключей записано: "
			f"глав {записано['chapters']}, уроков {записано['lessons']}"
		)


def заполнить(курс: str) -> dict[str, int]:
	"""Ключи записей курса по истории его релизов; сколько записано по видам."""
	история = по_истории(курс)
	записано = {}
	for вид, _, _, _, doctype in ВИДЫ:
		поле = projection.ПОЛЕ_КЛЮЧА[doctype]
		ключи = {
			з.name: з[поле] for з in frappe.get_all(doctype, filters={"course": курс}, fields=["name", поле])
		}
		заняты = {ключ for ключ in ключи.values() if ключ}
		записано[вид] = 0
		for ключ, имя in история[вид].items():
			if ключ in заняты or имя not in ключи or ключи[имя]:
				continue
			frappe.db.set_value(doctype, имя, поле, ключ, update_modified=False)
			ключи[имя] = ключ
			заняты.add(ключ)
			записано[вид] += 1
	return записано
