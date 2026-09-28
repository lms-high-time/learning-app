# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Изоляция организаций.

Менеджер видит обучение своей компании и не видит чужого. Ограничение стоит
на правах Frappe — `permission_query_conditions` для списков и `has_permission`
для прямого обращения по имени записи, — поэтому работает одинаково в браузере
и в MCP: оба канала ходят от имени пользователя.

**Источник правды о принадлежности — `Organization Membership`**: одна запись
об одном факте. `User Permission` стал бы её второй копией, а две копии
разъезжаются молча.

**Запись в эти DocType идёт только через whitelisted-методы.** Колбэки
отказывают в любом праве, кроме чтения: у роли ученика в схеме остаётся один
`read`, и прямое обращение к `/api/resource/...` записать ничего не может.
"""

from __future__ import annotations

import frappe

from lms_frappe_app.agent_learning.constants import ЧЛЕНСТВО_ДЕЙСТВУЕТ

ВСЕВИДЯЩИЕ_РОЛИ = frozenset({"System Manager", "Administrator", "Agent Service"})

#: Кому разрешено заводить и править записи из интерфейса. `Why:` хук
#: `has_permission` умеет только отнимать: без этого списка модератор,
#: у которого в схеме стоят create/write, получал бы «Not permitted» —
#: организации и назначения заводить стало бы некому, кроме администратора.
АДМИНИСТРАТИВНЫЕ_РОЛИ = ВСЕВИДЯЩИЕ_РОЛИ | {"Moderator", "Course Creator"}
РОЛИ_МЕНЕДЖЕРА = ("Manager", "Org Admin")

#: Права, которые вообще может получить обычный пользователь. Всё остальное —
#: запись, создание, удаление, отправка, шаринг — только у привилегированных
#: ролей: любое изменение проходит через whitelisted-методы, а не напрямую.
ЧИТАЮЩИЕ_ПРАВА = frozenset({"read", "select", "report", "export", "print"})


def _только_чтение(ptype: str, user: str) -> bool:
	"""Прямая запись запрещена всем, кроме привилегированных ролей.

	`Why:` роль ученика имеет доступ к DocType, а Frappe раздаёт права по
	схеме — без этой проверки ученик правит собственную попытку квиза через
	`/api/resource/...` и выставляет себе зачёт, минуя сверку. Проверено
	эксплуатацией: PUT со `score` проходил и записывался.
	"""
	return ptype in ЧИТАЮЩИЕ_ПРАВА or bool(
		set(frappe.get_roles(user)) & АДМИНИСТРАТИВНЫЕ_РОЛИ
	)


def видит_всё(user: str) -> bool:
	"""Роли, для которых изоляция не применяется."""
	return bool(set(frappe.get_roles(user)) & ВСЕВИДЯЩИЕ_РОЛИ)


def организации_менеджера(user: str) -> list[str]:
	"""Организации, обучение которых пользователь вправе видеть целиком.

	Роль Frappe `Organization Manager` даёт саму возможность смотреть отчёты,
	а вот **какие именно** организации — определяет членство. Без членства
	роль не открывает ничего: иначе первый же менеджер увидел бы всех.
	"""
	if "Organization Manager" not in frappe.get_roles(user):
		return []
	return frappe.get_all(
		"Organization Membership",
		filters={"user": user, "role": ("in", РОЛИ_МЕНЕДЖЕРА), "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ},
		pluck="organization",
	)


def _список(значения: list[str]) -> str:
	return ", ".join(frappe.db.escape(значение) for значение in значения)


def условие_членства(user: str | None = None) -> str:
	"""`Organization Membership`: своё членство плюс состав своей организации."""
	user = user or frappe.session.user
	if видит_всё(user):
		return ""
	свои = f"`tabOrganization Membership`.`user` = {frappe.db.escape(user)}"
	организации = организации_менеджера(user)
	if not организации:
		return свои
	return f"({свои} or `tabOrganization Membership`.`organization` in ({_список(организации)}))"


def условие_назначения(user: str | None = None) -> str:
	"""`Course Allocation`: назначения своих организаций.

	Ученик видит назначения компаний, в которых состоит: по ним он получает
	дедлайны и понимает, что курс обязателен. Чужие — не видит вовсе.
	"""
	from lms_frappe_app.agent_learning.doctype.learning_organization.learning_organization import (
		организации_пользователя,
	)

	user = user or frappe.session.user
	if видит_всё(user):
		return ""
	организации = организации_пользователя(user)
	if not организации:
		# Не «покажем всё», а «не покажем ничего»: пустое условие в Frappe
		# означает отсутствие ограничений, и ошибка здесь открыла бы чужие
		# назначения целиком.
		return "1 = 0"
	return f"`tabCourse Allocation`.`organization` in ({_список(организации)})"


def условие_занятия(user: str | None = None) -> str:
	"""`Agent Learning Session`: свои занятия, менеджеру — занятия его людей."""
	user = user or frappe.session.user
	if видит_всё(user):
		return ""
	свои = f"`tabAgent Learning Session`.`student` = {frappe.db.escape(user)}"
	организации = организации_менеджера(user)
	if not организации:
		return свои
	подзапрос = (
		"select user from `tabOrganization Membership` "
		f"where organization in ({_список(организации)}) and status = '{ЧЛЕНСТВО_ДЕЙСТВУЕТ}'"
	)
	return f"({свои} or `tabAgent Learning Session`.`student` in ({подзапрос}))"


def доступно_занятие(doc, ptype: str = "read", user: str | None = None) -> bool:
	"""Права на конкретное занятие.

	Нужен рядом с фильтром списка: без него чужое занятие остаётся доступным
	по прямому обращению по имени записи — а именно так его и попробуют взять.
	"""
	user = user or frappe.session.user
	if not _только_чтение(ptype, user):
		return False
	if видит_всё(user) or doc.student == user:
		return True
	организации = организации_менеджера(user)
	if not организации:
		return False
	return bool(
		frappe.db.exists(
			"Organization Membership",
			{"user": doc.student, "organization": ("in", организации), "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ},
		)
	)


def доступно_членство(doc, ptype: str = "read", user: str | None = None) -> bool:
	user = user or frappe.session.user
	if not _только_чтение(ptype, user):
		# Иначе ученик вписывает себя в любую организацию: `doc.user == user`
		# выполняется, а хук выдачи зачислений сразу открывает ему её курсы.
		return False
	if видит_всё(user) or doc.user == user:
		return True
	return doc.organization in организации_менеджера(user)


def доступно_назначение(doc, ptype: str = "read", user: str | None = None) -> bool:
	from lms_frappe_app.agent_learning.doctype.learning_organization.learning_organization import (
		организации_пользователя,
	)

	user = user or frappe.session.user
	if not _только_чтение(ptype, user):
		# Иначе рядовой участник правит дедлайны и состав адресатов.
		return False
	return видит_всё(user) or doc.organization in организации_пользователя(user)


def условие_события(user: str | None = None) -> str:
	"""`Agent Session Event`: журнал тех занятий, которые пользователю видны."""
	user = user or frappe.session.user
	if видит_всё(user):
		return ""
	видимые_занятия = (
		f"select name from `tabAgent Learning Session` where {условие_занятия(user)}"
	)
	return f"`tabAgent Session Event`.`session` in ({видимые_занятия})"


def доступно_событие(doc, ptype: str = "read", user: str | None = None) -> bool:
	user = user or frappe.session.user
	if видит_всё(user):
		return True
	занятие = frappe.db.get_value(
		"Agent Learning Session", doc.session, ["name", "student"], as_dict=True
	)
	return bool(занятие) and доступно_занятие(занятие, ptype, user)


def условие_попытки(user: str | None = None) -> str:
	"""`Agent Quiz Attempt`: свои попытки, менеджеру — попытки его людей."""
	user = user or frappe.session.user
	if видит_всё(user):
		return ""
	свои = f"`tabAgent Quiz Attempt`.`student` = {frappe.db.escape(user)}"
	организации = организации_менеджера(user)
	if not организации:
		return свои
	подзапрос = (
		"select user from `tabOrganization Membership` "
		f"where organization in ({_список(организации)}) and status = '{ЧЛЕНСТВО_ДЕЙСТВУЕТ}'"
	)
	return f"({свои} or `tabAgent Quiz Attempt`.`student` in ({подзапрос}))"


def доступна_попытка(doc, ptype: str = "read", user: str | None = None) -> bool:
	"""Права на конкретную попытку.

	`Why:` без этого `check_permission("read")` проходил у любого ученика на
	любую попытку — можно было отвечать в чужую и закрывать её за человека.
	"""
	user = user or frappe.session.user
	if not _только_чтение(ptype, user):
		return False
	if видит_всё(user) or doc.student == user:
		return True
	организации = организации_менеджера(user)
	return bool(организации) and bool(
		frappe.db.exists(
			"Organization Membership",
			{"user": doc.student, "organization": ("in", организации), "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ},
		)
	)


def условие_ответа(user: str | None = None) -> str:
	"""`Agent Quiz Answer`: только свои ответы.

	`Why:` в записи ответа лежит текст ответа рядом с признаком верности —
	это готовый эталон. Руководитель нередко проходит тот же курс, что и его
	сотрудники, и чтение чужих ответов дало бы ему ответы на собственный
	квиз. Агрегаты по сотрудникам он получает методом отчётности, где
	текстов нет.
	"""
	user = user or frappe.session.user
	if видит_всё(user):
		return ""
	свои = (
		"select name from `tabAgent Quiz Attempt` where student = "
		f"{frappe.db.escape(user)}"
	)
	return f"`tabAgent Quiz Answer`.`attempt` in ({свои})"


def доступен_ответ(doc, ptype: str = "read", user: str | None = None) -> bool:
	"""Свой ответ — да, чужой — нет даже руководителю: см. `условие_ответа`."""
	user = user or frappe.session.user
	if not _только_чтение(ptype, user):
		return False
	if видит_всё(user):
		return True
	return frappe.db.get_value("Agent Quiz Attempt", doc.attempt, "student") == user


def свои_организации_пересекаются(менеджер: str, ученик: str) -> bool:
	"""Состоит ли ученик в организации, которой управляет вызывающий.

	Нужна явной проверкой в методах отчётности: `frappe.get_all` — это
	`get_list(ignore_permissions=True)`, и `permission_query_conditions` к нему
	не применяются **никогда**. Изоляция, построенная только на хуках, в этих
	методах не работала — проверено эксплуатацией.
	"""
	if видит_всё(менеджер):
		return True
	организации = организации_менеджера(менеджер)
	if not организации:
		return False
	return bool(
		frappe.db.exists(
			"Organization Membership",
			{"user": ученик, "organization": ("in", организации), "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ},
		)
	)


def условие_заметки(user: str | None = None) -> str:
	"""`Agent Student Note`: только свои, менеджеру — ничего.

	`Why:` заметки об ученике — про разговор, а не про результат. Менеджеру
	идёт покрытие целей урока, и граница проходит по сущности целиком: так её
	видно в одном месте, вместо фильтра полей по роли в каждом методе.
	"""
	user = user or frappe.session.user
	if видит_всё(user):
		return ""
	return f"`tabAgent Student Note`.`student` = {frappe.db.escape(user)}"


def доступна_заметка(doc, ptype: str = "read", user: str | None = None) -> bool:
	"""Права на конкретную заметку. Удаляет ученик через `forget`, не напрямую."""
	user = user or frappe.session.user
	if not _только_чтение(ptype, user):
		return False
	return видит_всё(user) or doc.student == user


def организации_с_документами(user: str) -> list[str]:
	"""Организации, чьи документы пространства пользователь читает, кроме своих.

	Руководитель — всегда своей организации; участник — если организация
	открыла документы всем участникам. Только действующее членство: ушедший
	теряет доступ к чужим документам сразу, свои читает по авторству.
	"""
	from lms_frappe_app.agent_learning.constants import ДОКУМЕНТЫ_ВИДЯТ_ВСЕ

	руководит = организации_менеджера(user)
	состоит = frappe.get_all(
		"Organization Membership",
		filters={"user": user, "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ},
		pluck="organization",
	)
	открыты_всем = (
		frappe.get_all(
			"Learning Organization",
			filters={"name": ("in", состоит), "artifact_visibility": ДОКУМЕНТЫ_ВИДЯТ_ВСЕ},
			pluck="name",
		)
		if состоит
		else []
	)
	return sorted(set(руководит) | set(открыты_всем))


def условие_артефакта(user: str | None = None) -> str:
	"""`Agent Student Artifact`: свои плюс документы пространств, открытых пользователю.

	Доступ решает **пространство документа**, а не то, что руководитель и
	ученик где-то состоят вместе. `Why:` человек бывает в нескольких
	организациях и учится лично; руководитель компании X не должен видеть
	ни личный документ сотрудника, ни сделанный для компании Y
	(learning-services#341). Личный документ — пустое пространство — не
	читает никто, кроме автора.
	"""
	user = user or frappe.session.user
	if видит_всё(user):
		return ""
	свои = f"`tabAgent Student Artifact`.`student` = {frappe.db.escape(user)}"
	организации = организации_с_документами(user)
	if not организации:
		return свои
	return f"({свои} or `tabAgent Student Artifact`.`organization` in ({_список(организации)}))"


def доступен_артефакт(doc, ptype: str = "read", user: str | None = None) -> bool:
	"""Права на конкретный документ ученика. Пишется только через `update_artifact`."""
	user = user or frappe.session.user
	if not _только_чтение(ptype, user):
		return False
	if видит_всё(user) or doc.student == user:
		return True
	return bool(doc.organization) and doc.organization in организации_с_документами(user)


def доступен_desk(user: str | None = None) -> bool:
	"""Показывать ли приложение на стартовом экране desk.

	`Why:` хук `add_to_apps_screen` зовёт эту функцию для каждого вошедшего;
	без неё плитка приложения появлялась бы и у ученика, которому desk
	закрыт — ссылка вела бы в 403. Сотрудник платформы — тот, у кого есть
	административная роль; сама по себе роль `Organization Manager` desk не
	открывает, менеджер работает через агента.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(set(frappe.get_roles(user)) & АДМИНИСТРАТИВНЫЕ_РОЛИ)
