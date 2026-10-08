# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Выполнен ли патч приложения — для патчей, которые удаляют то, что читает
другой патч (learning-services#512).

Не патч: в `patches.txt` его нет.
"""

from frappe.modules.patch_handler import executed

#: Модули патчей этой версии.
ПАКЕТ = "lms_frappe_app.patches.v0_1"


def полное_имя(патч: str) -> str:
	"""Строка патча в `patches.txt` и `Patch Log`: `old_quiz_attempts` → полное имя модуля."""
	return f"{ПАКЕТ}.{патч}"


def выполнен(патч: str) -> bool:
	"""Патч `патч` (имя модуля в `patches.v0_1`) отработал без ошибки.

	`Why:` миграция с `--skip-failing` пишет упавший патч в `Patch Log` тоже —
	со `skipped=1`, — и запись в журнале ещё не значит, что патч отработал.
	Выполненным его считает только `skipped=0`: так решает и сам Frappe
	(`patch_handler.executed`), он здесь и зовётся.
	"""
	return bool(executed(полное_имя(патч)))
