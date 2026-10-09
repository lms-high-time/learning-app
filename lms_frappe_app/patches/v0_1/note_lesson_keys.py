# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Ключ урока — в заметке автора (learning-services#514).

Урок места заметка хранит ключом (`Agent Author Note.lesson_key`), а не
ссылкой на запись урока Learning: поле `lesson` снято. Патч заполняет пустой
ключ ключом на записи урока заметки (`Course Lesson.lesson_key`, его ставит
патч `release_record_keys`); у заметки, чей урок без ключа, ключ остаётся
пустым. Пишет только в пустое поле: повторный запуск ничего не меняет.

Колонку `lesson` схема больше не знает: патч читает её сырым SQL, а после
заполнения удаляет (`ALTER TABLE … DROP COLUMN IF EXISTS`). `Why:` миграция с
`--skip-failing` идёт дальше упавшего патча; без ключей на записях уроков
заметки остались бы без ключа, а колонка, по которой их заполнить, ушла бы.
Пока `release_record_keys` не выполнен (`patch_log.выполнен`), колонка
остаётся, и патч печатает почему. Колонки нет (свежий сайт или повторный
запуск) — патч ничего не делает.
"""

import frappe

from lms_frappe_app.patches.v0_1.drop_removed_columns import колонки
from lms_frappe_app.patches.v0_1.patch_log import выполнен

ЗАМЕТКА = "Agent Author Note"
УРОК = "Course Lesson"
#: Патч, который ставит ключи на записи уроков: без него колонка не удаляется.
ЖДЁТ = "release_record_keys"


def execute():
	if "lesson" not in колонки(ЗАМЕТКА):
		print("note_lesson_keys: колонки lesson нет — заполнять нечего")
		return
	for курс, (записано, без_ключа) in sorted(заполнить().items()):
		print(f"note_lesson_keys: {курс} — ключей записано {записано}, урок без ключа у {без_ключа}")
	if not выполнен(ЖДЁТ):
		print(f"note_lesson_keys: колонка lesson оставлена — патч {ЖДЁТ} ещё не выполнен")
		return
	frappe.db.sql_ddl(f"ALTER TABLE `tab{ЗАМЕТКА}` DROP COLUMN IF EXISTS `lesson`")
	frappe.client_cache.delete_value(f"table_columns::tab{ЗАМЕТКА}")
	print("note_lesson_keys: колонка lesson удалена")


def заполнить() -> dict[str, tuple[int, int]]:
	"""Пустые ключи заметок с уроком; курс → (записано, урок без ключа)."""
	уроки = dict(frappe.db.sql(f"SELECT name, lesson FROM `tab{ЗАМЕТКА}` WHERE ifnull(lesson, '') != ''"))
	заметки = (
		frappe.get_all(
			ЗАМЕТКА,
			filters={"name": ("in", list(уроки)), "lesson_key": ("is", "not set")},
			fields=["name", "course"],
		)
		if уроки
		else []
	)
	if not заметки:
		return {}
	ключи = dict(
		frappe.get_all(
			УРОК,
			filters={"name": ("in", list({уроки[з.name] for з in заметки})), "lesson_key": ("is", "set")},
			fields=["name", "lesson_key"],
			as_list=True,
		)
	)
	итог: dict[str, tuple[int, int]] = {}
	for заметка in заметки:
		записано, без_ключа = итог.get(заметка.course, (0, 0))
		if ключ := ключи.get(уроки[заметка.name]):
			frappe.db.set_value(ЗАМЕТКА, заметка.name, "lesson_key", ключ, update_modified=False)
			записано += 1
		else:
			без_ключа += 1
		итог[заметка.course] = (записано, без_ключа)
	return итог
