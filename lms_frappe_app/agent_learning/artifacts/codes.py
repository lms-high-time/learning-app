# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Коды отказов документа курса — одним модулем на весь пакет.

`Why:` `artifact_invalid_spec` бросают и формулы, и схема, и холст, а схема
сама опирается на формулы: код, объявленный в любом из них, замкнул бы
импорты в круг. Здесь он ниже всех модулей пакета.
"""

НЕВЕРНАЯ_СХЕМА = "artifact_invalid_spec"
НЕВЕРНОЕ_ЗНАЧЕНИЕ = "artifact_invalid_value"
НЕТ_КОЛОНКИ = "artifact_unknown_column"
НЕТ_ПОЛЯ = "artifact_unknown_field"
НЕТ_СТРОКИ = "artifact_unknown_row"
СТРОКИ_НЕ_ЗАВОДИТ = "artifact_rows_owner_only"
ФЛАЖКОВ_СЛИШКОМ_МНОГО = "artifact_too_many_checked"
СТРОК_СЛИШКОМ_МНОГО = "artifact_too_many_rows"

ВИД_НЕ_ТОТ = "artifact_kind_mismatch"
ФАЙЛ_НЕ_ТОГО_ТИПА = "artifact_file_type"
ФАЙЛ_СЛИШКОМ_БОЛЬШОЙ = "artifact_file_too_large"
ФАЙЛА_НЕТ = "artifact_file_missing"
НЕВЕРНАЯ_ССЫЛКА = "artifact_invalid_url"
НЕВЕРНАЯ_ТАБЛИЦА = "artifact_invalid_table"

АРТЕФАКТ_НЕ_НАЙДЕН = "artifact_not_found"
БЛОК_НЕ_НАЙДЕН = "artifact_block_not_found"
ПУСТОЙ_БЛОК = "artifact_content_required"
ОЧИСТКА_С_ТЕКСТОМ = "artifact_clear_with_content"
НЕВЕРНЫЙ_ВИД_БЛОКА = "invalid_block_kind"
