# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Значение клетки или поля по его типу: число, дата, выбор, ссылка на
строку; пустое — `None`, неверное — отказ.
"""

from __future__ import annotations

import re
from datetime import date

from lms_frappe_app.agent_learning.artifacts.codes import НЕВЕРНОЕ_ЗНАЧЕНИЕ
from lms_frappe_app.agent_learning.artifacts.formulas import _число
from lms_frappe_app.agent_learning.errors import Отказ

ДЛИНА_МАКС = 4000


def привести(описание: dict, значение, таблицы_данных: dict | None = None):
	"""Значение клетки или поля по его типу; пустое — `None`; неверное — отказ."""
	тип = описание["type"]
	где = {"column": описание["key"]}
	if тип == "formula":
		raise Отказ(НЕВЕРНОЕ_ЗНАЧЕНИЕ, "Колонку-формулу считает сервер", **где)
	if значение is None or (isinstance(значение, str) and not значение.strip()):
		return None
	if тип == "check":
		if isinstance(значение, str):
			return значение.strip().lower() in ("1", "true", "да", "yes", "x", "✓")
		return bool(значение)
	if тип in ("number", "scale"):
		число = значение
		if isinstance(значение, str):
			try:
				число = float(значение.strip().replace(",", ".").replace(" ", ""))
			except ValueError as ошибка:
				raise Отказ(НЕВЕРНОЕ_ЗНАЧЕНИЕ, "Нужно число", value=значение, **где) from ошибка
		if isinstance(число, bool) or not isinstance(число, (int, float)):
			raise Отказ(НЕВЕРНОЕ_ЗНАЧЕНИЕ, "Нужно число", value=значение, **где)
		число = _число(float(число))
		if тип == "scale":
			if not isinstance(число, int) or not описание["min"] <= число <= описание["max"]:
				raise Отказ(
					НЕВЕРНОЕ_ЗНАЧЕНИЕ,
					f"Балл по шкале — целое от {описание['min']} до {описание['max']}",
					value=значение,
					**где,
				)
		return число
	текст = str(значение).strip()
	if len(текст) > ДЛИНА_МАКС:
		raise Отказ(НЕВЕРНОЕ_ЗНАЧЕНИЕ, f"Не длиннее {ДЛИНА_МАКС} знаков", **где)
	if тип == "date":
		return _дата(текст, где)
	if тип == "select":
		for вариант in описание["options"]:
			if вариант.lower() == текст.lower():
				return вариант
		raise Отказ(НЕВЕРНОЕ_ЗНАЧЕНИЕ, "Выберите из списка", value=текст, options=описание["options"], **где)
	if тип == "ref":
		строки = (таблицы_данных or {}).get(описание["ref"]) or []
		for строка in строки:
			if строка.get("id", "").lower() == текст.lower():
				return строка["id"]
		raise Отказ(
			НЕВЕРНОЕ_ЗНАЧЕНИЕ, "Ссылка на строку, которой нет", value=текст, table=описание["ref"], **где
		)
	return текст


def _дата(текст: str, где: dict) -> str:
	"""ISO-дата; «31.12.2026» тоже принимается — так её пишут люди."""
	совпало = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", текст)
	if совпало:
		день, месяц, год = (int(ч) for ч in совпало.groups())
	else:
		совпало = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", текст)
		if not совпало:
			raise Отказ(НЕВЕРНОЕ_ЗНАЧЕНИЕ, "Дата — ГГГГ-ММ-ДД или ДД.ММ.ГГГГ", value=текст, **где)
		год, месяц, день = (int(ч) for ч in совпало.groups())
	try:
		return date(год, месяц, день).isoformat()
	except ValueError as ошибка:
		raise Отказ(НЕВЕРНОЕ_ЗНАЧЕНИЕ, "Такой даты нет", value=текст, **где) from ошибка


def пусто(значение) -> bool:
	return значение is None or значение == "" or значение is False
