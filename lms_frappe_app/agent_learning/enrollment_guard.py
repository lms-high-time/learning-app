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

- Изменение существующей записи — отказ по любому полю, включая владельца и
  дату создания, кроме тех, что Frappe сам подтягивает из `member`
  (`fetch_from`).
- Вставка — только изнутри записи в группу Learning: запись в группу
  (`LMS Batch Enrollment`) заводит зачисления на курсы группы от имени того, кто
  записывает, без `ignore_permissions`, в `validate_course_enrollment` — после
  проверок группы: кто записывает (сам себя, `Moderator` или
  `Batch Evaluator`), самозаписи, оплаты, мест. Примесь записи в группу
  (`ЗаписьВГруппу`, `extend_doctype_class`) метит запрос парой «группа,
  ученик» ровно на время этого шага, и вставка проходит только с этой группой
  и этим учеником; остальные поля — по умолчанию. Метка сверяется с учеником
  записи, а не с сессией: `Batch Evaluator` записывает в группу других.
  Learning переименует шаг — метки не будет, и самозапись в группу откажет, а
  не откроется: её держит тест. Обычная запись на курс — метод `enroll`: там пространство, анонс и
  порядок программы.

Чего не делает: чтение и удаление записи не трогает.
"""

from collections.abc import Iterable

import frappe
from frappe import _
from frappe.model import data_fieldtypes, table_fields
from frappe.utils import cast, cint

ДОКТАЙП = "LMS Enrollment"
#: Поля, которые ученик задаёт при вставке по группе Learning.
ПОЛЯ_ВСТАВКИ = frozenset({"course", "member", "enrollment_from_batch"})
#: Поля документа, которые не описаны в схеме, но меняются сохранением.
СЛУЖЕБНЫЕ = (
	frappe._dict(fieldname="owner", fieldtype="Data"),
	frappe._dict(fieldname="creation", fieldtype="Datetime"),
)
#: `frappe.flags`: (группа, ученик) записи в группу, которая сейчас сохраняется.
ГРУППА = "lms_frappe_app_enrollment_batch"
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


class ЗаписьВГруппу:
	"""Примесь к `LMS Batch Enrollment`: зачисления на курсы группы заводятся под
	меткой.

	`Why:` примесь, а не подмена класса: Frappe берёт последнюю подмену молча,
	и подмена платформы выкинула бы подмену Learning, если та появится.
	"""

	def validate_course_enrollment(self):
		прежняя = frappe.flags.get(ГРУППА)
		frappe.flags[ГРУППА] = (self.batch, self.member)
		try:
			super().validate_course_enrollment()
		finally:
			frappe.flags[ГРУППА] = прежняя


def только_свои(строки: Iterable, роли: set[str], право: str) -> bool:
	"""Даёт ли `право` хоть одна строка прав ролей `роли` — и только с `if_owner`.

	Подсчёт `frappe.permissions.get_role_permissions` для одного права: строки
	уровня 0 ролей пользователя. Нет права вовсе — `False`: сохранение
	отклонит Frappe.
	"""
	дающие = [
		строка
		for строка in строки
		if строка.role in роли and not cint(строка.permlevel) and cint(строка.get(право))
	]
	return bool(дающие) and all(cint(строка.if_owner) for строка in дающие)


def _только_свои(право: str) -> bool:
	return только_свои(frappe.get_meta(ДОКТАЙП).permissions, set(frappe.get_roles()), право)


def _проверить_изменение(doc) -> None:
	прежняя = doc.get_doc_before_save()
	if прежняя is None:
		_отказать()
	for df in (*_поля(), *СЛУЖЕБНЫЕ):
		if _значение(df, doc.get(df.fieldname)) != _значение(df, прежняя.get(df.fieldname)):
			_отказать()


def _проверить_вставку(doc) -> None:
	if not doc.member or frappe.flags.get(ГРУППА) != (doc.enrollment_from_batch, doc.member):
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
