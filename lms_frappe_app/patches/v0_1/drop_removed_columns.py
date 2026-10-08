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
колонкой. Колонки таблицы уходят одним `ALTER TABLE` с `DROP COLUMN IF
EXISTS` — только те, что есть в базе сайта. Колонки, которой уже нет, и
таблицы, которой нет, патч не трогает — повторный запуск ничего не делает.

`submission` попытки ждёт патча `old_quiz_attempts`: по ней тот находит
сдачи Learning, сделанные приложением. Миграция с `--skip-failing`
продолжает после упавшего патча, и без этой проверки колонка ушла бы
раньше сдач; пока `old_quiz_attempts` не выполнен (`patch_log.выполнен`),
колонка остаётся, и патч печатает почему. `baseline` заметки не ждёт
`note_release_keys`: перенос заметок её не читает.
"""

import frappe

from lms_frappe_app.patches.v0_1.patch_log import выполнен

#: Доктайп → колонки снятых полей.
КОЛОНКИ = {
	"Agent Course Report": ("lesson_directive", "question"),
	"Agent Author Note": ("baseline",),
	"Agent Quiz Attempt": ("quiz", "submission"),
	"Agent Quiz Answer": ("question",),
	"Agent Quiz Event": ("question",),
	"Agent Course Artifact": ("template", "template_version", "overlay"),
}

#: Колонка → патч, который читает её и должен отработать до её удаления.
ЖДУТ_ПАТЧА = {("Agent Quiz Attempt", "submission"): "old_quiz_attempts"}


def execute():
	for доктайп, поля in КОЛОНКИ.items():
		есть = колонки(доктайп)
		удалить = []
		for поле in поля:
			if поле not in есть:
				continue
			if (патч := ЖДУТ_ПАТЧА.get((доктайп, поле))) and not выполнен(патч):
				print(f"drop_removed_columns: {доктайп}.{поле} оставлена — патч {патч} ещё не выполнен")
				continue
			удалить.append(поле)
		if удалить:
			frappe.db.sql_ddl(
				f"ALTER TABLE `tab{доктайп}` "
				+ ", ".join(f"DROP COLUMN IF EXISTS `{поле}`" for поле in удалить)
			)
		frappe.client_cache.delete_value(f"table_columns::tab{доктайп}")
		print(f"drop_removed_columns: {доктайп} — удалены колонки: {', '.join(удалить) or 'нет'}")


def колонки(доктайп: str) -> set[str]:
	"""Колонки таблицы доктайпа в базе сайта, мимо кеша; таблицы нет — пусто.

	`Why:` `frappe.db.get_table_columns` ищет таблицу в `information_schema`
	по имени без базы, а на одном сервере MariaDB бывает несколько сайтов:
	колонка из чужой базы выглядела бы своей.
	"""
	return set(
		frappe.db.sql(
			"""
			SELECT column_name FROM information_schema.columns
			WHERE table_schema = DATABASE() AND table_name = %s
			""",
			f"tab{доктайп}",
			pluck=True,
		)
	)
