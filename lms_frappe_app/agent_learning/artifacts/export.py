# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Документ наружу: таблицы для страницы и агента, markdown и книга Excel.
"""

from __future__ import annotations

from lms_frappe_app.agent_learning.artifacts.data import вычисленные, поля_документа
from lms_frappe_app.agent_learning.artifacts.formulas import _лексемы, _число
from lms_frappe_app.agent_learning.artifacts.schema import поля_схемы, таблицы_схемы
from lms_frappe_app.agent_learning.artifacts.values import пусто


def таблицы_документа(блоки: list, д: dict) -> dict[str, dict]:
	"""Таблицы для страницы и агента: колонки всех блоков, строки с формулами."""
	итог = {}
	for имя, таблица in таблицы_схемы(блоки).items():
		ряды = вычисленные(таблица, д, блоки)
		итог[имя] = {
			"name": имя,
			"title": таблица["title"],
			"owner": таблица["owner"],
			"prefix": таблица["prefix"],
			"columns": таблица["columns"],
			"views": таблица["views"],
			"rows": ряды,
			# Строки — нетронутая заготовка автора: страница зовёт такую таблицу
			# «заготовкой», а не «готово» (learning-services#342).
			"preset": bool(таблица["rows"]) and имя not in д["tables"],
			"markdown": markdown_таблицы(таблица["columns"], ряды),
		}
	return итог


def ячейка_текстом(колонка: dict, значение) -> str:
	if значение is None or значение == "":
		return ""
	if колонка["type"] == "check" or isinstance(значение, bool):
		return "да" if значение else ""
	return str(_число(значение))


def markdown_таблицы(колонки: list[dict], ряды: list[dict]) -> str:
	if not ряды:
		return ""

	def клетка(текст: str) -> str:
		return текст.replace("|", "\\|").replace("\n", " ")

	шапка = ["ID", *(к["title"] for к in колонки)]
	линии = [
		"| " + " | ".join(клетка(ш) for ш in шапка) + " |",
		"|" + " --- |" * len(шапка),
	]
	for р in ряды:
		линии.append(
			"| "
			+ " | ".join([р["id"], *(клетка(ячейка_текстом(к, р.get(к["key"]))) for к in колонки)])
			+ " |"
		)
	return "\n".join(линии)


def markdown_полей(описания: list[dict], значения: dict) -> str:
	"""Поля строками «название: значение»; `значения` — `поля_документа`,
	с посчитанными формулами."""
	линии = []
	for п in описания:
		значение = значения.get(п["key"])
		if not пусто(значение):
			линии.append(f"- {п['title']}: {ячейка_текстом(п, значение)}")
	return "\n".join(линии)


def книга_xlsx(название: str, блоки: list, д: dict) -> bytes:
	"""Документ книгой Excel: лист «Шапка» с полями и лист на таблицу.

	Формулы колонок уходят формулами Excel — реестр продолжают вести с
	командой, и ранг считается сам. Формула с and/or/not уходит значением:
	у Excel другой синтаксис логики, и переводить его ради редкого случая
	незачем. Формула поля в шапке — значением: поля правят в документе, а не
	в книге.
	"""
	import io

	from openpyxl import Workbook
	from openpyxl.styles import Alignment, Font, PatternFill
	from openpyxl.utils import get_column_letter

	книга = Workbook()
	шапка = книга.active
	шапка.title = "Шапка"
	шапка.append([название])
	шапка["A1"].font = Font(bold=True, size=14)
	адреса_полей: dict[str, str] = {}
	значения = поля_документа(блоки, д)
	for имя, поле in поля_схемы(блоки).items():
		шапка.append([поле["title"], значения[имя]])
		адреса_полей[имя] = f"'Шапка'!$B${шапка.max_row}"
	шапка.column_dimensions["A"].width = 40
	шапка.column_dimensions["B"].width = 30

	заливка = PatternFill("solid", fgColor="EEF2F6")
	for имя, таблица in таблицы_схемы(блоки).items():
		лист = книга.create_sheet((таблица["title"] or имя)[:31])
		колонки = таблица["columns"]
		буквы = {"id": "A"}
		for номер, колонка in enumerate(колонки, start=2):
			буквы[колонка["key"]] = get_column_letter(номер)
		лист.append(["ID", *(к["title"] for к in колонки)])
		for клетка in лист[1]:
			клетка.font = Font(bold=True)
			клетка.fill = заливка
			клетка.alignment = Alignment(wrap_text=True, vertical="top")
		for номер_строки, р in enumerate(вычисленные(таблица, д, блоки), start=2):
			ряд = [р["id"]]
			for колонка in колонки:
				if колонка["type"] == "formula":
					ряд.append(
						_формула_excel(колонка["formula"], буквы, адреса_полей, номер_строки)
						or р.get(колонка["key"])
					)
				elif колонка["type"] == "check":
					ряд.append(bool(р.get(колонка["key"])))
				else:
					ряд.append(р.get(колонка["key"]))
			лист.append(ряд)
		лист.freeze_panes = "B2"
		for номер, колонка in enumerate(колонки, start=2):
			широкая = колонка["type"] in ("text", "longtext")
			лист.column_dimensions[get_column_letter(номер)].width = 36 if широкая else 14
		for ряд in лист.iter_rows(min_row=2):
			for клетка in ряд:
				клетка.alignment = Alignment(wrap_text=True, vertical="top")

	поток = io.BytesIO()
	книга.save(поток)
	return поток.getvalue()


def _формула_excel(формула: str, буквы: dict, поля: dict, строка: int) -> str | None:
	части = []
	for вид, значение in _лексемы(формула):
		if вид == "op" and значение in ("and", "or", "not"):
			return None
		if вид == "name":
			if значение in буквы:
				части.append(f"{буквы[значение]}{строка}")
			elif значение in поля:
				части.append(поля[значение])
			else:
				return None
		elif значение == "==":
			части.append("=")
		elif значение == "!=":
			части.append("<>")
		else:
			части.append(значение)
	return "=" + "".join(части)
