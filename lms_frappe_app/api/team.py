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

from lms_frappe_app.agent_learning.artifacts import export
from lms_frappe_app.agent_learning.artifacts.course import _действующая_схема, _схемы_курса
from lms_frappe_app.agent_learning.artifacts.document import (
	_блок,
	_вложения,
	_данные,
	_заполнен,
	_содержимое,
	_файлы,
)
from lms_frappe_app.agent_learning.constants import ЧЛЕНСТВО_ДЕЙСТВУЕТ
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.permissions import доступ_к_команде
from lms_frappe_app.api import контракт, текущий_пользователь

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
	_, доступ = _требовать_доступ(organization)
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
		таблицы = export.таблицы_документа(схема.blocks, данные)
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
