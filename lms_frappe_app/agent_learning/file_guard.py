# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Что в записях `File` меняет только System Manager (learning-services#683).

- Адрес файла (`file_url`) после вставки. `Why:` право скачать файл Frappe
  проверяет по записи `File`, а адрес связывает запись с байтами на диске.
  Доступ к уже существующему файлу Frappe сверяет только при вставке
  (`File.validate_private_file_access`), поэтому адрес задаёт только вставка.
- Файлы сдачи домашки. Вставляет их только сохранение сдачи
  (`uploads.новый_файл`), правка и удаление закрыты. `Why:` на файл ссылаются
  версии сдачи и решение куратора — «убранный файл не удаляется» (`CONTRACT.md`,
  `submit_homework`), а владелец записи `File` по правам Frappe — сам ученик.

Проверки — в классе-примеси (`extend_doctype_class`), а не в `doc_events`.
`Why:` обработчики `doc_events` идут после метода контроллера, а
`File.on_trash` и `File.before_insert` сразу трогают диск: отказ в обработчике
удаления пришёл бы, когда байты уже стёрты, а запись осталась бы без них.
Примесь проверяет до метода контроллера — и при `ignore_permissions` тоже.
"""

from __future__ import annotations

import frappe
from frappe import _

from lms_frappe_app.agent_learning.uploads import ФЛАГ_ФАЙЛА_СДАЧИ

СДАЧА = "Agent Homework Submission"
РОЛЬ = "System Manager"


def _без_роли() -> bool:
	return РОЛЬ not in frappe.get_roles()


def _отказать(сообщение: str) -> None:
	frappe.throw(сообщение, frappe.PermissionError)


class ФайлПлатформы:
	"""Примесь к контроллеру `File`: проверки до его методов."""

	def before_insert(self):
		if self.attached_to_doctype == СДАЧА and not self.flags.get(ФЛАГ_ФАЙЛА_СДАЧИ) and _без_роли():
			_отказать(_("Файлы сдачи домашки добавляет только её сохранение"))
		super().before_insert()

	def before_validate(self):
		# Вставку проверяет `before_insert`; `in_insert` Frappe ставит до проверок.
		прежний = None if self.flags.in_insert else self.get_doc_before_save()
		if прежний is not None and _без_роли():
			if (self.file_url or "") != (прежний.file_url or ""):
				_отказать(_("Адрес файла не меняется"))
			if СДАЧА in (self.attached_to_doctype, прежний.attached_to_doctype):
				_отказать(_("Файлы сдачи домашки не правятся"))
		родитель = getattr(super(), "before_validate", None)
		if родитель:
			родитель()

	def on_trash(self):
		if self.attached_to_doctype == СДАЧА and _без_роли():
			_отказать(_("Файлы сдачи домашки не удаляются"))
		super().on_trash()
