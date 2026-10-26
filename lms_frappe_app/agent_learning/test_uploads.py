# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Файлы учеников без базы: байты из base64 (`uploads.из_base64`), допустимые
типы (`uploads.тип_принят`)."""

import base64
import io
import mimetypes
import re
import zipfile
from pathlib import Path

from frappe.tests import UnitTestCase

from lms_frappe_app.agent_learning import uploads


class TestИзBase64(UnitTestCase):
	def test_байты_из_base64(self):
		"""Пустое и битое — пустые байты: отказ «файл пуст» ставит вызывающий."""
		abc = base64.b64encode(b"abc").decode()
		случаи = (
			("base64", abc, b"abc"),
			("пробелы и перевод строки по краям", f"  {abc}\n", b"abc"),
			("data-адрес браузера", f"data:text/csv;base64,{abc}", b"abc"),
			("data без запятой — не адрес", f"data:{abc}", b""),
			("None", None, b""),
			("пустая строка", "", b""),
			("только пробелы", "   ", b""),
			("битый base64", "не base64!", b""),
			("без выравнивания", abc[:-1], b""),
			("перевод строки внутри", f"{abc[:2]}\n{abc[2:]}", b""),
		)
		for что, вход, ожидание in случаи:
			with self.subTest(что):
				self.assertEqual(uploads.из_base64(вход), ожидание)


def _zip(**члены: bytes) -> bytes:
	буфер = io.BytesIO()
	with zipfile.ZipFile(буфер, "w") as архив:
		for имя, данные in члены.items():
			архив.writestr(имя, данные)
	return буфер.getvalue()


def _ooxml(папка: str) -> bytes:
	return _zip(**{"[Content_Types].xml": b"<Types/>", f"{папка}document.xml": b"<x/>"})


def _odf(тип: str) -> bytes:
	return _zip(mimetype=тип.encode(), **{"content.xml": b"<x/>"})


class TestТипПринят(UnitTestCase):
	def test_расширение_и_содержимое(self):
		"""Расширение из списка, а первые байты — того типа, который оно обещает."""
		png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
		случаи = (
			("pdf", "отчёт.pdf", b"%PDF-1.7\n...", True),
			("pdf с мусором перед заголовком", "a.pdf", b"\n\n%PDF-1.4", True),
			("pdf без заголовка", "a.pdf", b"<html>", False),
			("docx", "a.docx", _ooxml("word/"), True),
			("xlsx под видом docx", "a.docx", _ooxml("xl/"), False),
			("xlsx", "a.xlsx", _ooxml("xl/"), True),
			("pptx", "a.pptx", _ooxml("ppt/"), True),
			("docx — просто zip", "a.docx", _zip(**{"a.txt": b"x"}), False),
			("docx — битый zip", "a.docx", b"PK\x03\x04" + "мусор".encode(), False),
			("odt", "a.odt", _odf("application/vnd.oasis.opendocument.text"), True),
			("ods под видом odt", "a.odt", _odf("application/vnd.oasis.opendocument.spreadsheet"), False),
			("ods", "a.ods", _odf("application/vnd.oasis.opendocument.spreadsheet"), True),
			("odp", "a.odp", _odf("application/vnd.oasis.opendocument.presentation"), True),
			("txt", "a.txt", "текст".encode(), True),
			("csv в cp1251", "a.csv", "имя;роль".encode("cp1251"), True),
			("txt в UTF-16 с меткой", "a.txt", "текст".encode("utf-16"), True),
			("md с нулевым байтом", "a.md", b"#\x00", False),
			("png", "a.png", png, True),
			("расширение в верхнем регистре", "A.PNG", png, True),
			("png с чужим содержимым", "a.png", b"\xff\xd8\xff\xe0", False),
			("jpg", "a.jpg", b"\xff\xd8\xff\xe0", True),
			("jpeg", "a.jpeg", b"\xff\xd8\xff\xdb", True),
			("gif", "a.gif", b"GIF89a....", True),
			("webp", "a.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ", True),
			("heic", "a.heic", b"\x00\x00\x00\x18ftypheic", True),
			("heic без ftyp", "a.heic", b"\x00\x00\x00\x18moovheic", False),
			("исполняемый", "a.exe", b"MZ", False),
			("разметка", "a.html", b"<html>", False),
			("svg", "a.svg", b"<svg/>", False),
			("архив", "a.zip", _zip(**{"a.txt": b"x"}), False),
			("Office с макросами", "a.docm", _ooxml("word/"), False),
			("без расширения", "README", b"text", False),
		)
		for что, имя, данные, принят in случаи:
			with self.subTest(что):
				self.assertEqual(uploads.тип_принят(имя, данные), принят)

	def test_контракт_перечисляет_те_же_типы(self):
		"""Список типов в `CONTRACT.md` (`submit_homework`) — тот же, что в коде."""
		контракт = (Path(__file__).resolve().parents[2] / "CONTRACT.md").read_text(encoding="utf-8")
		абзац = re.search(r"Типы — (.+?);", контракт, re.S)
		self.assertIsNotNone(абзац)
		self.assertEqual(set(re.findall(r"`(\w+)`", абзац.group(1))), set(uploads.ТИПЫ_ДОМАШКИ))

	def test_типы_домашки_проходят_проверку_frappe(self):
		"""Каждый тип домашки есть в `allowed_file_extensions` сайта — в той форме,
		в какой его выводит `File.set_file_type`."""
		from lms_frappe_app.install import ТИПЫ_ФАЙЛОВ

		for тип in uploads.ТИПЫ_ДОМАШКИ:
			with self.subTest(тип):
				mime = mimetypes.guess_type(f"a.{тип}")[0]
				self.assertIn(mimetypes.guess_extension(mime).lstrip(".").upper(), ТИПЫ_ФАЙЛОВ)
