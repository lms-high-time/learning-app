# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""«Команда»: участники организации и документы, которые они собирают в её
пространстве (learning-services#355).

Доступ — тот же, что у самих документов пространства: руководители видят
всегда, участники — если организация открыла документы всем. Страница —
окно в документы, а не второй доступ к ним. Разговоры с наставником, заметки
агента и личные документы сюда не выходят ни при какой настройке.

Проверка — явная в каждом методе: `frappe.get_all` права не применяет.
"""

import frappe

from lms_frappe_app.agent_learning import artifact_tables
from lms_frappe_app.agent_learning.constants import ЧЛЕНСТВО_ДЕЙСТВУЕТ
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.permissions import доступ_к_команде
from lms_frappe_app.api import контракт, текущий_пользователь
from lms_frappe_app.api.student import (
	_блок,
	_вложения,
	_данные,
	_действующая_схема,
	_заполнен,
	_содержимое,
	_схемы_курса,
	_файлы,
)

КОМАНДА_НЕДОСТУПНА = "team_not_available"
КУРС_НЕ_ОРГАНИЗАЦИИ = "course_not_in_organization"


def _требовать_доступ(organization: str) -> tuple[str, str]:
	зритель = текущий_пользователь()
	доступ = доступ_к_команде(зритель, organization)
	if not доступ:
		raise Отказ(КОМАНДА_НЕДОСТУПНА, "Команда этой организации вам не открыта", organization=organization)
	return зритель, доступ


def _курсы_организации(organization: str) -> list[str]:
	"""Курсы, которые организация назначала, — в порядке первого назначения."""
	return list(
		dict.fromkeys(
			frappe.get_all(
				"Course Allocation",
				filters={"organization": organization},
				pluck="course",
				order_by="creation asc",
			)
		)
	)


def _участники(organization: str) -> list[dict]:
	"""Участники организации, ушедшие — с датой выхода, в конце."""
	членства = frappe.get_all(
		"Organization Membership",
		filters={"organization": organization},
		fields=["user", "role", "status", "left_on"],
		order_by="creation asc",
	)
	имена = dict(
		frappe.get_all(
			"User",
			filters={"name": ("in", [ч.user for ч in членства] or [""])},
			fields=["name", "full_name"],
			as_list=True,
		)
	)
	return sorted(
		(
			{
				"user": ч.user,
				"full_name": имена.get(ч.user),
				"role": ч.role,
				"left": ч.status != ЧЛЕНСТВО_ДЕЙСТВУЕТ,
				"left_on": str(ч.left_on) if ч.left_on else None,
			}
			for ч in членства
		),
		key=lambda у: у["left"],
	)


@frappe.whitelist()
@контракт
def team(organization: str) -> dict:
	"""Участники организации и курсы с документами, которые собирают в её пространстве."""
	зритель, доступ = _требовать_доступ(organization)
	курсы = _курсы_организации(organization)
	названия = dict(
		frappe.get_all(
			"LMS Course", filters={"name": ("in", курсы or [""])}, fields=["name", "title"], as_list=True
		)
	)
	return {
		"organization": organization,
		"title": frappe.db.get_value("Learning Organization", organization, "organization_name"),
		# Отчёт — только руководителю: прогресс людей — не то, что участники
		# открывают друг другу вместе с документами.
		"can_see_report": доступ == "manager",
		# Приглашать и отмечать уход — руководителю, менять роли — администратору
		# организации (#363).
		"can_manage": доступ == "manager",
		"can_change_roles": доступ == "manager" and _админ_ли(зритель, organization),
		"members": _участники(organization),
		"courses": [
			{
				"id": курс,
				"title": названия.get(курс),
				"documents": [{"artifact": с.slug, "title": с.title} for с in _схемы_курса(курс)],
			}
			for курс in курсы
		],
	}


@frappe.whitelist()
@контракт
def team_documents(organization: str, course: str, artifact: str) -> dict:
	"""Документ курса у каждого участника в пространстве организации — по блокам.

	Сравнение идёт блок за блоком: что написал каждый в «Спонсоре проекта»,
	а не пять документов целиком. У ушедшего документ остаётся — с пометкой.
	Табличный блок приходит markdown-таблицей документа: страница показывает
	её, не зная устройства таблиц.
	"""
	_требовать_доступ(organization)
	if course not in _курсы_организации(organization):
		raise Отказ(КУРС_НЕ_ОРГАНИЗАЦИИ, "Этот курс организация не назначала", course=course)
	схема = _действующая_схема(course, artifact)
	участники = {у["user"]: у for у in _участники(organization)}
	экземпляры = frappe.get_all(
		"Agent Student Artifact",
		filters={
			"course": course,
			"artifact": схема.slug,
			"organization": organization,
			"student": ("in", list(участники) or [""]),
		},
		pluck="name",
	)

	блоки = {блок.block_key: {"key": блок.block_key, "title": блок.title, "entries": []} for блок in схема.blocks}
	авторы = []
	for имя in экземпляры:
		экземпляр = frappe.get_doc("Agent Student Artifact", имя)
		участник = участники[экземпляр.student]
		содержимое, вложения, данные = _содержимое(экземпляр), _вложения(экземпляр), _данные(экземпляр)
		файлы = _файлы(вложения)
		таблицы = artifact_tables.таблицы_документа(схема.blocks, данные)
		заполнено = 0
		for блок in схема.blocks:
			описание = _блок(блок, содержимое, вложения, файлы, схема, данные)
			заполнен = _заполнен(описание)
			заполнено += заполнен
			блоки[блок.block_key]["entries"].append(
				{
					"user": участник["user"],
					"full_name": участник["full_name"],
					"left": участник["left"],
					"filled": заполнен,
					"content": описание["content"],
					"file": описание["file"],
					"url": описание["url"],
					"table_markdown": таблицы[описание["table"]]["markdown"]
					if описание.get("table")
					else None,
				}
			)
		авторы.append(
			{
				"user": участник["user"],
				"full_name": участник["full_name"],
				"left": участник["left"],
				"blocks_filled": заполнено,
				"blocks_total": len(схема.blocks),
				"modified": экземпляр.modified.isoformat(),
			}
		)
	return {
		"organization": organization,
		"course": course,
		"artifact": схема.slug,
		"title": схема.title,
		"authors": авторы,
		"blocks": list(блоки.values()),
	}


# --- сотрудники: приглашение ссылкой, роли, уход (learning-services#363) ---

ПРИГЛАШЕНИЕ_НЕ_НАЙДЕНО = "invite_not_found"
НЕТ_ПРАВА = "not_allowed"
ПОСЛЕДНИЙ_АДМИН = "last_org_admin"
НЕ_УЧАСТНИК = "not_a_member"
АДМИН = "Org Admin"
РОЛИ_УЧАСТИЯ = ("Member", "Manager", АДМИН)


def _руководитель(organization: str) -> str:
	"""Руководитель организации или сотрудник платформы; иначе — отказ."""
	зритель, доступ = _требовать_доступ(organization)
	if доступ != "manager":
		raise Отказ(НЕТ_ПРАВА, "Управлять командой может руководитель", organization=organization)
	return зритель


def _администратор(organization: str) -> str:
	"""`Org Admin` организации или сотрудник платформы; иначе — отказ.

	`Why:` роль руководителя открывает документы и отчёт организации: раздавать
	её — решение администратора, а не любого руководителя.
	"""
	зритель = _руководитель(organization)
	if _админ_ли(зритель, organization):
		return зритель
	raise Отказ(НЕТ_ПРАВА, "Менять роли может администратор организации", organization=organization)


def _админ_ли(user: str, organization: str) -> bool:
	from lms_frappe_app.agent_learning.permissions import видит_всё

	return видит_всё(user) or bool(
		frappe.db.exists(
			"Organization Membership",
			{"user": user, "organization": organization, "role": АДМИН, "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ},
		)
	)


def _ссылка(токен: str) -> str:
	return frappe.utils.get_url(f"/lms/join/{токен}")


def _приглашение(token: str):
	имя = frappe.db.get_value("Organization Invite", {"token": token or "", "revoked": 0})
	if not имя:
		raise Отказ(ПРИГЛАШЕНИЕ_НЕ_НАЙДЕНО, "Ссылка недействительна или отозвана")
	return frappe.get_doc("Organization Invite", имя)


def _членство(organization: str, user: str):
	имя = frappe.db.get_value("Organization Membership", {"user": user, "organization": organization})
	if not имя:
		raise Отказ(НЕ_УЧАСТНИК, "Этот человек не состоит в организации", user=user)
	return frappe.get_doc("Organization Membership", имя)


def _останется_админ(organization: str, кроме: str) -> bool:
	return bool(
		frappe.db.exists(
			"Organization Membership",
			{
				"organization": organization,
				"role": АДМИН,
				"status": ЧЛЕНСТВО_ДЕЙСТВУЕТ,
				"user": ("!=", кроме),
			},
		)
	)


@frappe.whitelist(methods=["POST"])
@контракт
def create_invite(organization: str) -> dict:
	"""Ссылка, по которой вступают в организацию участником."""
	зритель = _руководитель(organization)
	приглашение = frappe.get_doc(
		{"doctype": "Organization Invite", "organization": organization, "created_by": зритель}
	).insert(ignore_permissions=True)
	return {"token": приглашение.token, "url": _ссылка(приглашение.token)}


@frappe.whitelist()
@контракт
def invites(organization: str) -> dict:
	"""Действующие ссылки организации — чтобы отозвать ненужную."""
	_руководитель(organization)
	return {
		"invites": [
			{
				"token": з.token,
				"url": _ссылка(з.token),
				"created_by": з.created_by,
				"created": з.creation.isoformat(),
			}
			for з in frappe.get_all(
				"Organization Invite",
				filters={"organization": organization, "revoked": 0},
				fields=["token", "created_by", "creation"],
				order_by="creation desc",
			)
		]
	}


@frappe.whitelist(methods=["POST"])
@контракт
def revoke_invite(token: str) -> dict:
	"""Отзывает ссылку: по ней больше не вступить; вступившие остаются."""
	приглашение = _приглашение(token)
	_руководитель(приглашение.organization)
	приглашение.db_set("revoked", 1)
	return {"token": token, "revoked": True}


@frappe.whitelist(allow_guest=True)
@контракт
def invite_info(token: str) -> dict:
	"""Куда ведёт ссылка: организация и кто увидит документы.

	Гостю тоже — страница вступления показывает, куда зовут, до входа. Ключ —
	секрет ссылки, и название организации знает только тот, кому её дали.
	"""
	from lms_frappe_app.agent_learning.spaces import ВИДНО_КОМУ

	приглашение = _приглашение(token)
	организация = frappe.db.get_value(
		"Learning Organization",
		приглашение.organization,
		["organization_name", "artifact_visibility", "status"],
		as_dict=True,
	)
	пользователь = frappe.session.user
	состоит = пользователь != "Guest" and frappe.db.exists(
		"Organization Membership",
		{"user": пользователь, "organization": приглашение.organization, "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ},
	)
	return {
		"organization": приглашение.organization,
		"title": организация.organization_name,
		"documents_visible_to": ВИДНО_КОМУ[организация.artifact_visibility],
		"suspended": организация.status != "Active",
		"member": bool(состоит),
	}


@frappe.whitelist(methods=["POST"])
@контракт
def accept_invite(token: str) -> dict:
	"""Вступает в организацию по ссылке и выбирает её пространство.

	Ушедший раньше возвращается: членство снова действующее, курсы,
	назначенные без него, догоняют его сами (#341).
	"""
	from lms_frappe_app.agent_learning import spaces

	ученик = текущий_пользователь()
	приглашение = _приглашение(token)
	организация = приглашение.organization
	if frappe.db.get_value("Learning Organization", организация, "status") != "Active":
		raise Отказ(ПРИГЛАШЕНИЕ_НЕ_НАЙДЕНО, "Организация сейчас приостановлена")
	имя = frappe.db.get_value("Organization Membership", {"user": ученик, "organization": организация})
	if имя:
		членство = frappe.get_doc("Organization Membership", имя)
		if членство.status != ЧЛЕНСТВО_ДЕЙСТВУЕТ:
			членство.status = ЧЛЕНСТВО_ДЕЙСТВУЕТ
			членство.save(ignore_permissions=True)
	else:
		frappe.get_doc(
			{
				"doctype": "Organization Membership",
				"user": ученик,
				"organization": организация,
				"role": "Member",
			}
		).insert(ignore_permissions=True)
	spaces.выбрать(ученик, организация)
	return {"organization": организация, "space": организация}


@frappe.whitelist(methods=["POST"])
@контракт
def set_member_role(organization: str, user: str, role: str) -> dict:
	"""Меняет роль участника; только администратор организации."""
	_администратор(organization)
	if role not in РОЛИ_УЧАСТИЯ:
		raise Отказ(НЕТ_ПРАВА, "Такой роли нет", role=role)
	членство = _членство(organization, user)
	if членство.role == АДМИН and role != АДМИН and not _останется_админ(organization, user):
		raise Отказ(ПОСЛЕДНИЙ_АДМИН, "У организации должен остаться администратор", user=user)
	членство.role = role
	членство.save(ignore_permissions=True)
	return {"user": user, "role": role}


@frappe.whitelist(methods=["POST"])
@контракт
def remove_member(organization: str, user: str) -> dict:
	"""Отмечает уход: членство закрывается, документы остаются у организации.

	Руководитель отмечает уход участников; руководителей и администраторов —
	только администратор: иначе руководитель снимал бы коллег с их прав.
	"""
	зритель = _руководитель(organization)
	членство = _членство(organization, user)
	if членство.role != "Member" and user != зритель:
		_администратор(organization)
	if членство.role == АДМИН and not _останется_админ(organization, user):
		raise Отказ(ПОСЛЕДНИЙ_АДМИН, "У организации должен остаться администратор", user=user)
	if членство.status != "Left":
		членство.status = "Left"
		членство.save(ignore_permissions=True)
	return {"user": user, "left": True}
