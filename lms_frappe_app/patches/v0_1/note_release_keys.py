# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Заметки автора — на ключи релиза (learning-services#512).

Место заметки теперь — адрес по ключам релиза, и заметка помнит релиз, к
которому написана. Старые места переносятся так:

- `course` и `lesson` с уроком курса из релиза — `course` и
  `lesson.<ключ>` (ключ — по истории релизов курса, `index.известные`);
- `block.<документ>/<ключ>` документа действующего релиза — `section.<ключ>`;
- остальное ключей не имеет: открытая или сделанная заметка принимается с
  ответом `ОТВЕТ_АРХИВА`, принятая остаётся как есть. `Why:` не `done` —
  `done` просит автора проверить правку, а править здесь нечего;
- заметки курсов, которых больше нет, удаляются с нитями.

Перенесённая получает действующий релиз курса и с ним пропускается при
повторном запуске; принятая не трогается — повторный запуск ничего не
меняет. Таблицы и колонку `lesson` патч проверяет сам и читает сырым SQL:
на свежем сайте или после удаления колонок их может не быть.
"""

import frappe

from lms_frappe_app.agent_learning.releases import index

ЗАМЕТКА = "Agent Author Note"
ОТВЕТ = "Agent Note Reply"
ОТВЕТ_АРХИВА = "курс собран заново: места больше нет"


def execute():
	if not frappe.db.table_exists(ЗАМЕТКА, cached=False):
		print("note_release_keys: заметок нет — переносить нечего")
		return
	колонки = set(frappe.db.get_table_columns(ЗАМЕТКА))
	if "release" not in колонки:
		print("note_release_keys: у заметок нет поля release — сначала синхронизация доктайпа")
		return
	есть_урок = "lesson" in колонки
	урок = "lesson" if есть_урок else "NULL"
	заметки = frappe.db.sql(
		f"SELECT name, course, {урок} AS lesson, target, status, `release` FROM `tab{ЗАМЕТКА}` "
		"ORDER BY creation ASC",
		as_dict=True,
	)
	if not заметки:
		print("note_release_keys: заметок нет — переносить нечего")
		return
	по_курсам: dict[str, list] = {}
	for заметка in заметки:
		по_курсам.setdefault(заметка.course, []).append(заметка)
	курсы = {
		к.name: к.active_release
		for к in frappe.get_all(
			"LMS Course", filters={"name": ("in", list(по_курсам))}, fields=["name", "active_release"]
		)
	}
	ответы = frappe.db.table_exists(ОТВЕТ, cached=False)
	for курс, список in по_курсам.items():
		if курс not in курсы:
			_удалить([з.name for з in список], ответы)
			print(f"note_release_keys: {курс} — перенесено 0, в архиве 0, удалено {len(список)}")
			continue
		перенесено = в_архиве = 0
		перевод = _перевод(курс, курсы[курс])
		for заметка in список:
			if заметка.release:
				continue
			новое = перевод(заметка)
			if новое:
				поля = {"target": новое[0], "release": курсы[курс]}
				if есть_урок:
					поля["lesson"] = новое[1]
				frappe.db.set_value(ЗАМЕТКА, заметка.name, поля, update_modified=False)
				перенесено += 1
			elif заметка.status != "accepted":
				_в_архив(заметка.name, ответы)
				в_архиве += 1
		print(f"note_release_keys: {курс} — перенесено {перенесено}, в архиве {в_архиве}, удалено 0")


def _перевод(курс: str, релиз: str | None):
	"""Функция: старая заметка → (место, урок) по ключам релиза или `None`."""
	if not релиз:
		return lambda заметка: None
	уроки = {запись: ключ for ключ, запись in index.известные(курс)["lessons"].items()}
	# Битая ссылка на действующий релиз миграцию не останавливает: разделов просто нет.
	документ = (index.сведения(релиз) or {}).get("document_key")

	def перевод(заметка) -> tuple[str, str | None] | None:
		место = (заметка.target or "").strip()
		if место == "course":
			return "course", None
		if место == "lesson" and заметка.lesson in уроки:
			return f"lesson.{уроки[заметка.lesson]}", заметка.lesson
		if место.startswith("block.") and документ:
			slug, _, ключ = место[len("block.") :].partition("/")
			if slug == документ and ключ:
				return f"section.{ключ}", None
		return None

	return перевод


def _в_архив(заметка: str, ответы: bool) -> None:
	frappe.db.set_value(ЗАМЕТКА, заметка, "status", "accepted", update_modified=False)
	if not ответы:
		return
	номер = frappe.db.count(ОТВЕТ, {"parenttype": ЗАМЕТКА, "parent": заметка})
	frappe.get_doc(
		{
			"doctype": ОТВЕТ,
			"parenttype": ЗАМЕТКА,
			"parentfield": "replies",
			"parent": заметка,
			"idx": номер + 1,
			"via": "agent",
			"author": "Administrator",
			"created_at": frappe.utils.now_datetime(),
			"text": ОТВЕТ_АРХИВА,
		}
	).db_insert()


def _удалить(заметки: list[str], ответы: bool) -> None:
	if ответы:
		frappe.db.delete(ОТВЕТ, {"parenttype": ЗАМЕТКА, "parent": ("in", заметки)})
	frappe.db.delete(ЗАМЕТКА, {"name": ("in", заметки)})
