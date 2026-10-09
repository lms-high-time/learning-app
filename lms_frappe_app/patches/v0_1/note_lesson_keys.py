# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Ключ урока — в заметке автора (learning-services#514).

Урок места заметка хранит ключом (`Agent Author Note.lesson_key`), а не
ссылкой на запись урока Learning: поле `lesson` снято. Патч заполняет пустой
ключ ключом на записи урока заметки (`Course Lesson.lesson_key`, его ставит
патч `release_record_keys`). У заметки с релизом, чья запись урока осталась
без ключа (урок проиграл соответствие в `release_record_keys`), ключ берётся
из её адреса по индексу её релиза — как его записал бы `add_note`
(`places.Места(release).место(notes.разобрать_адрес(target))`). Индекс к
этому моменту ещё цел: освобождение содержимого (`free_release_content`)
ждёт этот патч. Пишет только в пустое поле: повторный запуск ничего не
меняет.

Что теряется с колонкой: заметка, урок которой не нашёлся ни по ключу записи,
ни по адресу в индексе её релиза, — заметки с висящей ссылкой на урок и
архивные заметки на курсах без релиза — остаётся без ключа и без ссылки на
урок. Сколько таких по курсу, патч печатает до удаления колонки («урок без
ключа у N»).

Колонку `lesson` схема больше не знает: патч читает её сырым SQL, а после
заполнения удаляет (`ALTER TABLE … DROP COLUMN IF EXISTS`). `Why:` миграция с
`--skip-failing` идёт дальше упавшего патча; без ключей на записях уроков
заметки остались бы без ключа, а колонка, по которой их заполнить, ушла бы.
Пока `release_record_keys` не выполнен (`patch_log.выполнен`), колонка
остаётся, и патч печатает почему. Колонки нет (свежий сайт или повторный
запуск) — патч ничего не делает.

Восстановление, если `release_record_keys` был пропущен: после исправления —
`bench --site <сайт> run-patch --force` сначала для `release_record_keys`,
затем для `note_lesson_keys`.
"""

import frappe

from lms_frappe_app.agent_learning import notes
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases import places
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
	for курс, (по_уроку, по_адресу, без_ключа) in sorted(заполнить().items()):
		print(
			f"note_lesson_keys: {курс} — ключей записано {по_уроку + по_адресу} "
			f"(по адресу в релизе заметки {по_адресу}), урок без ключа у {без_ключа}"
		)
	if not выполнен(ЖДЁТ):
		print(f"note_lesson_keys: колонка lesson оставлена — патч {ЖДЁТ} ещё не выполнен")
		return
	frappe.db.sql_ddl(f"ALTER TABLE `tab{ЗАМЕТКА}` DROP COLUMN IF EXISTS `lesson`")
	frappe.client_cache.delete_value(f"table_columns::tab{ЗАМЕТКА}")
	print("note_lesson_keys: колонка lesson удалена")


def заполнить() -> dict[str, tuple[int, int, int]]:
	"""Пустые ключи заметок с уроком; курс → (по ключу записи урока, по адресу в релизе заметки, без ключа)."""
	уроки = dict(frappe.db.sql(f"SELECT name, lesson FROM `tab{ЗАМЕТКА}` WHERE ifnull(lesson, '') != ''"))
	заметки = (
		frappe.get_all(
			ЗАМЕТКА,
			filters={"name": ("in", list(уроки)), "lesson_key": ("is", "not set")},
			fields=["name", "course", "release", "target"],
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
	места: dict[str, places.Места] = {}
	итог: dict[str, tuple[int, int, int]] = {}
	for заметка in заметки:
		по_уроку, по_адресу, без_ключа = итог.get(заметка.course, (0, 0, 0))
		if ключ := ключи.get(уроки[заметка.name]):
			по_уроку += 1
		elif ключ := _по_адресу(заметка, места):
			по_адресу += 1
		else:
			без_ключа += 1
		if ключ:
			frappe.db.set_value(ЗАМЕТКА, заметка.name, "lesson_key", ключ, update_modified=False)
		итог[заметка.course] = (по_уроку, по_адресу, без_ключа)
	return итог


def _по_адресу(заметка, места: dict) -> str | None:
	"""Ключ урока места заметки по индексу её релиза — как его записал бы `add_note`; нет — `None`.

	Места — одни на релиз: индекс релиза читается один раз на все его заметки.
	Релиз освобождён (`retention`) — индекса и снимка нет, ключ не найти: `None`.
	"""
	# `is not null`, а не `("is", "set")`: см. `retention`.
	if not заметка.release or not frappe.db.sql(
		"select 1 from `tabAgent Course Release` where name = %s and snapshot is not null", заметка.release
	):
		return None
	try:
		адрес = notes.разобрать_адрес(заметка.target)
	except Отказ:
		return None
	if заметка.release not in места:
		места[заметка.release] = places.Места(заметка.release)
	return места[заметка.release].место(адрес)["lesson_key"]
