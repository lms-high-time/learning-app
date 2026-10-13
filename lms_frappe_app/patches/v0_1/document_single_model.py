# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Документ курса — одна модель: раздел релиза (learning-services#515).

Схема документа держит раздел в форме релиза — `rows` и колонки; текста,
файлов и ссылок у блока нет, холста и формул у схемы — тоже. Проверку, что
действующие схемы существующих курсов переводятся, делает раньше
синхронизации схемы доктайпов патч `document_single_model_check`
(`проверить`); этот патч переводит данные и убирает лишнее:

1. бэкап — приватный `File` `document-model-515-backup.json`, только
   System Manager: непустые заметки, ссылки, файлы и срезы блоков с учеником,
   курсом, документом и блоком; сироты — документы учеников и схемы курсов,
   которых нет, — целиком; файлы документов учеников; непереводимые блоки
   недействующих версий схем — `spec` и холст схемы. Сохранять нечего —
   бэкапа нет;
2. сироты удаляются — с файлами при них;
3. файлы блоков — `File`, прикреплённые к документам учеников, — удаляются;
4. `spec` каждого блока схемы существующего курса → `rows` и `columns`: поля
   — раздел `one`, колонки своей таблицы — `many`; `required` строкой —
   `{"if_column": …}`. Виды таблицы, заготовка строк и `prefix`
   отбрасываются: ID строк, уже выданные, не меняются. Непереводимый блок
   недействующей версии остаётся без `rows` и `columns`: ни ученик, ни
   публикация её не читают;
5. колонки снятых полей (`Agent Artifact Block`: `kind`, `accept`, `span`,
   `hint`, `spec`; `Agent Course Artifact`: `layout`, `canvas`), доктайп и
   таблица `Agent Artifact Content` и значение `artifact_preview_rows` уходят
   из базы. DDL — последним: он фиксирует транзакцию.

Старые колонки читаются прямым запросом: миграция снимает поля из схемы
доктайпа, но колонки в таблице оставляет. Повторный запуск колонок `spec` и
таблицы заметок не находит и ничего не меняет.
"""

import json

import frappe
from frappe.utils import now

from lms_frappe_app.patches.v0_1.drop_removed_columns import колонки as колонки_таблицы

ДОКУМЕНТ = "Agent Student Artifact"
СХЕМА = "Agent Course Artifact"
БЛОК = "Agent Artifact Block"
ЗАМЕТКИ = "Agent Artifact Content"
НАСТРОЙКИ = "Agent Learning Settings"
#: Доктайп → колонки снятых полей.
КОЛОНКИ = {БЛОК: ("kind", "accept", "span", "hint", "spec"), СХЕМА: ("layout", "canvas")}
ПОЛЯ_НАСТРОЕК = ("artifact_preview_rows",)
БЭКАП = "document-model-515-backup.json"
#: Типы колонок и полей, которые переводятся в текст раздела.
ТЕКСТ = ("text", "longtext")


class Непереводимо(ValueError):
	"""Блок схемы не ложится на раздел релиза; текст — почему."""


def execute():
	курсы = set(frappe.get_all("LMS Course", pluck="name"))
	перевод = _переводы(курсы)
	if перевод["беды"]:
		raise frappe.ValidationError(
			"document_single_model: действующие схемы существующих курсов не переводятся в разделы "
			"релиза, данные не тронуты:\n" + "\n".join(перевод["беды"])
		)
	сироты_учеников = _сироты(
		ДОКУМЕНТ, курсы, ["name", "student", "archived_student", "course", "artifact", "organization", "data"]
	)
	сироты_схем = _сироты(СХЕМА, курсы, ["name", "course", "slug", "version", "title", "is_active"])
	заметки = _заметки()
	файлы = frappe.get_all(
		"File",
		filters={"attached_to_doctype": ДОКУМЕНТ},
		fields=["name", "file_name", "file_url", "file_size", "attached_to_name"],
		order_by="creation asc",
	)
	прежние = перевод["прежние"]
	бэкап = None
	if заметки or сироты_учеников or сироты_схем or файлы or прежние:
		бэкап = _сохранить_бэкап(
			{
				"patch": "lms_frappe_app.patches.v0_1.document_single_model",
				"created": now(),
				"content": заметки,
				"orphans": {"student_artifacts": сироты_учеников, "course_artifacts": сироты_схем},
				"files": файлы,
				"inactive_blocks": прежние,
			}
		)

	for запись in сироты_учеников:
		frappe.delete_doc(
			ДОКУМЕНТ, запись["name"], force=True, ignore_permissions=True, delete_permanently=True
		)
	for запись in сироты_схем:
		frappe.delete_doc(СХЕМА, запись["name"], force=True, ignore_permissions=True, delete_permanently=True)
	# Файлы сирот ушли вместе с сиротами; остальные — здесь.
	for файл in frappe.get_all("File", filters={"attached_to_doctype": ДОКУМЕНТ}, pluck="name"):
		frappe.delete_doc("File", файл, force=True, ignore_permissions=True, delete_permanently=True)
	for имя, (rows, columns) in перевод["переводы"].items():
		if frappe.db.exists(БЛОК, имя):
			frappe.db.set_value(
				БЛОК,
				имя,
				{"rows": rows, "columns": json.dumps(columns, ensure_ascii=False)},
				update_modified=False,
			)
	for блок in прежние:
		if блок["block"] and frappe.db.exists(БЛОК, блок["block"]):
			frappe.db.set_value(БЛОК, блок["block"], {"rows": None, "columns": None}, update_modified=False)
	настройки = frappe.db.sql(
		"SELECT field FROM `tabSingles` WHERE doctype = %s AND field IN %s",
		(НАСТРОЙКИ, ПОЛЯ_НАСТРОЕК),
		pluck=True,
	)
	frappe.db.delete("Singles", {"doctype": НАСТРОЙКИ, "field": ("in", ПОЛЯ_НАСТРОЕК)})
	frappe.clear_document_cache(НАСТРОЙКИ, НАСТРОЙКИ)
	print(
		f"document_single_model: бэкап {бэкап or 'не нужен'}; заметок блоков {len(заметки)}, "
		f"сирот: документов учеников {len(сироты_учеников)}, схем {len(сироты_схем)}; "
		f"файлов документов удалено {len(файлы)}; блоков переведено {len(перевод['переводы'])}, "
		f"непереводимых в прежних версиях {len(прежние)}; значений настроек удалено {len(настройки)}"
	)

	for доктайп, поля in КОЛОНКИ.items():
		есть = колонки_таблицы(доктайп)
		удалить = [поле for поле in поля if поле in есть]
		if удалить:
			frappe.db.sql_ddl(
				f"ALTER TABLE `tab{доктайп}` "
				+ ", ".join(f"DROP COLUMN IF EXISTS `{поле}`" for поле in удалить)
			)
		frappe.client_cache.delete_value(f"table_columns::tab{доктайп}")
		print(f"document_single_model: {доктайп} — удалены колонки: {', '.join(удалить) or 'нет'}")
	_удалить_заметки()


def проверить() -> list[str]:
	"""Беды действующих схем существующих курсов — строками; пусто — переводятся.

	Только чтение, прямым SQL: зовёт её и `document_single_model_check` до
	синхронизации схемы доктайпов.
	"""
	return _переводы(set(frappe.get_all("LMS Course", pluck="name")))["беды"]


def перевести(вид: str | None, спек) -> tuple[str, list[dict]]:
	"""`rows` и колонки раздела из вида и `spec` блока; не ложится — `Непереводимо`."""
	if вид and вид != "text":
		raise Непереводимо(f"блок вида {вид}")
	if isinstance(спек, str):
		спек = json.loads(спек) if спек.strip() else {}
	спек = спек or {}
	if not isinstance(спек, dict):
		raise Непереводимо("spec не объект")
	поля, колонки = спек.get("fields") or [], спек.get("columns") or []
	if not isinstance(поля, list) or not isinstance(колонки, list):
		raise Непереводимо("поля или колонки не списком")
	if поля and колонки:
		raise Непереводимо("у блока и поля, и колонки")
	if not поля and not колонки:
		raise Непереводимо("у блока ни полей, ни колонок")
	итог = []
	for описание in поля or колонки:
		if not isinstance(описание, dict) or not isinstance(описание.get("key"), str):
			raise Непереводимо("колонка без ключа")
		тип = описание.get("type") or "text"
		if тип not in ТЕКСТ:
			raise Непереводимо(f"колонка {описание['key']} типа {тип}")
		обязательна = описание.get("required")
		if isinstance(обязательна, str) and обязательна.strip():
			обязательна = {"if_column": обязательна.strip()}
		else:
			обязательна = обязательна is True
		итог.append(
			{
				"key": описание["key"],
				"title": str(описание.get("title") or описание["key"]).strip(),
				"required": обязательна,
			}
		)
	return ("one" if поля else "many"), итог


def есть_холст(layout, canvas) -> bool:
	"""Холст у схемы: раскладка `canvas` или непустой JSON холста.

	Пустые `{}`, `[]`, `null` и пустая строка — не холст.
	"""
	if layout == "canvas":
		return True
	if isinstance(canvas, str):
		if not canvas.strip():
			return False
		try:
			canvas = json.loads(canvas)
		except ValueError:
			return True
	return bool(canvas)


def _переводы(курсы: set[str]) -> dict:
	"""Перевод блоков схем существующих курсов:

	- `переводы` — `{блок: (rows, columns)}`;
	- `беды` — строки о действующих версиях, которые не переводятся;
	- `прежние` — непереводимые блоки и холсты недействующих версий: для бэкапа.

	Колонки `spec` уже нет — переводить нечего.
	"""
	итог: dict = {"переводы": {}, "беды": [], "прежние": []}
	if "spec" not in колонки_таблицы(БЛОК):
		return итог
	есть_у_схемы = колонки_таблицы(СХЕМА)
	холст = ", ".join(f"a.`{поле}`" for поле in ("layout", "canvas") if поле in есть_у_схемы) or "NULL"
	строки = frappe.db.sql(
		f"""
		SELECT b.name, b.block_key, b.kind, b.spec, a.name AS schema_name, a.course, a.slug,
			a.version, a.is_active, {холст}
		FROM `tabAgent Artifact Block` b
		JOIN `tabAgent Course Artifact` a ON a.name = b.parent
		WHERE b.parenttype = 'Agent Course Artifact'
		ORDER BY a.name, b.idx
		""",
		as_dict=True,
	)
	с_холстом: set[str] = set()
	for с in строки:
		if с.course not in курсы:
			continue
		где = f"  {с.schema_name} (курс {с.course}, документ {с.slug}, v{с.version}{', действующая' if с.is_active else ''})"
		прежняя = {
			"schema": с.schema_name,
			"course": с.course,
			"artifact": с.slug,
			"version": с.version,
		}
		if есть_холст(с.get("layout"), с.get("canvas")) and с.schema_name not in с_холстом:
			с_холстом.add(с.schema_name)
			if с.is_active:
				итог["беды"].append(f"{где}: холст")
			else:
				итог["прежние"].append(
					{**прежняя, "block": None, "layout": с.get("layout"), "canvas": с.get("canvas")}
				)
		try:
			спек = json.loads(с.spec) if isinstance(с.spec, str) and с.spec.strip() else (с.spec or {})
			if not isinstance(спек, dict):
				raise Непереводимо("spec не объект")
			if спек.get("columns") and (спек.get("table") or с.block_key) != с.block_key:
				raise Непереводимо(f"таблица {спек.get('table')} не своя")
			итог["переводы"][с.name] = перевести(с.kind, спек)
		except (ValueError, KeyError, AttributeError, TypeError) as беда:
			if с.is_active:
				итог["беды"].append(f"{где}: блок {с.block_key} — {беда}")
			else:
				итог["прежние"].append(
					{**прежняя, "block": с.name, "block_key": с.block_key, "kind": с.kind, "spec": с.spec}
				)
	return итог


def _сироты(доктайп: str, курсы: set[str], поля: list[str]) -> list[dict]:
	"""Записи доктайпа по курсам, которых нет."""
	return [
		dict(запись)
		for запись in frappe.get_all(доктайп, fields=поля, order_by="name asc")
		if запись.course not in курсы
	]


def _заметки() -> list[dict]:
	"""Непустые заметки, ссылки, файлы и срезы блоков документов учеников."""
	if not frappe.db.table_exists(ЗАМЕТКИ, cached=False):
		return []
	return [
		dict(строка)
		for строка in frappe.db.sql(
			"""
			SELECT d.name AS document, d.student, d.archived_student, d.course, d.artifact,
				d.organization, c.block_key AS block, c.content, c.url, c.file, f.file_name, c.preview
			FROM `tabAgent Artifact Content` c
			JOIN `tabAgent Student Artifact` d ON d.name = c.parent
			LEFT JOIN `tabFile` f ON f.name = c.file
			WHERE c.parenttype = 'Agent Student Artifact'
				AND (COALESCE(c.content, '') != '' OR COALESCE(c.url, '') != '' OR COALESCE(c.file, '') != '')
			ORDER BY d.name, c.idx
			""",
			as_dict=True,
		)
	]


def _сохранить_бэкап(содержимое: dict) -> str:
	"""Приватный `File` без привязки: читает его только System Manager."""
	файл = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": БЭКАП,
			"is_private": 1,
			"content": json.dumps(содержимое, ensure_ascii=False, indent=1, default=str),
		}
	).insert(ignore_permissions=True)
	return файл.file_url


def _удалить_заметки() -> None:
	"""Доктайп заметок блоков — запись `DocType` и таблица."""
	if frappe.db.exists("DocType", ЗАМЕТКИ):
		frappe.delete_doc("DocType", ЗАМЕТКИ, force=True, ignore_missing=True, delete_permanently=True)
		print(f"document_single_model: {ЗАМЕТКИ} — запись DocType удалена")
	if frappe.db.table_exists(ЗАМЕТКИ, cached=False):
		frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{ЗАМЕТКИ}`")
		print(f"document_single_model: {ЗАМЕТКИ} — таблица удалена")
	frappe.client_cache.delete_value("db_tables")
