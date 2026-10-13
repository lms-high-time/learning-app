# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Файлы учеников: предел размера, приватный `File` при записи ученика,
сведения о файлах для ответа, байты из base64.

Файл ученика — приватный `File`, привязанный к его записи (сдаче домашки):
права на файл Frappe берёт у записи, и видит его тот, кто видит запись.
"""

from __future__ import annotations

import base64
import binascii

import frappe

from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	настройка,
)


def расширение(имя: str) -> str:
	"""Расширение файла без точки, в нижнем регистре; без точки в имени — пусто."""
	return имя.rsplit(".", 1)[-1].lower() if "." in имя else ""


def предел_байт() -> int:
	"""Предел размера одного файла — `artifact_file_max_mb` из настроек, в байтах."""
	return int(настройка("artifact_file_max_mb", 10)) * 1024 * 1024


def из_base64(содержимое: str | None) -> bytes:
	"""Байты файла из base64; пустое или битое — пустые байты: отказ «файл пуст»
	ставит вызывающий. Префикс `data:…;base64,` снимается: так файл отдаёт браузер."""
	текст = (содержимое or "").strip()
	if текст.startswith("data:") and "," in текст:
		текст = текст.split(",", 1)[1]
	try:
		return base64.b64decode(текст, validate=True) if текст else b""
	except (binascii.Error, ValueError):
		return b""


def новый_файл(документ, имя: str, данные: bytes):
	"""Приватный `File`, привязанный к записи ученика, — ещё не вставленный."""
	return frappe.get_doc(
		{
			"doctype": "File",
			"file_name": имя,
			"content": данные,
			"is_private": 1,
			"attached_to_doctype": документ.doctype,
			"attached_to_name": документ.name,
		}
	)


def сведения_о_файлах(имена: list[str]) -> dict[str, dict]:
	"""Что показать о файлах — одним запросом: имя, тип, размер, время загрузки, адрес."""
	if not имена:
		return {}
	return {
		запись.name: {
			"name": запись.file_name,
			"type": расширение(запись.file_name or ""),
			"size": запись.file_size or 0,
			"uploaded_at": запись.creation.isoformat() if запись.creation else None,
			"url": запись.file_url,
		}
		for запись in frappe.get_all(
			"File",
			filters={"name": ("in", имена)},
			fields=["name", "file_name", "file_size", "creation", "file_url"],
		)
	}
