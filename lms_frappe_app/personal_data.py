# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Данные пользователя: выгрузка и удаление по запросу, сроки журналов
(learning-services#689).

Реестр — `user_data_fields` в `hooks.py`: в каких полях стоит пользователь и
что с записью при удалении. Здесь — записи, которые держатся за запись
ученика ссылкой (`ЗАВИСИМЫЕ`), поля пользователя вне реестра (`ВНЕ_РЕЕСТРА`)
и сама процедура.

Запрос приходит на privacy@cenda.app. Убедившись, что пишет владелец
аккаунта, администратор сервера запускает в контейнере backend:

	bench --site <сайт> execute lms_frappe_app.personal_data.выгрузить --args '["<почта>"]'
	bench --site <сайт> execute lms_frappe_app.personal_data.удалить --args '["<почта>"]'

Выгрузка печатает JSON; файлы сдач домашки — в `files`, их копии берутся из
`sites/<сайт>/private/files`. Отказ `bench execute` печатает двумя
трассировками: последняя строка — `NameError: name 'lms_frappe_app' is not
defined` от повторной попытки самого bench, причина — в первой.

Штатные запросы Frappe закрыты (`закрыть_штатные_запросы`,
`отказать_штатному_запросу`): удаление Frappe (`Personal Data Deletion
Request`) не удаляет записи, а заменяет в текстах почту и имя и
переименовывает пользователя — данные остались бы под псевдонимом, а
согласие ссылалось бы на живой переименованный аккаунт. Выгрузка Frappe
отдаёт строки реестра целиком, с чужим в них, и без дочерних таблиц.
"""

import re
from collections import defaultdict

import frappe
from frappe import STANDARD_USERS
from frappe.model.delete_doc import delete_dynamic_links
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning import consent
from lms_frappe_app.agent_learning.constants import ЧЛЕНСТВО_ДЕЙСТВУЕТ

#: Что процедура делает с записью: удаляет, очищает поле пользователя,
#: оставляет храниться своим сроком (`mode` в реестре) или очищает ссылку на
#: удалённую запись (`ЗАВИСИМЫЕ`).
УДАЛИТЬ = "delete"
ОБЕЗЛИЧИТЬ = "anonymize"
ХРАНИТЬ = "keep"
ОТВЯЗАТЬ = "unlink"

#: Записи без поля пользователя, которые держатся за удаляемую запись ссылкой:
#: `(доктайп, поле ссылки) -> действие`. Удаляемое — записи самого ученика:
#: события его занятий, ответы его попыток. Отвязываемое — записи, которые
#: остаются: замечание к курсу остаётся автору курса без занятия и имени
#: (`_обезличить_замечания`), чужие записи теряют ссылку.
ЗАВИСИМЫЕ = {
	("Agent Chat State", "session"): УДАЛИТЬ,
	("Agent Quiz Attempt", "session"): УДАЛИТЬ,
	("Agent Quiz Event", "session"): УДАЛИТЬ,
	("Agent Session Event", "session"): УДАЛИТЬ,
	("Agent Quiz Answer", "attempt"): УДАЛИТЬ,
	("Agent Quiz Event", "attempt"): УДАЛИТЬ,
	("Agent Learning Session", "run"): УДАЛИТЬ,
	("Homework Notice", "submission"): УДАЛИТЬ,
	("LMS Quiz Violation Log", "quiz_submission"): УДАЛИТЬ,
	("Personal Data Download Request", "amended_from"): УДАЛИТЬ,
	("Agent Course Report", "session"): ОТВЯЗАТЬ,
	("Agent Student Note", "source_session"): ОТВЯЗАТЬ,
	("Agent Lesson Run Goal", "session"): ОТВЯЗАТЬ,
	# Чужие ответы в его теме обсуждения остаются — без темы.
	("Discussion Reply", "topic"): ОТВЯЗАТЬ,
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

#: Поля выгрузки для записей, в которых есть чужое: уведомление куратору о
#: чужой сдаче, проверка чужой домашки, команда, замечание к курсу. Прочие
#: записи вне `удаляемых` выгружаются именем, датой и полем пользователя.
ПОЛЯ_ВЫГРУЗКИ = {
	"Homework Notice": ["name", "kind", "creation"],
	"Agent Homework Event": ["name", "parent", "parenttype", "event", "at", "creation"],
	"Learning Organization": ["name", "organization_name", "creation"],
	"Agent Course Report": ["name", "course", "lesson", "kind", "text", "status", "resolution", "creation"],
}

#: Кем становится владелец записи, которая остаётся после удаления.
АНОНИМ = "Guest"
#: Чей аккаунт удаляет процедура — ученика: тип `Website User` и ни одной роли
#: сотрудника. `Why:` служебные учётки (Agent Service, публикатор), авторы и
#: кураторы держат чужие записи и работу платформы; ошибка в адресе стоила бы
#: её. Роли авторов Learning — без доступа в Desk, и тип их не отличает.
УДАЛЯЕМЫЙ_ТИП = "Website User"
РОЛИ_СОТРУДНИКОВ = frozenset(
	{"System Manager", "Agent Service", "Course Creator", "Moderator", "Batch Evaluator", "Release Publisher"}
)
#: Администратор команды. У команды он должен остаться (`api.team`).
АДМИН_КОМАНДЫ = "Org Admin"
#: Чем заменяются почта и имя в тексте замечания.
УБРАНО = "[удалено]"
#: Имён в одном условии `in`.
ЗА_РАЗ = 500
ТОЧКА = "personal_data_delete"

#: Штатные веб-формы Frappe: запрос на удаление аккаунта и на копию данных.
ФОРМЫ_FRAPPE = ("request-to-delete-data", "request-data")

#: Сроки журналов в днях — политика конфиденциальности, раздел 10: журнал
#: входов, журнал ошибок, очередь писем, токены ассистентов после истечения.
СРОКИ_ЖУРНАЛОВ = {
	"Activity Log": 90,
	"Error Log": 14,
	"Email Queue": 30,
	"OAuth Bearer Token": 30,
}

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
	зависимые записи и замечания к курсу, журнал входов и список файлов. Из
	записей с чужим — только свои поля (`ПОЛЯ_ВЫГРУЗКИ`)."""
	_проверить(пользователь)
	найдено = _удаляемые(пользователь)
	файлы = _файлы(найдено)
	записи = {доктайп: _выгрузка(доктайп, имена) for доктайп, имена in найдено.items()}
	for запись in реестр():
		доктайп, поле = запись["doctype"], запись["filter_by"]
		if запись["mode"] == УДАЛИТЬ and not _дочерний(доктайп):
			continue
		свои = ["name", "creation", поле] + (["parent", "parenttype"] if _дочерний(доктайп) else [])
		поля = ["*"] if запись["mode"] == ХРАНИТЬ else ПОЛЯ_ВЫГРУЗКИ.get(доктайп, свои)
		строки = frappe.get_all(доктайп, filters={поле: пользователь}, fields=поля)
		if строки:
			записи.setdefault(доктайп, []).extend(строки)
	if замечания := _замечания(найдено):
		записи["Agent Course Report"] = _выгрузка("Agent Course Report", замечания)
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

	Сначала, отдельным коммитом, закрывается доступ: токены и сессии. Дальше —
	одной точкой сохранения: записи реестра и зависимые удаляются вместе с
	дочерними таблицами, историей правок, комментариями и копиями в корзине;
	поля пользователя в записях команды очищаются, а сами записи остаются;
	замечания к курсу остаются автору без занятия, почты и имени; владельцем
	оставшихся записей приложения становится `АНОНИМ`. Записи согласия
	хранятся, к ним добавляется отметка об удалении — с неё идёт срок
	хранения. Последними удаляются аккаунт и контакт.

	Если аккаунт держит ссылка вне реестра (автор, платёж), Frappe откажет при
	удалении аккаунта, и всё после закрытия доступа откатится. Файлы с диска
	удаляются после коммита: удаление с диска не откатить.
	"""
	_проверить(пользователь)
	if frappe.db.get_value("User", пользователь, "user_type") != УДАЛЯЕМЫЙ_ТИП or (
		РОЛИ_СОТРУДНИКОВ & set(frappe.get_roles(пользователь))
	):
		frappe.throw(
			frappe._("Процедура удаляет только аккаунты учеников; служебные учётки и сотрудники — вручную"),
			frappe.ValidationError,
		)
	if команды := _последний_администратор(пользователь):
		frappe.throw(
			frappe._("Последний администратор команд: {0}. Сначала назначьте другого администратора").format(
				", ".join(команды)
			),
			frappe.ValidationError,
		)
	_закрыть_доступ(пользователь)
	frappe.db.savepoint(ТОЧКА)
	try:
		найдено = _удаляемые(пользователь)
		файлы = _файлы(найдено)
		_обезличить_замечания(пользователь, найдено)
		_отвязать(найдено)
		_обезличить(пользователь)
		for доктайп, имена in найдено.items():
			_стереть(доктайп, имена)
		_стереть_строки(пользователь)
		копий = _очистить_корзину(пользователь)
		_стереть_следы(пользователь)
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
	frappe.db.commit()
	for файл in файлы:
		# Каждый файл — своим коммитом: сбой на одном не оставляет остальные.
		try:
			frappe.delete_doc(
				"File", файл, ignore_permissions=True, delete_permanently=True, ignore_missing=True
			)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Удаление данных ученика: файл {файл}")
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


def _последний_администратор(пользователь: str) -> list[str]:
	"""Команды, где он единственный действующий администратор."""
	действует = {"role": АДМИН_КОМАНДЫ, "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ}
	return [
		команда
		for команда in frappe.get_all(
			"Organization Membership", filters={"user": пользователь, **действует}, pluck="organization"
		)
		if not frappe.db.exists(
			"Organization Membership", {"organization": команда, "user": ("!=", пользователь), **действует}
		)
	]


def _закрыть_доступ(пользователь: str) -> None:
	"""Токены, коды авторизации и сессии — до сбора записей и своим коммитом.

	`Why:` агент с токеном, пишущий посреди удаления, оставил бы запись вне
	собранного списка; а `clear_sessions` Frappe коммитит сам и снял бы точку
	сохранения. Доступ закрыт, даже если удаление дальше откажет: человек
	просил удалить аккаунт.
	"""
	from frappe.sessions import clear_sessions

	frappe.db.delete("OAuth Bearer Token", {"user": пользователь})
	frappe.db.delete("OAuth Authorization Code", {"user": пользователь})
	clear_sessions(пользователь, keep_current=False, force=True)
	frappe.db.commit()


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


def _доктайпы_приложения() -> list[str]:
	return frappe.get_all(
		"DocType",
		filters={"module": ("in", frappe.get_module_list("lms_frappe_app")), "issingle": 0, "is_virtual": 0},
		pluck="name",
	)


def _образец(пользователь: str) -> str:
	"""`LIKE` по подстроке — только отбор: совпадение проверяет код."""
	return "%" + пользователь.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _адрес(пользователь: str) -> re.Pattern:
	"""Почта целиком, а не частью другой: `an@gmail.com` не найдётся в
	`ivan@gmail.com`."""
	return re.compile(r"(?<![\w.+-])" + re.escape(пользователь) + r"(?![\w.-])")


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


def _выгрузка(доктайп: str, имена) -> list[dict]:
	if поля := ПОЛЯ_ВЫГРУЗКИ.get(доктайп):
		строки = []
		for часть in _части(имена):
			строки += frappe.get_all(доктайп, filters={"name": ("in", часть)}, fields=поля)
		return строки
	return [frappe.get_doc(доктайп, имя).as_dict() for имя in sorted(имена)]


def _замечания(найдено: dict[str, set[str]]) -> set[str]:
	return _по_ссылке("Agent Course Report", "session", найдено.get("Agent Learning Session", ()))


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


def _обезличить_замечания(пользователь: str, найдено: dict[str, set[str]]) -> None:
	"""Почта и имя в тексте замечания и в ответе автора — `УБРАНО`; занятие
	отвяжет `_отвязать`, владельца снимет `_снять_владельца`.

	`Why:` замечание — материал автора курса о курсе, а не о человеке:
	удалённое, оно пропало бы из работы автора и из ответа дублям. Без
	занятия, почты и имени это уже не персональные данные (политика §6 —
	обезличивание).
	"""
	имя = frappe.db.get_value("User", пользователь, "full_name") or ""
	# Имя — целым словом и как записано: короткое или частью слова заменило бы
	# и текст. Имя в другом падеже или без фамилии так не найти.
	шаблоны = [_адрес(пользователь).pattern]
	if len(имя) >= 4:
		шаблоны.append(r"(?<!\w)" + re.escape(имя) + r"(?!\w)")
	шаблон = re.compile("|".join(шаблоны))
	поля = ["text", "objective", "resolution"]
	for часть in _части(_замечания(найдено)):
		for замечание in frappe.get_all(
			"Agent Course Report", filters={"name": ("in", часть)}, fields=["name", *поля]
		):
			правка = {
				поле: шаблон.sub(УБРАНО, замечание[поле])
				for поле in поля
				if замечание[поле] and шаблон.search(замечание[поле])
			}
			if правка:
				frappe.db.set_value("Agent Course Report", замечание.name, правка, update_modified=False)


def _отвязать(найдено: dict[str, set[str]]) -> None:
	for (доктайп, поле), действие in ЗАВИСИМЫЕ.items():
		цели = найдено.get(_цель(доктайп, поле)) if действие == ОТВЯЗАТЬ else None
		for часть in _части(цели or ()):
			_записать(доктайп, {поле: ("in", часть)}, поле, None)


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


def _упоминает(данные: dict, пользователь: str) -> bool:
	"""Пользователь — значение поля записи или строки её таблицы, в том числе
	владелец."""
	for значение in данные.values():
		if значение == пользователь:
			return True
		if isinstance(значение, list) and any(
			isinstance(строка, dict) and пользователь in строка.values() for строка in значение
		):
			return True
	return False


def _очистить_корзину(пользователь: str) -> int:
	"""Копии удалённых раньше записей ученика в корзине (`Deleted Document`) —
	с их историей правок: записи приложения и реестра, где он стоит в любом
	поле или владельцем. `Why:` удаление в Desk и сброс прогресса кладут туда
	полный текст записи."""
	доктайпы = {з["doctype"] for з in реестр()} | set(_доктайпы_приложения()) | {д for д, _ in ЗАВИСИМЫЕ}
	копии = frappe.get_all(
		"Deleted Document",
		filters={"deleted_doctype": ("in", sorted(доктайпы)), "data": ("like", _образец(пользователь))},
		fields=["name", "deleted_doctype", "deleted_name", "data"],
	)
	удалено = 0
	for копия in копии:
		if not _упоминает(frappe.parse_json(копия.data) or {}, пользователь):
			continue
		# Следы — только если имя не занято живой записью: у команды имя —
		# её название, и новая команда с тем же названием потеряла бы свои.
		if not frappe.db.exists(копия.deleted_doctype, копия.deleted_name):
			delete_dynamic_links(копия.deleted_doctype, копия.deleted_name)
		frappe.db.delete("Deleted Document", {"name": копия.name})
		удалено += 1
	return удалено


def _стереть_следы(пользователь: str) -> None:
	"""История правок и комментарии на оставшихся записях приложения — его
	правки и правки, где стоит его почта (создатель команды, член назначения).
	Правка удаляется целиком, с тем, что в ней поменяли другие поля. Записи
	согласия хранятся как есть."""
	хранить = {з["doctype"] for з in реестр() if з["mode"] == ХРАНИТЬ}
	доктайпы = sorted(set(_доктайпы_приложения()) - хранить)
	адрес = _адрес(пользователь)
	правки = [
		правка.name
		for правка in frappe.get_all(
			"Version",
			filters={"ref_doctype": ("in", доктайпы)},
			or_filters={"owner": пользователь, "data": ("like", _образец(пользователь))},
			fields=["name", "owner", "data"],
		)
		if правка.owner == пользователь or адрес.search(правка.data or "")
	]
	комментарии = frappe.get_all(
		"Comment",
		filters={"reference_doctype": ("in", доктайпы)},
		or_filters={"owner": пользователь, "comment_email": пользователь},
		pluck="name",
	)
	for доктайп, имена in (("Version", правки), ("Comment", комментарии)):
		for часть in _части(имена):
			frappe.db.delete(доктайп, {"name": ("in", часть)})


def _снять_владельца(пользователь: str) -> None:
	"""Владелец и автор правки оставшихся записей приложения — `АНОНИМ`: в них
	почта пользователя. Записи согласия хранятся как есть."""
	хранить = {з["doctype"] for з in реестр() if з["mode"] == ХРАНИТЬ}
	for доктайп in sorted(set(_доктайпы_приложения()) - хранить):
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


def закрыть_штатные_запросы() -> None:
	"""`after_migrate`: штатные запросы Frappe на удаление и копию данных
	закрыты — веб-формы сняты с публикации, автоудаление по запросу выключено.

	`Why:` формы отвечают без входа, а ежечасная задача Frappe
	(`process_data_deletion_request`) через `auto_account_deletion` часов
	«удаляет» подтверждённый запрос по реестру — заменой почты и имени в
	текстах и переименованием пользователя. Данные остались бы, а запрос шёл бы
	мимо проверки, что пишет владелец. Пишется только расходящееся: миграция
	идёт на каждом старте контейнера.
	"""
	if frappe.db.get_single_value("Website Settings", "auto_account_deletion"):
		frappe.db.set_single_value("Website Settings", "auto_account_deletion", 0)
	for форма in ФОРМЫ_FRAPPE:
		if frappe.db.get_value("Web Form", форма, "published"):
			frappe.db.set_value("Web Form", форма, "published", 0)


def отказать_штатному_запросу(doc, method=None) -> None:
	"""Хук `before_insert` штатных запросов Frappe: запрос — письмом.

	`Why:` форму можно вернуть в публикацию правкой в Desk, а запрос
	создаётся и через REST; процедура приложения одна — `удалить`/`выгрузить`
	по письму на `consent.ПОЧТА_ДАННЫХ`.
	"""
	frappe.throw(
		frappe._("Копию данных и удаление аккаунта запрашивают письмом на {0}").format(consent.ПОЧТА_ДАННЫХ),
		frappe.ValidationError,
	)


def закрепить_сроки_журналов(doc, method=None) -> None:
	"""Хук `validate` Log Settings: сроки `СРОКИ_ЖУРНАЛОВ`, какие бы ни ввели.

	`Why:` срок обещан политикой, а Desk позволяет его поменять, и обещание
	разошлось бы с хранением молча.
	"""
	строки = {строка.ref_doctype: строка for строка in doc.logs_to_clear}
	for доктайп, дней in СРОКИ_ЖУРНАЛОВ.items():
		if строка := строки.get(доктайп):
			строка.days = дней
		else:
			doc.append("logs_to_clear", {"ref_doctype": доктайп, "days": дней})


def обеспечить_сроки_журналов() -> None:
	"""`after_migrate`: сроки в Log Settings, если они расходятся с политикой.
	Только при расхождении: миграция идёт на каждом старте контейнера."""
	настройки = frappe.get_single("Log Settings")
	текущие = {строка.ref_doctype: строка.days for строка in настройки.logs_to_clear}
	if any(текущие.get(доктайп) != дней for доктайп, дней in СРОКИ_ЖУРНАЛОВ.items()):
		настройки.save(ignore_permissions=True)
