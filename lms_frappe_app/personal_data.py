# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Данные пользователя: выгрузка и удаление по запросу (learning-services#689).

Реестр — `user_data_fields` в `hooks.py`: в каких полях стоит пользователь и
что с записью при удалении. Здесь — записи, которые держатся за запись
ученика ссылкой (`ЗАВИСИМЫЕ`), поля пользователя вне реестра (`ВНЕ_РЕЕСТРА`)
и сама процедура.

Запрос приходит на privacy@cenda.app. Убедившись, что пишет владелец
аккаунта, администратор сервера запускает в контейнере backend:

	bench --site <сайт> execute lms_frappe_app.personal_data.выгрузить --args '["<почта>"]'
	bench --site <сайт> execute lms_frappe_app.personal_data.удалить --args '["<почта>"]'

Выгрузка печатает JSON; файлы сдач домашки — в `files`, их копии берутся из
`sites/<сайт>/private/files`.

Штатное удаление Frappe (`Personal Data Deletion Request`) не используется:
оно не удаляет записи, а заменяет в них почту и имя и переименовывает
пользователя — тексты заметок, документов и переписки остались бы. Штатная
выгрузка (`Personal Data Download Request`) читает тот же реестр, но без
дочерних таблиц и зависимых записей; полная — здесь.
"""

from collections import defaultdict

import frappe
from frappe import STANDARD_USERS
from frappe.model.delete_doc import delete_dynamic_links
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning import consent

#: Что процедура делает с записью: удаляет, очищает поле пользователя,
#: оставляет храниться своим сроком (`mode` в реестре) или очищает ссылку на
#: удалённую запись (`ЗАВИСИМЫЕ`).
УДАЛИТЬ = "delete"
ОБЕЗЛИЧИТЬ = "anonymize"
ХРАНИТЬ = "keep"
ОТВЯЗАТЬ = "unlink"

#: Записи без поля пользователя, которые держатся за удаляемую запись ссылкой:
#: `(доктайп, поле ссылки) -> действие`. Удаляемое — записи самого ученика:
#: события и репорты его занятий, ответы его попыток. Отвязываемое — чужие
#: записи, которые могут сослаться на его запись.
ЗАВИСИМЫЕ = {
	("Agent Chat State", "session"): УДАЛИТЬ,
	("Agent Course Report", "session"): УДАЛИТЬ,
	("Agent Quiz Attempt", "session"): УДАЛИТЬ,
	("Agent Quiz Event", "session"): УДАЛИТЬ,
	("Agent Session Event", "session"): УДАЛИТЬ,
	("Agent Quiz Answer", "attempt"): УДАЛИТЬ,
	("Agent Quiz Event", "attempt"): УДАЛИТЬ,
	("Agent Learning Session", "run"): УДАЛИТЬ,
	("Homework Notice", "submission"): УДАЛИТЬ,
	("LMS Quiz Violation Log", "quiz_submission"): УДАЛИТЬ,
	# Дубль чужого репорта теряет оригинал, но не ответ: ответ оригинала
	# переносится в дубль (`_отвязать`).
	("Agent Course Report", "duplicate_of"): ОТВЯЗАТЬ,
	("Agent Student Note", "source_session"): ОТВЯЗАТЬ,
	("Agent Lesson Run Goal", "session"): ОТВЯЗАТЬ,
}

#: Поля пользователя в доктайпах приложения, которых нет в реестре:
#: `(доктайп, поле) -> (действие или None, почему)`. Без действия поле
#: держит аккаунт сотрудника, и удаление ему откажет по ссылке — удаление
#: сотрудника решается вручную.
ВНЕ_РЕЕСТРА = {
	("Organization Invite", "created_by"): (
		ОБЕЗЛИЧИТЬ,
		"в записи — ключ приглашения: выгрузка Frappe отправила бы его письмом",
	),
	("Agent Course Release", "published_by"): (None, "автор, опубликовавший релиз курса"),
	("Agent Note Reply", "author"): (None, "автор курса, ответивший на заметку"),
}

#: Кем становится владелец записи, которая остаётся после удаления.
АНОНИМ = "Guest"
#: Роли, чей аккаунт процедура не удаляет. `Why:` ошибка в адресе стоила бы
#: доступа администратора к платформе.
НЕ_УДАЛЯТЬ_РОЛИ = frozenset({"System Manager"})
#: Имён в одном условии `in`.
ЗА_РАЗ = 500
ТОЧКА = "personal_data_delete"

ПОЛЯ_АККАУНТА = (
	"name",
	"email",
	"first_name",
	"middle_name",
	"last_name",
	"full_name",
	"user_image",
	"language",
	"time_zone",
	"enabled",
	"user_type",
	"creation",
	"last_login",
	"last_active",
	"last_ip",
)


def реестр() -> list[dict]:
	return frappe.get_hooks("user_data_fields", app_name="lms_frappe_app")


def выгрузить(пользователь: str) -> dict:
	"""Все данные пользователя: аккаунт, записи реестра с дочерними таблицами,
	зависимые записи, журнал входов и список файлов."""
	_проверить(пользователь)
	найдено = _удаляемые(пользователь)
	файлы = _файлы(найдено)
	записи = {
		доктайп: [frappe.get_doc(доктайп, имя).as_dict() for имя in sorted(имена)]
		for доктайп, имена in найдено.items()
	}
	for запись in реестр():
		доктайп = запись["doctype"]
		if запись["mode"] == УДАЛИТЬ and not _дочерний(доктайп):
			continue
		строки = frappe.get_all(доктайп, filters={запись["filter_by"]: пользователь}, fields=["*"])
		if строки:
			записи.setdefault(доктайп, []).extend(строки)
	return {
		"account": _аккаунт(пользователь),
		"records": записи,
		"login_log": frappe.get_all(
			"Activity Log",
			filters={"user": пользователь, "operation": ("in", ["Login", "Logout"])},
			fields=["creation", "operation", "status", "ip_address", "subject"],
			order_by="creation asc",
		),
		"files": frappe.get_all(
			"File",
			filters={"name": ("in", файлы)},
			fields=["name", "file_name", "file_url", "attached_to_doctype", "attached_to_name", "file_size"],
		)
		if файлы
		else [],
	}


def удалить(пользователь: str) -> dict:
	"""Удаляет данные ученика и его аккаунт.

	Записи реестра и зависимые удаляются вместе с дочерними таблицами,
	историей правок, комментариями и копиями в корзине; поля пользователя в
	записях команды очищаются, а сами записи остаются; владельцем оставшихся
	записей приложения становится `АНОНИМ`. Записи согласия хранятся, к ним
	добавляется отметка об удалении — с неё идёт срок хранения. Последним
	удаляются аккаунт и контакт.

	Всё — одной точкой сохранения: если аккаунт держит ссылка вне реестра
	(сотрудник, платёж), Frappe откажет при удалении аккаунта, и прежние шаги
	откатятся. Файлы с диска удаляются после этого: удаление с диска не
	откатить.
	"""
	_проверить(пользователь)
	if set(frappe.get_roles(пользователь)) & НЕ_УДАЛЯТЬ_РОЛИ:
		frappe.throw(frappe._("Аккаунт с ролью System Manager процедура не удаляет"), frappe.ValidationError)
	frappe.db.savepoint(ТОЧКА)
	try:
		# Сначала — доступ: агент с токеном, пишущий посреди удаления, оставил
		# бы запись, которой нет в собранном списке.
		frappe.db.delete("OAuth Bearer Token", {"user": пользователь})
		from frappe.sessions import clear_sessions

		clear_sessions(пользователь, keep_current=False, force=True)
		найдено = _удаляемые(пользователь)
		файлы = _файлы(найдено)
		_отвязать(найдено)
		_обезличить(пользователь)
		for доктайп, имена in найдено.items():
			_стереть(доктайп, имена)
		_стереть_строки(пользователь)
		копий = _очистить_корзину(пользователь)
		_снять_владельца(пользователь)
		consent.отметить_удаление(пользователь)
		consent.удалить_пользователя(пользователь)
	except Exception:
		try:
			frappe.db.rollback(save_point=ТОЧКА)
		except Exception:
			# Взаимная блокировка откатывает транзакцию целиком, и точки уже нет.
			frappe.db.rollback()
		raise
	for файл in файлы:
		frappe.delete_doc("File", файл, ignore_permissions=True, delete_permanently=True, ignore_missing=True)
	return {
		"user": пользователь,
		"deleted": {доктайп: len(имена) for доктайп, имена in sorted(найдено.items())},
		"files": len(файлы),
		"deleted_document_copies": копий,
		"at": now_datetime(),
	}


def _проверить(пользователь: str) -> None:
	if пользователь in STANDARD_USERS or not frappe.db.exists("User", пользователь):
		frappe.throw(frappe._("Нет такого пользователя: {0}").format(пользователь), frappe.DoesNotExistError)


def _дочерний(доктайп: str) -> bool:
	return bool(frappe.get_meta(доктайп).istable)


def _цель(доктайп: str, поле: str) -> str:
	return frappe.get_meta(доктайп).get_field(поле).options


def _части(имена) -> list[list[str]]:
	имена = sorted(имена)
	return [имена[i : i + ЗА_РАЗ] for i in range(0, len(имена), ЗА_РАЗ)]


def _по_ссылке(доктайп: str, поле: str, имена) -> set[str]:
	найдено = set()
	for часть in _части(имена):
		найдено.update(frappe.get_all(доктайп, filters={поле: ("in", часть)}, pluck="name"))
	return найдено


def _удаляемые(пользователь: str) -> dict[str, set[str]]:
	"""Удаляемые записи по доктайпам: по полям реестра, затем зависимые — пока
	находятся новые (ответы попыток, найденных через занятия)."""
	найдено: dict[str, set[str]] = defaultdict(set)
	for запись in реестр():
		доктайп = запись["doctype"]
		if запись["mode"] == УДАЛИТЬ and not _дочерний(доктайп):
			найдено[доктайп].update(
				frappe.get_all(доктайп, filters={запись["filter_by"]: пользователь}, pluck="name")
			)
	прибавилось = True
	while прибавилось:
		прибавилось = False
		for (доктайп, поле), действие in ЗАВИСИМЫЕ.items():
			цели = найдено.get(_цель(доктайп, поле)) if действие == УДАЛИТЬ else None
			if not цели:
				continue
			новые = _по_ссылке(доктайп, поле, цели) - найдено[доктайп]
			if новые:
				найдено[доктайп] |= новые
				прибавилось = True
	return {доктайп: имена for доктайп, имена in найдено.items() if имена}


def _файлы(найдено: dict[str, set[str]]) -> list[str]:
	файлы = []
	for доктайп, имена in найдено.items():
		for часть in _части(имена):
			файлы += frappe.get_all(
				"File",
				filters={"attached_to_doctype": доктайп, "attached_to_name": ("in", часть)},
				pluck="name",
			)
	return sorted(файлы)


def _отвязать(найдено: dict[str, set[str]]) -> None:
	for (доктайп, поле), действие in ЗАВИСИМЫЕ.items():
		цели = найдено.get(_цель(доктайп, поле)) if действие == ОТВЯЗАТЬ else None
		for часть in _части(цели or ()):
			if (доктайп, поле) == ("Agent Course Report", "duplicate_of"):
				_перенести_ответ(часть)
			_записать(доктайп, {поле: ("in", часть)}, поле, None)


def _перенести_ответ(оригиналы: list[str]) -> None:
	"""Дубль без своего ответа показывал ученику ответ оригинала — он остаётся
	дублю. `Why:` иначе удаление чужого аккаунта стёрло бы ответ, который
	ученик уже получил."""
	ответы = dict(
		frappe.get_all(
			"Agent Course Report",
			filters={"name": ("in", оригиналы), "resolution": ("is", "set")},
			fields=["name", "resolution"],
			as_list=True,
		)
	)
	for дубль in frappe.get_all(
		"Agent Course Report",
		filters={"duplicate_of": ("in", list(ответы)), "resolution": ("is", "not set")},
		fields=["name", "duplicate_of"],
	):
		frappe.db.set_value(
			"Agent Course Report", дубль.name, "resolution", ответы[дубль.duplicate_of], update_modified=False
		)


def _записать(доктайп: str, условие: dict, поле: str, значение) -> None:
	"""Запись по найденным именам, а не по условию. `Why:` UPDATE по полю без
	индекса запер бы до конца удаления все строки таблицы — занятия и попытки
	всех учеников."""
	for часть in _части(frappe.get_all(доктайп, filters=условие, pluck="name")):
		frappe.db.set_value(доктайп, {"name": ("in", часть)}, поле, значение, update_modified=False)


def _обезличить(пользователь: str) -> None:
	поля = [(з["doctype"], з["filter_by"]) for з in реестр() if з["mode"] == ОБЕЗЛИЧИТЬ]
	поля += [ключ for ключ, (действие, _) in ВНЕ_РЕЕСТРА.items() if действие == ОБЕЗЛИЧИТЬ]
	for доктайп, поле in поля:
		_записать(доктайп, {поле: пользователь}, поле, None)


def _стереть_строки(пользователь: str) -> None:
	"""Строки дочерних таблиц реестра — из своих таблиц: родитель (назначение,
	программа) остаётся."""
	for запись in реестр():
		if запись["mode"] == УДАЛИТЬ and _дочерний(запись["doctype"]):
			имена = frappe.get_all(
				запись["doctype"], filters={запись["filter_by"]: пользователь}, pluck="name"
			)
			for часть in _части(имена):
				frappe.db.delete(запись["doctype"], {"name": ("in", часть)})


def _стереть(доктайп: str, имена: set[str]) -> None:
	"""Записи — без копии в корзине, с дочерними таблицами и следами
	(`delete_dynamic_links`: история правок, комментарии, подписки).

	Прямым удалением, а не `delete_doc`: тот удаляет вложения с диска сразу,
	и отказ на следующем шаге уже не откатить; и он зовёт `on_trash`, а
	журнал занятия удалять запрещает (`Agent Session Event`).
	"""
	таблицы = [поле.options for поле in frappe.get_meta(доктайп).get_table_fields()]
	for часть in _части(имена):
		for таблица in таблицы:
			frappe.db.delete(таблица, {"parenttype": доктайп, "parent": ("in", часть)})
		frappe.db.delete(доктайп, {"name": ("in", часть)})
		for имя in часть:
			delete_dynamic_links(доктайп, имя)


def _очистить_корзину(пользователь: str) -> int:
	"""Копии удалённых раньше записей ученика в корзине (`Deleted Document`) —
	с их историей правок. `Why:` удаление в Desk и сброс прогресса кладут туда
	полный текст записи."""
	поля = defaultdict(set)
	for запись in реестр():
		поля[запись["doctype"]].add(запись["filter_by"])
	# Подстрока в JSON — только отбор: совпадение проверяется по полям ниже.
	образец = "%" + пользователь.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
	копии = frappe.get_all(
		"Deleted Document",
		filters={"deleted_doctype": ("in", sorted(поля)), "data": ("like", образец)},
		fields=["name", "deleted_doctype", "deleted_name", "data"],
	)
	удалено = 0
	for копия in копии:
		данные = frappe.parse_json(копия.data) or {}
		if not any(данные.get(поле) == пользователь for поле in поля[копия.deleted_doctype]):
			continue
		delete_dynamic_links(копия.deleted_doctype, копия.deleted_name)
		frappe.db.delete("Deleted Document", {"name": копия.name})
		удалено += 1
	return удалено


def _снять_владельца(пользователь: str) -> None:
	"""Владелец и автор правки оставшихся записей приложения — `АНОНИМ`: в них
	почта пользователя. Записи согласия хранятся как есть."""
	хранить = {з["doctype"] for з in реестр() if з["mode"] == ХРАНИТЬ}
	доктайпы = frappe.get_all(
		"DocType",
		filters={"module": ("in", frappe.get_module_list("lms_frappe_app")), "issingle": 0, "is_virtual": 0},
		pluck="name",
	)
	for доктайп in sorted(set(доктайпы) - хранить):
		for поле in ("owner", "modified_by"):
			_записать(доктайп, {поле: пользователь}, поле, АНОНИМ)


def _аккаунт(пользователь: str) -> dict:
	аккаунт = frappe.db.get_value("User", пользователь, list(ПОЛЯ_АККАУНТА), as_dict=True)
	аккаунт["roles"] = frappe.get_roles(пользователь)
	аккаунт["social_logins"] = frappe.get_all(
		"User Social Login",
		filters={"parent": пользователь, "parenttype": "User"},
		fields=["provider", "userid", "username"],
	)
	аккаунт["contacts"] = [
		frappe.get_doc("Contact", имя).as_dict()
		for имя in frappe.get_all("Contact", filters={"user": пользователь}, pluck="name")
	]
	return аккаунт

