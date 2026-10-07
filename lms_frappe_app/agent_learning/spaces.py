# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Пространства ученика: личное и организаций, в которых он состоит.

Прогресс у человека один, документы — у пространства (learning-services#341).
Здесь — какие пространства у ученика есть, какое выбрано сейчас, какие курсы
в каждом и в каком пространстве идёт работа по курсу, если его не назвали.

Выбранное пространство хранится на сервере, а не в браузере: по нему же
работают агент и веб-чат, которые переключателя не видят.
"""

from __future__ import annotations

import frappe
from frappe.utils import getdate

from lms_frappe_app.agent_learning import announcements
from lms_frappe_app.agent_learning.access import (
	КУРС_ГОТОВИТСЯ,
	КУРС_НЕ_ОПУБЛИКОВАН,
	КУРС_НЕ_ОТКРЫТ,
	УЖЕ_ЗАПИСАН,
	курсы_ученика,
	назначения_ученика,
	приостановленные_организации,
)
from lms_frappe_app.agent_learning.constants import (
	ДОКУМЕНТЫ_ВИДЯТ_ВСЕ,
	ДОКУМЕНТЫ_ВИДЯТ_РУКОВОДИТЕЛИ,
	ОТКРЫТЫЕ,
	ЧЛЕНСТВО_ДЕЙСТВУЕТ,
)
from lms_frappe_app.agent_learning.errors import Отказ

#: Значение параметра `space` для личного пространства. Организация
#: называется своим именем записи.
ЛИЧНОЕ = "personal"

#: Ключ пользовательской настройки Frappe, в которой лежит выбор.
КЛЮЧ_ВЫБОРА = "agent_learning_space"

ПРОСТРАНСТВО_НЕДОСТУПНО = "space_not_available"

#: Настройка видимости организации — словом контракта.
ВИДНО_КОМУ = {ДОКУМЕНТЫ_ВИДЯТ_РУКОВОДИТЕЛИ: "managers", ДОКУМЕНТЫ_ВИДЯТ_ВСЕ: "members"}
КУРС_НЕ_В_ПРОСТРАНСТВЕ = "course_not_in_space"


def пространства(user: str) -> list[dict]:
	"""Организации, в которых человек состоит сейчас, — в порядке вступления."""
	членства = frappe.get_all(
		"Organization Membership",
		filters={"user": user, "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ},
		fields=["organization", "role"],
		order_by="creation asc",
	)
	if not членства:
		return []
	организации = {
		о.name: о
		for о in frappe.get_all(
			"Learning Organization",
			filters={"name": ("in", [ч.organization for ч in членства])},
			fields=["name", "organization_name", "artifact_visibility"],
		)
	}
	приостановлены = приостановленные_организации(ч.organization for ч in членства)
	return [
		{
			"id": ч.organization,
			"title": организации[ч.organization].organization_name,
			"role": ч.role,
			"suspended": ч.organization in приостановлены,
			# Кто, кроме автора, читает документы этого пространства: интерфейс
			# говорит это ученику прямо на документе (learning-services#347).
			"documents_visible_to": ВИДНО_КОМУ[организации[ч.organization].artifact_visibility],
		}
		for ч in членства
	]


def текущее(user: str, свои: list[dict] | None = None) -> str | None:
	"""Выбранное пространство: организация или `None` — личное.

	Не выбирал — первая действующая организация, иначе личное: сотрудник
	компании приходит учиться прежде всего по её курсам (решение владельца,
	learning-services#135). Выбор, который больше не действует — вышел из
	организации, её приостановили, — уступает тому же правилу.
	"""
	свои = пространства(user) if свои is None else свои
	доступные = [п["id"] for п in свои if not п["suspended"]]
	выбор = frappe.defaults.get_user_default(КЛЮЧ_ВЫБОРА, user=user)
	if выбор == ЛИЧНОЕ:
		return None
	if выбор in доступные:
		return выбор
	return доступные[0] if доступные else None


def выбрать(user: str, space: str) -> str | None:
	"""Запоминает выбор; возвращает выбранную организацию или `None` — личное."""
	организация = проверить(user, space)
	frappe.defaults.set_user_default(КЛЮЧ_ВЫБОРА, организация or ЛИЧНОЕ, user=user)
	return организация


def проверить(user: str, space: str | None) -> str | None:
	"""Значение параметра `space` → организация или `None`; чужое — отказ.

	Пустое значение — это не «личное», а «не названо»: его разрешает
	`пространство_курса`. Личное называют явно — `personal`.
	"""
	if space == ЛИЧНОЕ:
		return None
	доступные = {п["id"] for п in пространства(user) if not п["suspended"]}
	if space not in доступные:
		raise Отказ(ПРОСТРАНСТВО_НЕДОСТУПНО, "Такого пространства у вас нет", space=space)
	return space


def наружу(организация: str | None) -> str:
	"""Пространство для ответа контракта: имя организации или `personal`."""
	return организация or ЛИЧНОЕ


def курсы(
	user: str, организация: str | None, назначения: list | None = None, *, с_архивом: bool = False
) -> list[dict]:
	"""Курсы пространства с условиями поверх.

	Личное — все курсы человека: прогресс у него один, и курс, назначенный
	компанией, он вправе пройти и для себя (решение владельца,
	learning-services#346). Организация — курсы, которые она назначила ему
	или которые он выбрал в её каталоге; дедлайн и обязательность — её.
	`с_архивом` — и курсы в архиве: в каком пространстве лежит работа по ним.
	"""
	if назначения is None:
		назначения = назначения_ученика(user)
	все = курсы_ученика(user, назначения, с_архивом=с_архивом)
	if организация is None:
		return все
	свои = {н.course: н for н in назначения if н.organization == организация}
	итог = []
	for запись in все:
		назначение = свои.get(запись["course"])
		if назначение is None:
			continue
		итог.append(
			{
				**запись,
				"organization": организация,
				"deadline": назначение.deadline,
				"mandatory": bool(назначение.mandatory),
				"overdue": bool(назначение.deadline and getdate(назначение.deadline) < getdate()),
			}
		)
	return итог


def в_пространстве(user: str, course: str, организация: str | None, *, с_архивом: bool = False) -> bool:
	return any(запись["course"] == course for запись in курсы(user, организация, с_архивом=с_архивом))


def пространство_курса(user: str, course: str, space: str | None = None) -> str | None:
	"""В каком пространстве идёт работа по курсу: организация или `None` — личное.

	Названо — оно, если курс в нём есть. Не названо — пространство открытого
	занятия по курсу: агент, начавший урок в компании, пишет документ туда же,
	даже не повторяя `space`. Занятия нет — выбранное пространство, если курс
	в нём есть, иначе личное: там все курсы человека.
	"""
	if space:
		организация = проверить(user, space)
		# С архивом: документы по курсу в архиве читаются там же, где написаны
		# (learning-services#500); занятия по нему закрывает доступ к курсу.
		if not в_пространстве(user, course, организация, с_архивом=True):
			raise Отказ(
				КУРС_НЕ_В_ПРОСТРАНСТВЕ,
				"Этого курса нет в выбранном пространстве",
				course=course,
				space=наружу(организация),
			)
		return организация

	открытое = frappe.get_all(
		"Agent Learning Session",
		filters={
			"student": user,
			"course": course,
			"status": ("in", ОТКРЫТЫЕ),
		},
		fields=["organization"],
		order_by="last_activity_at desc",
		limit=1,
	)
	if открытое:
		return открытое[0].organization or None

	выбранное = текущее(user)
	if выбранное and в_пространстве(user, course, выбранное, с_архивом=True):
		return выбранное
	return None


def каталог(user: str, организация: str | None) -> list[dict]:
	"""Опубликованные курсы, на которые можно записаться в этом пространстве,
	и анонсы — курсы, которые готовятся.

	Личное — весь каталог без курсов, на которые человек уже записан. Сотрудник
	учится и для себя, а за свой счёт каталог не сужается (learning-services#346).
	Организация — курсы, которые она открыла (пустой список — весь каталог),
	кроме тех, что уже в её пространстве: курс, пройденный лично, можно взять и
	в компанию — прогресс общий, документ будет её.
	"""
	if организация is None:
		занято = set(frappe.get_all("LMS Enrollment", filters={"member": user}, pluck="course"))
	else:
		занято = {запись["course"] for запись in курсы(user, организация)}
	открытые = _открытые(организация)
	доступные = [
		курс
		for курс in frappe.get_all(
			"LMS Course",
			filters={"published": 1},
			fields=["name", "title", "short_introduction", "upcoming"],
		)
		if курс.name not in занято and (открытые is None or курс.name in открытые)
	]
	подписки = announcements.подписки(user, [курс.name for курс in доступные if курс.upcoming])
	return [_строка_каталога(курс, подписки) for курс in доступные]


def _строка_каталога(курс, подписки: set[str]) -> dict:
	"""Курс каталога. У анонса — цели курса и подписан ли человек на выход.

	Анонс в каталоге остаётся: агент рассказывает, о чём будет курс, и
	предлагает сообщить о выходе вместо записи (learning-services#389).
	"""
	строка = {
		"id": курс.name,
		"title": курс.title,
		"summary": курс.short_introduction,
		"upcoming": bool(курс.upcoming),
	}
	if курс.upcoming:
		строка["objectives"] = announcements.цели_курса(курс.name)
		строка["notify"] = курс.name in подписки
	return строка


def можно_записаться(user: str, course: str, организация: str | None) -> tuple[bool, str | None]:
	"""Проверка перед записью в пространстве; при отказе — машинный код причины."""
	if not frappe.db.get_value("LMS Course", course, "published"):
		return False, КУРС_НЕ_ОПУБЛИКОВАН
	if announcements.анонсирован(course):
		return False, КУРС_ГОТОВИТСЯ
	if организация is None:
		if frappe.db.exists("LMS Enrollment", {"member": user, "course": course}):
			return False, УЖЕ_ЗАПИСАН
		return True, None
	открытые = _открытые(организация)
	if открытые is not None and course not in открытые:
		return False, КУРС_НЕ_ОТКРЫТ
	if в_пространстве(user, course, организация):
		return False, УЖЕ_ЗАПИСАН
	return True, None


def записать(user: str, course: str, организация: str | None) -> None:
	"""Записывает на курс в пространстве.

	В организации запись — назначение этой организации ученику по его выбору:
	курс появляется в её пространстве и в её отчёте, а обучение идёт за её
	счёт. Зачисление порождает само назначение, как у любого другого.
	"""
	if организация is None:
		frappe.get_doc(
			{"doctype": "LMS Enrollment", "member": user, "course": course, "member_type": "Student"}
		).insert(ignore_permissions=True)
		return
	frappe.get_doc(
		{
			"doctype": "Course Allocation",
			"organization": организация,
			"course": course,
			"audience": "Selected Members",
			"members": [{"user": user}],
			"chosen_by_member": 1,
		}
	).insert(ignore_permissions=True)


def _открытые(организация: str | None) -> set[str] | None:
	"""Курсы, открытые организации; `None` — ограничений нет.

	Пустой список разрешённых курсов означает «весь каталог» — то же правило,
	что при назначении (`LearningOrganization.разрешает_курс`).
	"""
	if организация is None:
		return None
	строки = frappe.get_all("Organization Course", filters={"parent": организация}, pluck="course")
	return set(строки) or None
