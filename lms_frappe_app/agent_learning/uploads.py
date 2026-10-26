# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Файлы учеников: предел размера, допустимые типы, приватный `File` при
записи ученика, сведения о файлах для ответа, байты из base64.

Файл ученика — приватный `File`, привязанный к его записи (сдаче домашки):
права на файл Frappe берёт у записи, и видит его тот, кто видит запись.
"""

from __future__ import annotations

import base64
import binascii
import io
import zipfile
from collections.abc import Callable

import frappe

from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	настройка,
)


def расширение(имя: str) -> str:
	"""Расширение файла без точки, в нижнем регистре; без точки в имени — пусто."""
	return имя.rsplit(".", 1)[-1].lower() if "." in имя else ""


#: Флаг записи `File`, вставленной сохранением сдачи: другим путём файл к сдаче
#: не добавить (`file_guard.ФайлПлатформы`).
ФЛАГ_ФАЙЛА_СДАЧИ = "homework_file"


def _начало(*подписи: bytes) -> Callable[[bytes], bool]:
	return lambda данные: данные.startswith(подписи)


def _pdf(данные: bytes) -> bool:
	# Заголовок PDF читатели ищут в первом килобайте, а не только в начале.
	return b"%PDF-" in данные[:1024]


def _webp(данные: bytes) -> bool:
	return данные[:4] == b"RIFF" and данные[8:12] == b"WEBP"


#: Бренды контейнера HEIF. `Why:` основной бренд у снимков разных камер разный
#: (`MiHE` и др.), а совместимые бренды в `ftyp` называют HEIF явно.
БРЕНДЫ_HEIF = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


def _heif(данные: bytes) -> bool:
	if данные[4:8] != b"ftyp":
		return False
	размер = int.from_bytes(данные[:4], "big")
	бренды = [данные[8:12]] + [данные[i : i + 4] for i in range(16, min(размер, 64), 4)]
	return any(бренд in БРЕНДЫ_HEIF for бренд in бренды)


def _архив(данные: bytes) -> zipfile.ZipFile | None:
	if not данные.startswith(b"PK\x03\x04"):
		return None
	try:
		return zipfile.ZipFile(io.BytesIO(данные))
	except Exception:
		# Битый, зашифрованный или с неизвестным сжатием архив — не тот тип.
		return None


def _ooxml(папка: str) -> Callable[[bytes], bool]:
	"""Документ Office: zip с `[Content_Types].xml` и частями своей программы."""

	def проверка(данные: bytes) -> bool:
		архив = _архив(данные)
		if архив is None:
			return False
		имена = архив.namelist()
		if "[Content_Types].xml" not in имена or not any(имя.startswith(папка) for имя in имена):
			return False
		# Документ с макросами не принимается и под расширением без «m».
		return not any(имя.lower().endswith("vbaproject.bin") for имя in имена)

	return проверка


def _odf(тип: str) -> Callable[[bytes], bool]:
	"""Документ OpenDocument: zip, в котором `mimetype` — тип документа."""

	def проверка(данные: bytes) -> bool:
		архив = _архив(данные)
		if архив is None:
			return False
		try:
			# Только начало: заявленный размер члена архива не проверяется
			# до чтения, а `read` распаковал бы его целиком.
			with архив.open("mimetype") as член:
				return член.read(128).strip() == тип.encode()
		except Exception:
			return False

	return проверка


def _текст(данные: bytes) -> bool:
	# Кодировку не угадываем: CSV из Excel бывает и в cp1251. Нулевой байт —
	# признак двоичного файла, кроме UTF-16 с меткой порядка байтов.
	if данные.startswith((b"\xff\xfe", b"\xfe\xff")):
		try:
			данные.decode("utf-16")
		except UnicodeDecodeError:
			return False
		return True
	return b"\x00" not in данные


#: Какие файлы принимает сдача домашки и как сверяется содержимое: расширение
#: из списка, а первые байты — того типа, который расширение обещает. `Why:`
#: куратор открывает файл ученика у себя (ASVS 5.2.2); исполняемое, разметку,
#: архивы и форматы Office с макросами платформа не принимает.
ТИПЫ_ДОМАШКИ: dict[str, Callable[[bytes], bool]] = {
	"pdf": _pdf,
	"docx": _ooxml("word/"),
	"xlsx": _ooxml("xl/"),
	"pptx": _ooxml("ppt/"),
	"odt": _odf("application/vnd.oasis.opendocument.text"),
	"ods": _odf("application/vnd.oasis.opendocument.spreadsheet"),
	"odp": _odf("application/vnd.oasis.opendocument.presentation"),
	"txt": _текст,
	"md": _текст,
	"csv": _текст,
	"png": _начало(b"\x89PNG\r\n\x1a\n"),
	"jpg": _начало(b"\xff\xd8\xff"),
	"jpeg": _начало(b"\xff\xd8\xff"),
	"gif": _начало(b"GIF87a", b"GIF89a"),
	"webp": _webp,
	"heic": _heif,
	"heif": _heif,
}


def тип_принят(имя: str, данные: bytes) -> bool:
	"""Расширение — из `ТИПЫ_ДОМАШКИ`, и содержимое ему соответствует."""
	проверка = ТИПЫ_ДОМАШКИ.get(расширение(имя))
	return bool(проверка and проверка(данные))


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
	файл = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": имя,
			"content": данные,
			"is_private": 1,
			"attached_to_doctype": документ.doctype,
			"attached_to_name": документ.name,
		}
	)
	файл.flags[ФЛАГ_ФАЙЛА_СДАЧИ] = True
	return файл


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
