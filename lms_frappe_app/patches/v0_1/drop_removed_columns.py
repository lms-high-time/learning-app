# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Колонки снятых полей уходят из базы (learning-services#512).

Поля сняты из схем, но миграция Frappe колонки снятых полей не удаляет: они
остаются в таблице с данными, которых схема больше не знает. Уходят:

- `Agent Course Report.lesson_directive`, `.question` — ссылки на директиву
  урока и на вопрос `LMS Question`; репорт ссылается на релиз и ключ вопроса;
- `Agent Author Note.baseline` — снимок места заметки; заметки перенесены на
  ключи релиза патчем `note_release_keys`, он идёт раньше;
- `Agent Quiz Attempt.quiz`, `.submission`, `Agent Quiz Answer.question`,
  `Agent Quiz Event.question` — квиз по `LMS Quiz`; сдачи Learning удаляет
  раньше патч `old_quiz_attempts`;
- `Agent Course Artifact.template`, `.template_version`, `.overlay` — привязка
  схемы документа к шаблону.

Индексы на этих колонках одноколоночные: `DROP COLUMN` удаляет их вместе с
колонкой. Колонки, которой уже нет, и таблицы, которой нет, патч не трогает —
повторный запуск ничего не делает.
"""

import frappe

#: Доктайп → колонки снятых полей.
КОЛОНКИ = {
	"Agent Course Report": ("lesson_directive", "question"),
	"Agent Author Note": ("baseline",),
	"Agent Quiz Attempt": ("quiz", "submission"),
	"Agent Quiz Answer": ("question",),
	"Agent Quiz Event": ("question",),
	"Agent Course Artifact": ("template", "template_version", "overlay"),
}


def execute():
	for доктайп, поля in КОЛОНКИ.items():
		есть = колонки(доктайп)
		удалить = [поле for поле in поля if поле in есть]
		for поле in удалить:
			frappe.db.sql_ddl(f"ALTER TABLE `tab{доктайп}` DROP COLUMN `{поле}`")
		frappe.client_cache.delete_value(f"table_columns::tab{доктайп}")
		print(f"drop_removed_columns: {доктайп} — удалены колонки: {', '.join(удалить) or 'нет'}")


def колонки(доктайп: str) -> set[str]:
	"""Колонки таблицы доктайпа по базе, мимо кеша; таблицы нет — пусто."""
	frappe.client_cache.delete_value(f"table_columns::tab{доктайп}")
	try:
		return set(frappe.db.get_table_columns(доктайп))
	except frappe.db.TableMissingError:
		return set()
