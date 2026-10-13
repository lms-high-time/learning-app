# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Значение клетки или поля: текст. Пробелы по краям обрезаются, пустое —
`None` (клетка стирается), длиннее предела или не текст — отказ.
"""

from __future__ import annotations

from lms_frappe_app.agent_learning.artifacts.codes import НЕВЕРНОЕ_ЗНАЧЕНИЕ
from lms_frappe_app.agent_learning.errors import Отказ

ДЛИНА_МАКС = 4000


def привести(значение, **где) -> str | None:
	"""Значение клетки или поля текстом; пустое — `None`; неверное — отказ.

	Число принимается и становится текстом: агент пишет «12» и 12 одинаково.
	`где` — `column` или `field` — уходит в подробности отказа.
	"""
	if значение is None:
		return None
	if isinstance(значение, bool) or not isinstance(значение, str | int | float):
		raise Отказ(НЕВЕРНОЕ_ЗНАЧЕНИЕ, "Значение — текст", **где)
	текст = str(значение).strip()
	if len(текст) > ДЛИНА_МАКС:
		raise Отказ(НЕВЕРНОЕ_ЗНАЧЕНИЕ, f"Не длиннее {ДЛИНА_МАКС} знаков", max=ДЛИНА_МАКС, **где)
	return текст or None


def пусто(значение) -> bool:
	return значение is None or (isinstance(значение, str) and not значение.strip())
