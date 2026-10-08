# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Доктайпы сборки курса по кусочку уходят из базы (learning-services#512).

Курс публикуется релизом, и уходят: директивы урока и курса, карта курса,
визиты автора и шаблоны документов. Их записи удаляются безвозвратно, без
копий в корзине: курсы, которым они принадлежали, удалены в
learning-services#500, а цели анонсов перенёс раньше патч `announce_objectives`.

На каждый доктайп:

- записи, настроенные на него, — `frappe.delete_doc` каждой, с её строками
  и файлами (`ДОКУМЕНТЫ`): отчёты, клиентские и серверные скрипты,
  уведомления, карточки и графики дашбордов, канбан-доски, форматы печати,
  процессы, правила назначения и именования, вебхуки, вложения.
  Стандартные — с признаком стандартности — не трогаются: они лежат в коде
  приложений;
- письма к его записям отвязываются, а не удаляются, — как при удалении
  записи во Frappe (`delete_dynamic_links`);
- метаданные, которые ссылаются на него и которые `frappe.delete_doc` не
  удаляет, — строками (`МЕТАДАННЫЕ`): поля и свойства (`Custom Field`,
  `Property Setter`, `Custom DocPerm`), связи других доктайпов (`DocType
  Link`), ссылки workspace и сайдбара, следы записей (`Version`, `Comment`,
  `DocShare`, `Tag Link`, `ToDo`, ссылки писем, подписки, журналы действий,
  просмотров и уведомлений, `User Permission` на доктайп и для доктайпа,
  `Deleted Document`, настройки списков и глобального поиска);
- запись `DocType` — `frappe.delete_doc`: она снимает поля, права, действия
  и связи самого доктайпа. Папки контроллера у доктайпа нет, и это ей не
  мешает: контроллер `DocType` — общий, а удаление папки она делает только в
  режиме разработчика вне миграции и отсутствие папки глотает;
- таблица — `DROP TABLE IF EXISTS`, последней: DDL фиксирует транзакцию.

`Why:` уборка сирот при миграции удаляет только запись `DocType`, а таблицу и
ссылки на доктайп оставляет. Ссылка workspace на несуществующий доктайп прятала
у Administrator все карточки workspace.

`Agent Course Directive` ждёт патча `announce_objectives`: тот переносит из
неё цели анонсов. Миграция с `--skip-failing` продолжает после упавшего
патча; пока он не выполнен (`patch_log.выполнен`), доктайп остаётся целиком
и патч печатает почему. Директиву урока не читает ни один патч.

Автоповторы (`Auto Repeat`) патч не трогает: ни у одного доктайпа старой
модели их не включали, а удаление автоповтора пишет в саму запись доктайпа.

Повторный запуск ничего не находит и ничего не удаляет.
"""

import frappe

from lms_frappe_app.patches.v0_1.patch_log import выполнен

ДОКТАЙПЫ = (
	"Agent Lesson Directive",
	"Agent Course Directive",
	"Agent Course Map",
	"Agent Author Visit",
	"Agent Artifact Template",
)

#: Доктайп → патч, который читает его и должен отработать до его удаления.
ЖДУТ_ПАТЧА = {"Agent Course Directive": "announce_objectives"}

#: Записи, настроенные на доктайп, — удаляются `frappe.delete_doc` со своими
#: строками и файлами: доктайп → поле со ссылкой на доктайп. Пара — ещё и
#: условие: только нестандартные. У `Server Script` признака стандартности
#: нет — он живёт только в базе. Поля — по схемам Frappe 16.
ДОКУМЕНТЫ = (
	("Report", "ref_doctype", ("is_standard", "No")),
	("Client Script", "dt", None),
	("Server Script", "reference_doctype", None),
	("Notification", "document_type", ("is_standard", 0)),
	("Number Card", "document_type", ("is_standard", 0)),
	("Dashboard Chart", "document_type", ("is_standard", 0)),
	("Kanban Board", "reference_doctype", None),
	("Print Format", "doc_type", ("standard", "No")),
	("Workflow", "document_type", None),
	("Assignment Rule", "document_type", None),
	("Document Naming Rule", "document_type", None),
	("Webhook", "webhook_doctype", None),
	("File", "attached_to_doctype", None),
)

#: Записи, которые отвязываются от доктайпа: таблица → поля ссылки (доктайп,
#: запись) — они очищаются.
ОТВЯЗАТЬ = (("Communication", "reference_doctype", "reference_name"),)

#: Метаданные о доктайпе — удаляются строками: таблица → поле со ссылкой на
#: него. Пара — ещё и условие на вид ссылки, где ссылка бывает не только на
#: доктайп.
МЕТАДАННЫЕ = (
	("Custom Field", "dt", None),
	("Property Setter", "doc_type", None),
	("Custom DocPerm", "parent", None),
	("DocType Link", "link_doctype", None),
	("Workspace Shortcut", "link_to", ("type", "DocType")),
	("Workspace Quick List", "document_type", None),
	("Workspace Sidebar Item", "link_to", ("link_type", "DocType")),
	("Version", "ref_doctype", None),
	("Comment", "reference_doctype", None),
	("DocShare", "share_doctype", None),
	("Tag Link", "document_type", None),
	("ToDo", "reference_type", None),
	("Communication Link", "link_doctype", None),
	("Document Follow", "ref_doctype", None),
	("Activity Log", "reference_doctype", None),
	("View Log", "reference_doctype", None),
	("Notification Log", "document_type", None),
	("User Permission", "allow", None),
	("User Permission", "applicable_for", None),
	("List View Settings", "name", None),
	("Deleted Document", "deleted_doctype", None),
	("Global Search DocType", "document_type", None),
)

#: Таблицы без префикса `tab` — `table_exists` их не найдёт: таблица → поле.
СЛУЖЕБНЫЕ = (("__UserSettings", "doctype"), ("__global_search", "doctype"))


def execute():
	for доктайп in ДОКТАЙПЫ:
		if (патч := ЖДУТ_ПАТЧА.get(доктайп)) and not выполнен(патч):
			print(f"drop_old_model_doctypes: {доктайп} оставлен — патч {патч} ещё не выполнен")
			continue
		удалено = _метаданные(доктайп)
		if ссылок := _ссылки_workspace(доктайп):
			удалено["Workspace Link"] = ссылок
		if frappe.db.exists("DocType", доктайп):
			frappe.delete_doc("DocType", доктайп, force=True, ignore_missing=True, delete_permanently=True)
			удалено["DocType"] = 1
		if frappe.db.table_exists(доктайп, cached=False):
			удалено["записей"] = frappe.db.sql(f"SELECT COUNT(*) FROM `tab{доктайп}`")[0][0]
			frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{доктайп}`")
			удалено["таблица"] = 1
		print(
			f"drop_old_model_doctypes: {доктайп} — "
			+ (", ".join(f"{что} {сколько}" for что, сколько in удалено.items()) or "нечего удалять")
		)
	frappe.client_cache.delete_value("db_tables")


def _метаданные(доктайп: str) -> dict[str, int]:
	"""Удаляет записи и метаданные о доктайпе; возвращает, сколько ушло из каждой таблицы.

	Записи — раньше строк: удаление вложения пишет комментарий «вложение
	удалено» записи доктайпа, и его уберут строки `Comment`.
	"""
	удалено = {}
	for таблица, поле, вид in ДОКУМЕНТЫ:
		if not frappe.db.table_exists(таблица):
			continue
		for имя in frappe.get_all(таблица, filters=_условие(доктайп, поле, вид), pluck="name"):
			frappe.delete_doc(
				таблица,
				имя,
				force=True,
				ignore_permissions=True,
				ignore_missing=True,
				delete_permanently=True,
			)
			удалено[таблица] = удалено.get(таблица, 0) + 1
	for таблица, поле, запись in ОТВЯЗАТЬ:
		if not frappe.db.table_exists(таблица):
			continue
		if сколько := frappe.db.count(таблица, {поле: доктайп}):
			frappe.db.set_value(таблица, {поле: доктайп}, {поле: None, запись: None}, update_modified=False)
			удалено[f"{таблица} отвязано"] = сколько
	for таблица, поле, вид in МЕТАДАННЫЕ:
		if not frappe.db.table_exists(таблица):
			continue
		условие = _условие(доктайп, поле, вид)
		if сколько := frappe.db.count(таблица, условие):
			frappe.db.delete(таблица, условие)
			удалено[таблица] = удалено.get(таблица, 0) + сколько
	таблицы = frappe.db.get_tables(cached=False)
	for таблица, поле in СЛУЖЕБНЫЕ:
		if таблица not in таблицы:
			continue
		[[сколько]] = frappe.db.sql(f"SELECT COUNT(*) FROM `{таблица}` WHERE `{поле}` = %s", доктайп)
		if сколько:
			frappe.db.sql(f"DELETE FROM `{таблица}` WHERE `{поле}` = %s", доктайп)
			удалено[таблица] = сколько
	return удалено


def _условие(доктайп: str, поле: str, вид: tuple[str, str] | None) -> dict:
	условие = {поле: доктайп}
	if вид:
		условие[вид[0]] = вид[1]
	return условие


def _ссылки_workspace(доктайп: str) -> int:
	"""Удаляет ссылки workspace на доктайп и уменьшает счёт ссылок их карточек.

	`Why:` карточка — `Card Break` и следующие за ней ссылки, и редактор
	workspace вырезает карточку по `link_count`: со старым счётом он унёс бы
	вместе с ней первую ссылку соседней. Нулевой счёт редактор считает сам.
	"""
	if not frappe.db.table_exists("Workspace Link"):
		return 0
	страницы = frappe.get_all(
		"Workspace Link",
		filters={"link_type": "DocType", "link_to": доктайп},
		pluck="parent",
		distinct=True,
	)
	удалено = 0
	for страница in страницы:
		карточка = None
		счёт = {}
		лишние = []
		for ссылка in frappe.get_all(
			"Workspace Link",
			filters={"parent": страница, "parenttype": "Workspace"},
			fields=["name", "type", "link_type", "link_to", "link_count"],
			order_by="idx asc",
		):
			if ссылка.type == "Card Break":
				карточка = ссылка
			elif ссылка.link_type == "DocType" and ссылка.link_to == доктайп:
				лишние.append(ссылка.name)
				if карточка and карточка.link_count:
					счёт[карточка.name] = счёт.get(карточка.name, карточка.link_count) - 1
		for имя, link_count in счёт.items():
			frappe.db.set_value(
				"Workspace Link", имя, "link_count", max(0, link_count), update_modified=False
			)
		frappe.db.delete("Workspace Link", {"name": ("in", лишние)})
		удалено += len(лишние)
	return удалено
