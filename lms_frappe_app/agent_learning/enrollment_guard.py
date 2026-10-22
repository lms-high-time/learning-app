# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Поля зачисления пишет код платформы.

Зачисление — единственное основание доступа к курсу (`access.py`), а его поля
решают, к какому курсу, кому и в каком качестве: `course`, `member`,
`agent_tester`, `payment`, `enrollment_from_batch`. Хук `validate` держит
правило на самом документе, какой бы дорогой ни пришло сохранение — Desk,
`/api/resource` или код Learning. `Why:` права доктайпа задаёт Learning —
чужое приложение, которое сливается с upstream, — и то, что решает доступ к
курсу, на них одних держаться не должно.

Правило касается того, кому запись или создание доступны только на свои
записи (`if_owner`). Кто это, решают права доктайпа вместе с `Custom DocPerm`
сайта, а не список ролей здесь: роль с полным правом (`Moderator`,
`Course Creator`, `System Manager`) пишет зачисления как раньше. Код
платформы и Learning сохраняет зачисления с `ignore_permissions` — его хук не
трогает.

- Изменение существующей записи — отказ по любому полю, кроме тех, что Frappe
  сам подтягивает из `member` (`fetch_from`).
- Вставка — только на себя и только по группе Learning
  (`enrollment_from_batch`): самозапись в группу заводит зачисления на её
  курсы от имени ученика, без `ignore_permissions`, а допуск к группе и курсу
  проверяет Learning. Остальные поля — по умолчанию. Обычная запись на курс —
  метод `enroll`: там пространство, анонс и порядок программы.

Чего не делает: чтение и удаление записи не трогает.
"""

import frappe
from frappe import _
from frappe.model import data_fieldtypes, table_fields
from frappe.utils import cast, cint

ДОКТАЙП = "LMS Enrollment"
#: Поля, которые ученик задаёт при вставке по группе Learning.
ПОЛЯ_ВСТАВКИ = frozenset({"course", "member", "enrollment_from_batch"})
ОТКАЗ = "Запись на курс меняется только методами платформы"


def проверить_зачисление(doc, method=None) -> None:
	"""Хук `validate` у `LMS Enrollment`."""
	if doc.flags.ignore_permissions:
		return
	if doc.is_new():
		if _только_свои("create"):
			_проверить_вставку(doc)
		return
	if _только_свои("write"):
		_проверить_изменение(doc)


def _только_свои(право: str) -> bool:
	"""Право `право` на доктайп у пользователя есть, но только на свои записи.

	Повторяет подсчёт `frappe.permissions.get_role_permissions` для одного
	права: роли пользователя на уровне 0, и у каждой, что даёт право, стоит
	`if_owner`. Нет права вовсе — не наш случай: сохранение отклонит Frappe.
	"""
	if frappe.session.user == "Administrator":
		return False
	роли = set(frappe.get_roles())
	дающие = [
		строка
		for строка in frappe.get_meta(ДОКТАЙП).permissions
		if строка.role in роли and not cint(строка.permlevel) and cint(строка.get(право))
	]
	return bool(дающие) and all(cint(строка.if_owner) for строка in дающие)


def _проверить_изменение(doc) -> None:
	прежняя = doc.get_doc_before_save() or frappe.get_doc(ДОКТАЙП, doc.name)
	for df in _поля():
		if _значение(df, doc.get(df.fieldname)) != _значение(df, прежняя.get(df.fieldname)):
			_отказать()


def _проверить_вставку(doc) -> None:
	if doc.member != frappe.session.user or not doc.enrollment_from_batch:
		_отказать()
	for df in _поля():
		if df.fieldname in ПОЛЯ_ВСТАВКИ:
			continue
		if _значение(df, doc.get(df.fieldname)) != _значение(df, df.default):
			_отказать()


def _поля() -> list:
	"""Поля данных записи без тех, что Frappe подтягивает из ссылки на ней же."""
	мета = frappe.get_meta(ДОКТАЙП)
	return [
		df
		for df in мета.fields
		if df.fieldtype in data_fieldtypes
		and df.fieldtype not in table_fields
		and not (df.fetch_from and мета.get_field(df.fetch_from.split(".")[0]))
	]


def _значение(df, значение):
	"""Значение в типе поля; пустые `None`, `""` и `0` не различаются."""
	return cast(df.fieldtype, значение) or None


def _отказать() -> None:
	frappe.throw(_(ОТКАЗ), frappe.PermissionError)
