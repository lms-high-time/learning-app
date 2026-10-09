# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Патчи удаления старой модели курса (learning-services#512).

DDL в тестах не выполняется: `sql_ddl` подменён и записывает запросы, а схема
— колонки и таблицы — подменена словарём, который эти запросы правят. Так
проверяются оба реальных состояния сайта — старый, где всё на месте, и
свежий, где ни таблиц, ни колонок нет, — и повторный запуск. Записи (DML)
идут по-настоящему и откатываются вместе с тестом.
"""

import io
import json
import re
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.patches.v0_1 import (
	drop_old_model_doctypes,
	drop_removed_columns,
	drop_removed_settings,
	old_quiz_attempts,
	patch_log,
)

ПРИЛОЖЕНИЕ = Path(drop_removed_columns.__file__).parents[2]
DDL = re.compile(r"^\s*(alter|create|drop)\b", re.IGNORECASE)


class Схема:
	"""Подменённая схема: таблицы и их колонки; DDL патчей правит её, а не базу.

	`свои` — доктайпы, чьи таблицы подменены: не названная в `таблицы` — её
	нет. Прочие идут в базу как есть.
	"""

	def __init__(self, таблицы: dict[str, set[str]], свои=()):
		self.таблицы = таблицы
		self.свои = set(свои) | set(таблицы)
		self.ddl: list[str] = []
		#: Искали ли колонки в базе сайта, а не во всех базах сервера.
		self.схема_по_базе: bool | None = None

	def выполнить_ddl(self, запрос: str, **_) -> None:
		self.ddl.append(запрос)
		if колонки := re.match(
			r"ALTER TABLE `tab(.+?)` ((?:DROP COLUMN IF EXISTS `[^`]+`(?:, )?)+)$", запрос
		):
			for колонка in re.findall(r"DROP COLUMN IF EXISTS `([^`]+)`", колонки[2]):
				self.таблицы[колонки[1]].discard(колонка)
		elif таблица := re.match(r"DROP TABLE IF EXISTS `tab(.+?)`$", запрос):
			self.таблицы.pop(таблица[1], None)
		else:
			raise AssertionError(f"неожиданный DDL: {запрос}")

	@contextmanager
	def подменить(self, строки: dict[str, list] | None = None):
		"""Подменяет схему для доктайпов из `таблицы` и ответы на запросы к ним.

		`строки` — подстрока запроса → что вернуть: так читаются таблицы,
		которых на свежем сайте нет.
		"""
		исходный_sql = frappe.db.sql
		исходные_колонки = frappe.db.get_table_columns
		исходная_таблица = frappe.db.table_exists
		свои = self.свои

		def sql(запрос, *args, **kwargs):
			текст = str(запрос)
			if DDL.match(текст):
				raise AssertionError(f"DDL мимо sql_ddl: {текст}")
			if "information_schema.columns" in текст and args and args[0].removeprefix("tab") in свои:
				self.схема_по_базе = "table_schema = DATABASE()" in текст
				return list(self.таблицы.get(args[0].removeprefix("tab"), ()))
			for часть, ответ in (строки or {}).items():
				if часть in текст:
					return ответ
			return исходный_sql(запрос, *args, **kwargs)

		def get_table_columns(доктайп):
			if доктайп not in свои:
				return исходные_колонки(доктайп)
			if доктайп not in self.таблицы:
				raise frappe.db.TableMissingError("DocType", доктайп)
			return list(self.таблицы[доктайп])

		def table_exists(доктайп, *args, **kwargs):
			if доктайп in свои:
				return доктайп in self.таблицы
			return исходная_таблица(доктайп, *args, **kwargs)

		# Флаги — как при настоящей миграции: `delete_doc` доктайпа не трогает
		# папки контроллеров и не шлёт уведомлений.
		флаги = {флаг: frappe.flags.get(флаг) for флаг in ("in_patch", "in_migrate")}
		frappe.flags.update(in_patch=True, in_migrate=True)
		try:
			with (
				patch.object(frappe.db, "sql", side_effect=sql),
				patch.object(frappe.db, "sql_ddl", side_effect=self.выполнить_ddl),
				patch.object(frappe.db, "get_table_columns", side_effect=get_table_columns),
				patch.object(frappe.db, "table_exists", side_effect=table_exists),
				patch.object(frappe.db, "commit"),
			):
				yield self
		finally:
			frappe.flags.update(флаги)


def выполнить(патч) -> str:
	"""Запускает патч и возвращает, что он напечатал."""
	вывод = io.StringIO()
	with redirect_stdout(вывод):
		патч.execute()
	return вывод.getvalue()


class TestPatchOrder(IntegrationTestCase):
	def test_патчи_идут_после_переноса_целей_в_нужном_порядке(self):
		строки = (ПРИЛОЖЕНИЕ / "patches.txt").read_text(encoding="utf-8").splitlines()
		порядок = [
			f"lms_frappe_app.patches.v0_1.{имя}"
			for имя in (
				"announce_objectives",
				"old_quiz_attempts",
				"drop_removed_columns",
				"drop_old_model_doctypes",
				"drop_removed_settings",
			)
		]
		self.assertGreater(строки.index("[post_model_sync]"), строки.index("[pre_model_sync]"))
		номера = [строки.index(патч) for патч in порядок]
		self.assertGreater(номера[0], строки.index("[post_model_sync]"))
		self.assertEqual(номера, sorted(номера))


def отметить_патч(патч: str, состояние: str) -> None:
	"""`Patch Log` патча `патч`: `выполнен`, `пропущен` (упал при `--skip-failing`,
	`skipped=1`) или `нет` — записи нет. Запись откатится с тестом."""
	имя = patch_log.полное_имя(патч)
	frappe.db.delete("Patch Log", {"patch": имя})
	if состояние != "нет":
		frappe.get_doc(
			{"doctype": "Patch Log", "patch": имя, "skipped": int(состояние == "пропущен")}
		).insert(ignore_permissions=True)


class IntegrationTestPatchLog(IntegrationTestCase):
	"""Выполнен — только `Patch Log` со `skipped=0`, как решает сам Frappe."""

	def test_выполнен_только_без_пропуска(self):
		for состояние, ожидается in (("выполнен", True), ("пропущен", False), ("нет", False)):
			with self.subTest(состояние=состояние):
				отметить_патч("old_quiz_attempts", состояние)
				self.assertIs(patch_log.выполнен("old_quiz_attempts"), ожидается)

	def test_ждут_патчей_из_списка(self):
		"""Патчи, которых ждут удаления, есть в `patches.txt` и идут раньше ждущих."""
		строки = (ПРИЛОЖЕНИЕ / "patches.txt").read_text(encoding="utf-8").splitlines()
		for ждущий, ждут in (
			("drop_removed_columns", drop_removed_columns.ЖДУТ_ПАТЧА.values()),
			("drop_old_model_doctypes", drop_old_model_doctypes.ЖДУТ_ПАТЧА.values()),
		):
			for патч in ждут:
				with self.subTest(ждущий=ждущий, патч=патч):
					self.assertLess(
						строки.index(patch_log.полное_имя(патч)), строки.index(patch_log.полное_имя(ждущий))
					)


class IntegrationTestDropRemovedColumns(IntegrationTestCase):
	"""Колонки снятых полей: одним `ALTER TABLE … DROP COLUMN IF EXISTS` на таблицу —
	те, что есть в базе сайта."""

	def setUp(self):
		отметить_патч("old_quiz_attempts", "выполнен")

	def старый_сайт(self) -> Схема:
		return Схема(
			{доктайп: {"name", "creation", *поля} for доктайп, поля in drop_removed_columns.КОЛОНКИ.items()}
		)

	def test_снятых_полей_нет_в_схемах(self):
		"""Страховка от удаления живой колонки: поле, вернувшееся в схему,
		патч снёс бы вместе с данными."""
		for доктайп, поля in drop_removed_columns.КОЛОНКИ.items():
			имя = frappe.scrub(доктайп)
			схема = json.loads(
				(ПРИЛОЖЕНИЕ / "agent_learning" / "doctype" / имя / f"{имя}.json").read_text(encoding="utf-8")
			)
			живые = {поле["fieldname"] for поле in схема["fields"]}
			self.assertEqual(живые & set(поля), set(), доктайп)

	def test_удаляет_каждую_колонку_один_раз(self):
		схема = self.старый_сайт()

		with схема.подменить():
			первый = выполнить(drop_removed_columns)
			ddl = list(схема.ddl)
			второй = выполнить(drop_removed_columns)

		self.assertEqual(
			ddl,
			[
				f"ALTER TABLE `tab{доктайп}` " + ", ".join(f"DROP COLUMN IF EXISTS `{поле}`" for поле in поля)
				for доктайп, поля in drop_removed_columns.КОЛОНКИ.items()
			],
		)
		self.assertTrue(схема.схема_по_базе, "колонки — по базе сайта")
		self.assertEqual(схема.ddl, ddl, "повторный запуск DDL не шлёт")
		self.assertEqual(
			схема.таблицы, {доктайп: {"name", "creation"} for доктайп in drop_removed_columns.КОЛОНКИ}
		)
		self.assertIn("Agent Course Report — удалены колонки: lesson_directive, question", первый)
		self.assertIn("Agent Course Report — удалены колонки: нет", второй)

	def test_свежий_сайт_без_колонок_и_таблиц(self):
		"""Колонок снятых полей нет, части таблиц нет вовсе: DDL — только по найденной колонке."""
		схема = Схема(
			{"Agent Course Report": {"name"}, "Agent Author Note": {"name", "baseline"}},
			свои=drop_removed_columns.КОЛОНКИ,
		)
		with схема.подменить():
			вывод = выполнить(drop_removed_columns)

		self.assertEqual(схема.ddl, ["ALTER TABLE `tabAgent Author Note` DROP COLUMN IF EXISTS `baseline`"])
		self.assertIn("Agent Quiz Event — удалены колонки: нет", вывод)

	def test_submission_ждёт_патча_попыток(self):
		"""`--skip-failing`: патч попыток упал и записан в `Patch Log` со
		`skipped=1` — его колонка остаётся, прочие уходят."""
		отметить_патч("old_quiz_attempts", "пропущен")
		схема = self.старый_сайт()

		with схема.подменить():
			вывод = выполнить(drop_removed_columns)

		self.assertIn("ALTER TABLE `tabAgent Quiz Attempt` DROP COLUMN IF EXISTS `quiz`", схема.ddl)
		self.assertEqual(схема.таблицы["Agent Quiz Attempt"], {"name", "creation", "submission"})
		self.assertIn(
			"Agent Quiz Attempt.submission оставлена — патч old_quiz_attempts ещё не выполнен", вывод
		)
		self.assertIn("Agent Quiz Attempt — удалены колонки: quiz\n", вывод)

		отметить_патч("old_quiz_attempts", "выполнен")
		with схема.подменить():
			выполнить(drop_removed_columns)

		self.assertEqual(
			схема.ddl[-1], "ALTER TABLE `tabAgent Quiz Attempt` DROP COLUMN IF EXISTS `submission`"
		)


class IntegrationTestDropRemovedSettings(IntegrationTestCase):
	НАСТРОЙКИ = drop_removed_settings.НАСТРОЙКИ

	def строки(self) -> set[str]:
		return set(
			frappe.db.sql("SELECT field FROM `tabSingles` WHERE doctype = %s", self.НАСТРОЙКИ, pluck=True)
		)

	def test_удаляет_значения_снятых_полей(self):
		frappe.db.delete("Singles", {"doctype": self.НАСТРОЙКИ, "field": ("in", drop_removed_settings.ПОЛЯ)})
		for поле, значение in (("lesson_segment_limit", "6000"), ("pass_threshold", "1")):
			frappe.db.sql(
				"INSERT INTO `tabSingles` (doctype, field, value) VALUES (%s, %s, %s)",
				(self.НАСТРОЙКИ, поле, значение),
			)
		было = self.строки()

		первый = выполнить(drop_removed_settings)
		второй = выполнить(drop_removed_settings)

		self.assertEqual(было - self.строки(), set(drop_removed_settings.ПОЛЯ))
		self.assertIn("max_attempts", self.строки(), "живые настройки на месте")
		self.assertIn("удалены значения: lesson_segment_limit, pass_threshold", первый)
		self.assertIn("удалены значения: нет", второй)
		self.assertIsNone(frappe.get_cached_doc(self.НАСТРОЙКИ).get("pass_threshold"))


class IntegrationTestOldQuizAttempts(IntegrationTestCase):
	"""Сдачи Learning от старых попыток удаляются, зависшие старые попытки закрываются."""

	ПОПЫТКА = old_quiz_attempts.ПОПЫТКА

	def setUp(self):
		суффикс = frappe.generate_hash(length=8)
		self.ученик = f"oqa-{суффикс}@example.com"
		self.зависшая = self.попытка(None, "In Progress")
		self.сданная = self.попытка(None, "Passed")
		self.по_релизу = self.попытка("REL-нет-такого", "In Progress")
		self.сдача = f"oqa-sub-{суффикс}"
		frappe.get_doc(
			{"doctype": old_quiz_attempts.СДАЧА, "name": self.сдача, "member": self.ученик, "quiz": "нет"}
		).db_insert()
		self.следы = self.следы_сдачи()
		self.колонки = set(frappe.db.get_table_columns(self.ПОПЫТКА)) | {"submission"}

	def следы_сдачи(self) -> list[tuple[str, dict]]:
		"""Версия, комментарий и уведомление о сдаче: таблица → отбор по сдаче."""
		сдача = old_quiz_attempts.СДАЧА
		следы = [
			("Version", {"ref_doctype": сдача, "docname": self.сдача}),
			("Comment", {"reference_doctype": сдача, "reference_name": self.сдача}),
			("Notification Log", {"document_type": сдача, "document_name": self.сдача}),
		]
		for таблица, отбор in следы:
			поля = {"comment_type": "Comment"} if таблица == "Comment" else {}
			if таблица == "Notification Log":
				поля = {"subject": "Сдача", "for_user": "Administrator"}
			frappe.get_doc({"doctype": таблица, **отбор, **поля}).db_insert()
		return следы

	def попытка(self, релиз: str | None, статус: str) -> str:
		документ = frappe.get_doc(
			{
				"doctype": self.ПОПЫТКА,
				"name": f"oqa-{frappe.generate_hash(length=10)}",
				"student": self.ученик,
				"release": релиз,
				"status": статус,
			}
		)
		документ.db_insert()
		return документ.name

	def выполнить(self, колонки: set[str] | None = None) -> tuple[str, list[str]]:
		"""Патч со своей колонкой `submission`: на свежем сайте её нет в базе."""
		схема = Схема({self.ПОПЫТКА: колонки if колонки is not None else set(self.колонки)})
		with схема.подменить({"SELECT DISTINCT submission": [self.сдача, "oqa-нет-такой-сдачи"]}):
			return выполнить(old_quiz_attempts), схема.ddl

	def статус(self, попытка: str) -> tuple:
		return tuple(frappe.db.get_value(self.ПОПЫТКА, попытка, ["status", "finished_at"]))

	def test_удаляет_сдачи_и_закрывает_зависшие(self):
		# Очередь заглушена: следы сдачи убирает сам патч, а не задача после коммита.
		with patch.object(frappe, "enqueue"):
			первый, ddl = self.выполнить()
		закрыта = self.статус(self.зависшая)
		второй, _ = self.выполнить()

		self.assertEqual(ddl, [])
		self.assertFalse(frappe.db.exists(old_quiz_attempts.СДАЧА, self.сдача))
		self.assertFalse(
			frappe.db.exists(
				"Deleted Document", {"deleted_doctype": old_quiz_attempts.СДАЧА, "deleted_name": self.сдача}
			)
		)
		self.assertEqual(закрыта[0], "Abandoned")
		self.assertIsNotNone(закрыта[1])
		self.assertEqual(self.статус(self.зависшая), закрыта, "повторный запуск не трогает закрытую")
		self.assertEqual(self.статус(self.сданная)[0], "Passed")
		self.assertEqual(self.статус(self.по_релизу)[0], "In Progress", "попытка по релизу живая")
		self.assertIn("сдач Learning удалено 1", первый)
		self.assertIn("сдач Learning удалено 0, зависших попыток закрыто 0", второй)
		for таблица, отбор in self.следы:
			self.assertFalse(frappe.db.exists(таблица, отбор), f"след сдачи в {таблица}")

	def test_без_колонки_сдачи_не_ищет(self):
		with patch.object(frappe, "delete_doc") as удалить:
			вывод, _ = self.выполнить(колонки={"name", "status", "release"})

		удалить.assert_not_called()
		self.assertTrue(frappe.db.exists(old_quiz_attempts.СДАЧА, self.сдача))
		self.assertIn("сдач Learning удалено 0", вывод)
		self.assertEqual(self.статус(self.зависшая)[0], "Abandoned")

	def test_без_таблицы_попыток(self):
		схема = Схема({}, свои={self.ПОПЫТКА})
		with схема.подменить():
			вывод = выполнить(old_quiz_attempts)

		self.assertIn("попыток квиза нет", вывод)
		self.assertEqual(self.статус(self.зависшая)[0], "In Progress")


class IntegrationTestDropOldModelDoctypes(IntegrationTestCase):
	"""Доктайпы старой модели: метаданные, запись `DocType` и таблица.

	Доктайп — выдуманный и без папки контроллера, как настоящие после
	удаления кода: так проверяется, что `frappe.delete_doc` без контроллера не
	падает, и настоящие записи общего сайта не трогаются.
	"""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.доктайп = f"Agent Gone {суффикс}"
		self.workspace = f"Gone WS {суффикс}"
		доктайп = frappe.get_doc(
			{
				"doctype": "DocType",
				"name": self.доктайп,
				"module": "Agent Learning",
				"fields": [{"fieldname": "objectives", "fieldtype": "Small Text", "label": "Цели"}],
				"permissions": [{"role": "System Manager", "read": 1}],
			}
		)
		доктайп.db_insert()
		for строка in (*доктайп.fields, *доктайп.permissions):
			строка.db_insert()
		for запись in (
			{"doctype": "Custom Field", "dt": self.доктайп, "fieldname": "extra", "fieldtype": "Data"},
			{
				"doctype": "Property Setter",
				"doc_type": self.доктайп,
				"doctype_or_field": "DocType",
				"property": "track_changes",
				"value": "1",
				"property_type": "Check",
			},
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": self.доктайп,
				"reference_name": "X-1",
			},
			{"doctype": "Version", "ref_doctype": self.доктайп, "docname": "X-1", "data": "{}"},
		):
			frappe.get_doc(запись).db_insert()
		self.записи_о_доктайпе(суффикс)
		frappe.db.sql(
			"INSERT INTO `__UserSettings` (`user`, `doctype`, `data`) VALUES ('Administrator', %s, '{}')",
			self.доктайп,
		)
		self.карточки = self.страница_workspace()

	def записи_о_доктайпе(self, суффикс: str) -> None:
		"""По записи на каждую строку `ДОКУМЕНТЫ`, `ОТВЯЗАТЬ`, `МЕТАДАННЫЕ` и
		`СЛУЖЕБНЫЕ`, которых нет выше, и стандартные записи — они остаются."""
		д = self.доктайп
		self.стандартные = [
			("Report", f"Gone std report {суффикс}"),
			("Print Format", f"Gone std print {суффикс}"),
			("Notification", f"Gone std notification {суффикс}"),
			("Number Card", f"Gone std card {суффикс}"),
			("Dashboard Chart", f"Gone std chart {суффикс}"),
		]
		self.письмо = письмо = f"gone-comm-{суффикс}"
		# `__global_search` — MyISAM: откат теста её не чистит.
		frappe.db.sql("INSERT INTO `__global_search` (doctype, name, content) VALUES (%s, 'X-1', 'x')", д)
		self.addCleanup(frappe.db.sql, "DELETE FROM `__global_search` WHERE doctype = %s", д)
		for таблица, имя in self.стандартные[2:]:
			поле = "chart_name" if таблица == "Dashboard Chart" else "label"
			frappe.get_doc(
				{
					"doctype": таблица,
					"name": имя,
					поле: имя,
					"document_type": д,
					"is_standard": 1,
					"module": "Agent Learning",
				}
			).db_insert()
		for запись in (
			{
				"doctype": "Workflow",
				"name": f"Gone workflow {суффикс}",
				"workflow_name": f"Gone workflow {суффикс}",
				"document_type": д,
				"workflow_state_field": "workflow_state",
			},
			{
				"doctype": "Assignment Rule",
				"name": f"Gone rule {суффикс}",
				"document_type": д,
				"rule": "Round Robin",
			},
			{"doctype": "Document Naming Rule", "document_type": д, "prefix": "GONE-"},
			{
				"doctype": "Webhook",
				"name": f"Gone webhook {суффикс}",
				"webhook_doctype": д,
				"webhook_docevent": "after_insert",
				"request_url": "https://example.com/hook",
			},
			{"doctype": "Document Follow", "ref_doctype": д, "ref_docname": "X-1", "user": "Administrator"},
			{
				"doctype": "Global Search DocType",
				"parent": "Global Search Settings",
				"parenttype": "Global Search Settings",
				"parentfield": "allowed_in_global_search",
				"document_type": д,
			},
			{
				"doctype": "Report",
				"name": f"Gone report {суффикс}",
				"report_name": f"Gone report {суффикс}",
				"ref_doctype": д,
				"is_standard": "No",
				"report_type": "Report Builder",
			},
			{
				"doctype": "Report",
				"name": self.стандартные[0][1],
				"report_name": self.стандартные[0][1],
				"ref_doctype": д,
				"is_standard": "Yes",
				"report_type": "Report Builder",
				"module": "Agent Learning",
			},
			{"doctype": "Client Script", "name": f"Gone cs {суффикс}", "dt": д, "script": ""},
			{
				"doctype": "Server Script",
				"name": f"Gone ss {суффикс}",
				"script_type": "DocType Event",
				"reference_doctype": д,
				"doctype_event": "Before Save",
				"script": "",
			},
			{
				"doctype": "Notification",
				"name": f"Gone notification {суффикс}",
				"document_type": д,
				"subject": "x",
				"event": "New",
				"channel": "Email",
			},
			{
				"doctype": "Number Card",
				"name": f"Gone card {суффикс}",
				"label": "x",
				"type": "Document Type",
				"document_type": д,
				"function": "Count",
			},
			{
				"doctype": "Dashboard Chart",
				"name": f"Gone chart {суффикс}",
				"chart_name": f"Gone chart {суффикс}",
				"chart_type": "Count",
				"document_type": д,
				"type": "Line",
			},
			{
				"doctype": "Kanban Board",
				"name": f"Gone kanban {суффикс}",
				"kanban_board_name": f"Gone kanban {суффикс}",
				"reference_doctype": д,
				"field_name": "status",
			},
			{"doctype": "Print Format", "name": f"Gone print {суффикс}", "doc_type": д, "standard": "No"},
			{
				"doctype": "Print Format",
				"name": self.стандартные[1][1],
				"doc_type": д,
				"standard": "Yes",
				"module": "Agent Learning",
			},
			{"doctype": "File", "file_name": "gone.txt", "attached_to_doctype": д, "is_folder": 0},
			{
				"doctype": "Communication",
				"name": письмо,
				"subject": "x",
				"communication_type": "Communication",
				"reference_doctype": д,
				"reference_name": "X-1",
			},
			{
				"doctype": "Communication Link",
				"parent": письмо,
				"parenttype": "Communication",
				"parentfield": "timeline_links",
				"link_doctype": д,
				"link_name": "X-1",
			},
			{"doctype": "ToDo", "description": "x", "reference_type": д, "reference_name": "X-1"},
			{"doctype": "Activity Log", "subject": "x", "reference_doctype": д, "reference_name": "X-1"},
			{
				"doctype": "View Log",
				"reference_doctype": д,
				"reference_name": "X-1",
				"viewed_by": "Administrator",
			},
			{
				"doctype": "Notification Log",
				"subject": "x",
				"for_user": "Administrator",
				"document_type": д,
				"document_name": "X-1",
			},
			{
				"doctype": "User Permission",
				"user": "Administrator",
				"allow": "User",
				"for_value": "Administrator",
				"applicable_for": д,
			},
		):
			frappe.get_doc(запись).db_insert()

	def страница_workspace(self) -> dict[str, str]:
		"""Workspace с карточками «A» (ссылки: живая, удаляемая, живая) и «B»."""
		frappe.get_doc(
			{"doctype": "Workspace", "name": self.workspace, "label": self.workspace, "title": self.workspace}
		).db_insert()
		карточки = {}
		for номер, (вид, подпись, ссылка, счёт) in enumerate(
			(
				("Card Break", "A", None, 3),
				("Link", "Живая", "Agent Course Release", 0),
				("Link", "Удаляемая", self.доктайп, 0),
				("Link", "Тоже живая", "Agent Course Report", 0),
				("Card Break", "B", None, 1),
				("Link", "Ещё живая", "Agent Author Note", 0),
			),
			start=1,
		):
			строка = frappe.get_doc(
				{
					"doctype": "Workspace Link",
					"parent": self.workspace,
					"parenttype": "Workspace",
					"parentfield": "links",
					"idx": номер,
					"type": вид,
					"label": подпись,
					"link_type": "DocType" if ссылка else None,
					"link_to": ссылка,
					"link_count": счёт,
				}
			)
			строка.db_insert()
			if вид == "Card Break":
				карточки[подпись] = строка.name
		return карточки

	def выполнить(self, схема: Схема) -> str:
		with (
			схема.подменить({f"SELECT COUNT(*) FROM `tab{self.доктайп}`": ((4,),)}),
			patch.object(drop_old_model_doctypes, "ДОКТАЙПЫ", (self.доктайп,)),
		):
			return выполнить(drop_old_model_doctypes)

	def test_настоящие_доктайпы_без_кода(self):
		"""Патч сносит только доктайпы, которых в коде нет."""
		self.assertEqual(
			set(drop_old_model_doctypes.ДОКТАЙПЫ),
			{
				"Agent Lesson Directive",
				"Agent Course Directive",
				"Agent Course Map",
				"Agent Author Visit",
				"Agent Artifact Template",
			},
		)
		схемы = {путь.stem for путь in ПРИЛОЖЕНИЕ.glob("*/doctype/*/*.json")}
		for доктайп in drop_old_model_doctypes.ДОКТАЙПЫ:
			self.assertNotIn(frappe.scrub(доктайп), схемы, доктайп)

	def test_удаляет_метаданные_запись_и_таблицу(self):
		начало = frappe.utils.now_datetime()
		схема = Схема({self.доктайп: {"name", "objectives"}})

		первый = self.выполнить(схема)
		второй = self.выполнить(схема)

		self.assertEqual(схема.ddl, [f"DROP TABLE IF EXISTS `tab{self.доктайп}`"], "таблица — одним DDL")
		self.assertFalse(frappe.db.exists("DocType", self.доктайп))
		for таблица, поле in (
			("DocField", "parent"),
			("DocPerm", "parent"),
			("Workspace Link", "link_to"),
		):
			self.assertFalse(frappe.db.exists(таблица, {поле: self.доктайп}), таблица)
		for таблица, поле, вид in (*drop_old_model_doctypes.ДОКУМЕНТЫ, *drop_old_model_doctypes.МЕТАДАННЫЕ):
			if таблица == "List View Settings":
				continue
			условие = {поле: self.доктайп, **({вид[0]: вид[1]} if вид else {})}
			self.assertFalse(frappe.db.exists(таблица, условие), f"{таблица}.{поле}")
		for таблица, имя in self.стандартные:
			self.assertTrue(frappe.db.exists(таблица, имя), f"стандартный {таблица} на месте")
		self.assertFalse(frappe.db.exists("Communication Link", {"link_doctype": self.доктайп}))
		self.assertEqual(
			frappe.db.get_value("Communication", self.письмо, ["reference_doctype", "reference_name"]),
			(None, None),
			"письмо отвязано, а не удалено",
		)
		self.assertFalse(frappe.db.sql("SELECT 1 FROM `__global_search` WHERE doctype = %s", self.доктайп))
		self.assertFalse(frappe.db.sql("SELECT 1 FROM `__UserSettings` WHERE doctype = %s", self.доктайп))
		self.assertFalse(
			frappe.db.exists(
				"Deleted Document",
				{"deleted_doctype": "DocType", "deleted_name": self.доктайп, "creation": (">=", начало)},
			),
			"доктайп удалён насовсем, без копии в корзине",
		)
		self.assertEqual(
			frappe.get_all(
				"Workspace Link",
				filters={"parent": self.workspace},
				fields=["label", "link_count"],
				order_by="idx asc",
			),
			[
				{"label": "A", "link_count": 2},
				{"label": "Живая", "link_count": 0},
				{"label": "Тоже живая", "link_count": 0},
				{"label": "B", "link_count": 1},
				{"label": "Ещё живая", "link_count": 0},
			],
		)
		for что in (
			"Report 1",
			"Client Script 1",
			"Server Script 1",
			"Notification 1",
			"Number Card 1",
			"Dashboard Chart 1",
			"Kanban Board 1",
			"Print Format 1",
			"Workflow 1",
			"Assignment Rule 1",
			"Document Naming Rule 1",
			"Webhook 1",
			"File 1",
			"Communication отвязано 1",
			"Document Follow 1",
			"Global Search DocType 1",
			"__global_search 1",
			"Custom Field 1",
			"Property Setter 1",
			"Version 1",
			"ToDo 1",
			"Activity Log 1",
			"View Log 1",
			"Notification Log 1",
			"User Permission 1",
			"__UserSettings 1",
		):
			self.assertIn(что, первый)
		self.assertIn("Workspace Link 1, DocType 1, записей 4, таблица 1", первый)
		self.assertIn(f"{self.доктайп} — нечего удалять", второй)

	def test_директива_курса_ждёт_переноса_целей(self):
		"""`--skip-failing`: `announce_objectives` упал (`skipped=1`) — доктайп,
		из которого он читает, остаётся целиком; выполнен — уходит."""
		схема = Схема({self.доктайп: {"name", "objectives"}})
		отметить_патч("announce_objectives", "пропущен")
		with patch.object(drop_old_model_doctypes, "ЖДУТ_ПАТЧА", {self.доктайп: "announce_objectives"}):
			вывод = self.выполнить(схема)

			self.assertIn(f"{self.доктайп} оставлен — патч announce_objectives ещё не выполнен", вывод)
			self.assertEqual(схема.ddl, [])
			self.assertTrue(frappe.db.exists("DocType", self.доктайп))
			self.assertTrue(frappe.db.exists("Custom Field", {"dt": self.доктайп}))

			отметить_патч("announce_objectives", "выполнен")
			self.выполнить(схема)

		self.assertEqual(схема.ddl, [f"DROP TABLE IF EXISTS `tab{self.доктайп}`"])
		self.assertFalse(frappe.db.exists("DocType", self.доктайп))

	def test_ничего_нет_ничего_не_удаляет(self):
		"""Свежий сайт: ни записи, ни таблицы, ни метаданных."""
		схема = Схема({}, свои={"Agent Nothing Here"})
		with (
			схема.подменить(),
			patch.object(drop_old_model_doctypes, "ДОКТАЙПЫ", ("Agent Nothing Here",)),
			patch.object(frappe, "delete_doc") as удалить,
		):
			вывод = выполнить(drop_old_model_doctypes)

		удалить.assert_not_called()
		self.assertEqual(схема.ddl, [])
		self.assertIn("Agent Nothing Here — нечего удалять", вывод)
