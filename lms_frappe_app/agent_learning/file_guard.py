# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Что в записях `File` меняет только System Manager (learning-services#683).

- Адрес файла (`file_url`), миниатюра (`thumbnail_url`), отпечаток
  содержимого (`content_hash`) и приватность (`is_private`) после вставки.
  `Why:` право скачать файл Frappe проверяет по записи `File`, а эти поля
  связывают запись с байтами на диске и с другими записями тех же байтов.
- При вставке: адрес — только в каноническом виде, приватный — только адрес
  существующей записи, которую пользователь читает; миниатюру и отпечаток
  ставит Frappe, а не запрос; расширение имени — из списка типов сайта.
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
import mimetypes
import posixpath
from urllib.parse import unquote

from frappe import _

from lms_frappe_app.agent_learning.uploads import ФЛАГ_ФАЙЛА_СДАЧИ, расширение

СДАЧА = "Agent Homework Submission"
РОЛЬ = "System Manager"


def _без_роли() -> bool:
	return РОЛЬ not in frappe.get_roles()


def _отказать(сообщение: str) -> None:
	frappe.throw(сообщение, frappe.PermissionError)


#: Поля, которые после вставки меняет только System Manager.
НЕИЗМЕННЫЕ = ("file_url", "thumbnail_url", "content_hash", "is_private")


def _адрес_канонический(адрес: str) -> bool:
	"""Локальный адрес без `.`/`..`, двойных `/` и закодированных символов пути."""
	раскрытый = unquote(адрес)
	return раскрытый == адрес and "//" not in адрес and posixpath.normpath(адрес) == адрес


def _проверить_адрес(файл) -> None:
	"""Адрес, заданный запросом, ведёт только к файлу, который пользователь уже видит."""
	адрес = файл.file_url or ""
	if not адрес or адрес.startswith(("http://", "https://")):
		return
	if not _адрес_канонический(адрес):
		_отказать(_("Неверный адрес файла"))
	if адрес.startswith("/private/"):
		видимые = [
			имя
			for имя in frappe.get_all("File", filters={"file_url": адрес}, pluck="name")
			if frappe.has_permission("File", "read", doc=имя)
		]
		if not видимые:
			_отказать(_("Неверный адрес файла"))


def _по_запросу() -> bool:
	"""Вставка пришла запросом: как и Frappe, список типов сверяется только для загрузок."""
	return bool(frappe.request)


def _проверить_расширение(файл) -> None:
	"""Расширение имени — из списка типов сайта там, где Frappe тип не узнаёт.

	`Why:` Frappe сверяет со списком расширение, выведенное из типа по имени
	(`File.set_file_type`: тип, затем обратно расширение), а когда обратного
	расширения нет — например, у документов Office с макросами, — не проверяет
	вовсе. Выведенное расширение проверит он сам.
	"""
	разрешённые = (frappe.get_system_settings("allowed_file_extensions") or "").split()
	if not разрешённые or not _по_запросу() or файл.is_folder:
		return
	if (файл.file_url or "").startswith(("http://", "https://")):
		return
	имя = файл.file_name or (файл.file_url or "").rsplit("/", 1)[-1]
	тип = mimetypes.guess_type(имя)[0]
	if тип and mimetypes.guess_extension(тип):
		return
	if расширение(имя) not in {р.lower() for р in разрешённые}:
		_отказать(_("Такой тип файла загрузить нельзя"))


class ФайлПлатформы:
	"""Примесь к контроллеру `File`: проверки до его методов."""

	def before_insert(self):
		if _без_роли():
			if self.attached_to_doctype == СДАЧА and not self.flags.get(ФЛАГ_ФАЙЛА_СДАЧИ):
				_отказать(_("Файлы сдачи домашки добавляет только её сохранение"))
			if self.thumbnail_url or self.content_hash:
				_отказать(_("Миниатюру и отпечаток файла задаёт платформа"))
			if not self.content:
				_проверить_адрес(self)
			_проверить_расширение(self)
		super().before_insert()

	def before_validate(self):
		# Вставку проверяет `before_insert`; `in_insert` Frappe ставит до проверок.
		прежний = None if self.flags.in_insert else self.get_doc_before_save()
		if прежний is not None and _без_роли():
			if any(str(self.get(поле) or "") != str(прежний.get(поле) or "") for поле in НЕИЗМЕННЫЕ):
				_отказать(_("Адрес, миниатюра и приватность файла не меняются"))
			if СДАЧА in (self.attached_to_doctype, прежний.attached_to_doctype):
				_отказать(_("Файлы сдачи домашки не правятся"))
		родитель = getattr(super(), "before_validate", None)
		if родитель:
			родитель()

	def on_trash(self):
		if self.attached_to_doctype == СДАЧА and _без_роли():
			_отказать(_("Файлы сдачи домашки не удаляются"))
		super().on_trash()
