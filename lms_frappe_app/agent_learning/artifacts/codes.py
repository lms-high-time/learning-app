# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Коды отказов документа курса — одним модулем на весь пакет.

`Why:` коды бросают и схема, и данные, и запись, а модули пакета опираются
друг на друга: код, объявленный в любом из них, замкнул бы импорты в круг.
Здесь он ниже всех модулей пакета.
"""

НЕВЕРНАЯ_СХЕМА = "artifact_invalid_spec"
НЕВЕРНОЕ_ЗНАЧЕНИЕ = "artifact_invalid_value"
НЕТ_КОЛОНКИ = "artifact_unknown_column"
НЕТ_ПОЛЯ = "artifact_unknown_field"
НЕТ_СТРОКИ = "artifact_unknown_row"
СТРОК_СЛИШКОМ_МНОГО = "artifact_too_many_rows"
НЕ_ТОТ_РАЗДЕЛ = "artifact_wrong_section"
НЕЧЕГО_ПИСАТЬ = "artifact_nothing_to_write"
ОЧИСТКА_С_ЗАПИСЬЮ = "artifact_clear_with_content"

АРТЕФАКТ_НЕ_НАЙДЕН = "artifact_not_found"
БЛОК_НЕ_НАЙДЕН = "artifact_block_not_found"
