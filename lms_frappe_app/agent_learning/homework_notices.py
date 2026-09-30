# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Письма домашки: напоминание о сроке, «вернули на доработку» и дайджест
куратору (learning-services#452).

Всё — из одной ежечасной задачи, а не по событию. `Why:` без исходящей почты
письмо не ставится в очередь и не пишется в журнал, и следующий запуск
пробует снова; письмо по событию без почты пропало бы насовсем.

Однократность держит журнал `Homework Notice` с уникальным ключом:
`{сдача}:reminder:{срок}`, `{строка журнала}:returned`,
`{получатель}:digest:{дата}`. Новый срок сдачи — новый ключ напоминания.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.query_builder import Order
from frappe.utils import escape_html, format_datetime, get_url, now_datetime

from lms_frappe_app.agent_learning import homework as домашка
from lms_frappe_app.agent_learning import notices
from lms_frappe_app.agent_learning.access import курсы_ученика
from lms_frappe_app.agent_learning.constants import (
	ДОМАШКА_ВОЗВРАЩЕНА,
	ДОМАШКА_ВЫДАНА,
	ДОМАШКА_СДАНА,
	ЧЛЕНСТВО_ДЕЙСТВУЕТ,
)
from lms_frappe_app.agent_learning.permissions import МЕТОДИСТЫ, РОЛИ_МЕНЕДЖЕРА

ЖУРНАЛ = "Homework Notice"
НАПОМИНАНИЕ = "reminder"
ВЕРНУЛИ = "returned"
ДАЙДЖЕСТ = "digest"

#: Дайджест — первым запуском после этого часа по времени платформы.
ЧАС_ДАЙДЖЕСТА = 9
#: Возвраты старше этого письма не получают: письмо о давнем возврате — шум,
#: а журнал без предела пришлось бы перечитывать целиком каждый час.
ДНЕЙ_ВОЗВРАТА = 7
#: Адрес очереди «Ждут проверки» в SPA.
АДРЕС_ОЧЕРЕДИ = "/lms/homework?tab=queue"


def разослать() -> None:
	"""Ежечасная задача: напоминания, «вернули» и дайджест."""
	if not notices.почта_есть():
		return
	сейчас = now_datetime()
	получатели = _Получатели()
	письма = [*_напоминания(сейчас, получатели), *_возвраты(сейчас, получатели), *_дайджесты(сейчас)]
	записаны = _записанные([п["key"] for п in письма])
	for письмо in письма:
		if письмо["key"] not in записаны:
			_отправить(письмо)


# --- кому можно писать ---


class _Получатели:
	"""Проверки адресата с кешем на запуск: доступ к курсу стоит запросов."""

	def __init__(self):
		self._курсы: dict[str, set[str]] = {}
		self._адреса: dict[str, dict[str, str]] = {}

	def ученику_можно(self, сдача, курс: str | None) -> bool:
		"""Ученик активен, курс ему открыт, а у сдачи организации — он в ней состоит."""
		if not курс or not _включён(сдача.member):
			return False
		if сдача.member not in self._курсы:
			self._курсы[сдача.member] = {к["course"] for к in курсы_ученика(сдача.member)}
		if курс not in self._курсы[сдача.member]:
			return False
		return not сдача.organization or bool(
			frappe.db.exists(
				"Organization Membership",
				{"user": сдача.member, "organization": сдача.organization, "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ},
			)
		)

	def адрес(self, lesson: str, курс: str) -> str:
		"""Ссылка на блок домашки урока. Порядок уроков — раз на курс."""
		if курс not in self._адреса:
			self._адреса[курс] = домашка.адреса_уроков(курс)
		путь = self._адреса[курс].get(lesson) or f"/lms/courses/{курс}"
		return get_url(f"{путь}#homework")


def _включён(user: str) -> bool:
	return bool(frappe.get_cached_value("User", user, "enabled"))


# --- напоминание ---


def _напоминания(сейчас, получатели: _Получатели) -> list[dict]:
	"""Сдачи, от которых ждут действия, со сроком впереди — пора ли напомнить.

	Порог — `deadline_reminder_days`; если от начала до срока меньше, то
	середина промежутка: иначе письмо ушло бы сразу после выдачи. Начало —
	выдача, у сдачи до выдачи — сохранение.
	"""
	дней = notices.дней_напоминания()
	if not дней:
		return []
	сдачи = frappe.get_all(
		домашка.СДАЧА,
		filters={
			"status": ("in", [ДОМАШКА_ВЫДАНА, ДОМАШКА_ВОЗВРАЩЕНА]),
			"member": ("is", "set"),
			"due_at": (">", сейчас),
		},
		fields=[
			"name",
			"homework",
			"lesson",
			"member",
			"organization",
			"due_at",
			"assigned_at",
			"submitted_at",
			"creation",
		],
	)
	порог = timedelta(days=дней)
	пора = []
	for сдача in сдачи:
		начало = сдача.assigned_at or сдача.submitted_at or сдача.creation
		промежуток = сдача.due_at - начало
		if сейчас >= сдача.due_at - (порог if промежуток >= порог else промежуток / 2):
			пора.append(сдача)
	if not пора:
		return []
	уроки = домашка.уроки_с_курсом({с.lesson for с in пора})
	задания = _названия(домашка.ЗАДАНИЕ, {с.homework for с in пора})
	письма = []
	for сдача in пора:
		урок = уроки.get(сдача.lesson) or frappe._dict()
		if not получатели.ученику_можно(сдача, урок.course):
			continue
		задание = задания.get(сдача.homework) or ""
		курс = _название_курса(урок.course)
		письма.append(
			{
				"key": f"{сдача.name}:{НАПОМИНАНИЕ}:{сдача.due_at:%Y%m%d%H%M}",
				"kind": НАПОМИНАНИЕ,
				"recipient": сдача.member,
				"submission": сдача.name,
				"subject": f"Скоро срок домашнего задания «{задание}»",
				"message": (
					f"<p>Срок домашнего задания «{escape_html(задание)}» к уроку "
					f"«{escape_html(урок.title or '')}» курса «{escape_html(курс)}» — "
					f"{format_datetime(сдача.due_at, 'dd.MM.yyyy HH:mm')}.</p>"
					f'<p><a href="{получатели.адрес(сдача.lesson, урок.course)}">Открыть задание</a></p>'
				),
			}
		)
	return письма


# --- «вернули на доработку» ---


def _возвраты(сейчас, получатели: _Получатели) -> list[dict]:
	"""Возвраты и отмены приёма за последние дни у сдач, всё ещё на доработке.

	Пересдал до рассылки — письма нет: ученик уже доделал. Письмо — по
	последнему возврату сдачи: прежний, если письмо о нём не ушло (почты не
	было), перекрыт новым комментарием.
	"""
	событие = frappe.qb.DocType("Agent Homework Event")
	сдача = frappe.qb.DocType(домашка.СДАЧА)
	строки = (
		frappe.qb.from_(событие)
		.join(сдача)
		.on(сдача.name == событие.parent)
		.select(
			событие.name.as_("row"),
			событие.event,
			событие.comment,
			сдача.name,
			сдача.homework,
			сдача.lesson,
			сдача.member,
			сдача.organization,
		)
		.where(событие.parenttype == домашка.СДАЧА)
		.where(событие.parentfield == "history")
		.where(событие.event.isin(list(домашка.СОБЫТИЯ_С_КОММЕНТАРИЕМ)))
		.where(событие.at >= сейчас - timedelta(days=ДНЕЙ_ВОЗВРАТА))
		.where(сдача.status == ДОМАШКА_ВОЗВРАЩЕНА)
		.where(сдача.member.isnotnull())
		.orderby(событие.idx, order=Order.asc)
	).run(as_dict=True)
	последние = {строка.name: строка for строка in строки}
	if not последние:
		return []
	уроки = домашка.уроки_с_курсом({с.lesson for с in последние.values()})
	задания = _названия(домашка.ЗАДАНИЕ, {с.homework for с in последние.values()})
	письма = []
	for строка in последние.values():
		урок = уроки.get(строка.lesson) or frappe._dict()
		if not получатели.ученику_можно(строка, урок.course):
			continue
		задание = задания.get(строка.homework) or ""
		что = "отменили приём домашнего задания" if строка.event == "reopened" else "вернули домашнее задание"
		комментарий = escape_html(строка.comment or "").replace("\n", "<br>")
		письма.append(
			{
				"key": f"{строка.row}:{ВЕРНУЛИ}",
				"kind": ВЕРНУЛИ,
				"recipient": строка.member,
				"submission": строка.name,
				"subject": f"Домашнее задание «{задание}» — на доработку",
				"message": (
					f"<p>Вам {что} «{escape_html(задание)}» к уроку «{escape_html(урок.title or '')}» "
					f"курса «{escape_html(_название_курса(урок.course))}». Что доделать:</p>"
					f"<blockquote>{комментарий}</blockquote>"
					f'<p><a href="{получатели.адрес(строка.lesson, урок.course)}">Открыть задание</a></p>'
				),
			}
		)
	return письма


# --- дайджест куратору ---


def _дайджесты(сейчас) -> list[dict]:
	"""«N домашек ждут проверки» — раз в день, первым запуском после часа дайджеста.

	Руководителю — сдачи пространств его организаций, модератору — все,
	методисту — курсов, где он инструктор: очередь чужих курсов ему ни к чему.
	Свои сдачи куратору не считаются. Пустая очередь — письма нет.
	"""
	if сейчас.hour < ЧАС_ДАЙДЖЕСТА:
		return []
	ждут = frappe.get_all(
		домашка.СДАЧА,
		filters={"status": ДОМАШКА_СДАНА, "member": ("is", "set")},
		fields=["name", "lesson", "member", "organization"],
	)
	if not ждут:
		return []
	курсы = {имя: урок.course for имя, урок in домашка.уроки_с_курсом({с.lesson for с in ждут}).items()}
	роли: dict[str, set[str]] = {}
	for строка in frappe.get_all(
		"Has Role",
		filters={
			"parenttype": "User",
			"role": ("in", ["Organization Manager", *МЕТОДИСТЫ]),
		},
		fields=["parent", "role"],
	):
		роли.setdefault(строка.parent, set()).add(строка.role)
	if not роли:
		return []
	включены = set(frappe.get_all("User", filters={"name": ("in", list(роли)), "enabled": 1}, pluck="name"))
	руководит: dict[str, set[str]] = {}
	for строка in frappe.get_all(
		"Organization Membership",
		filters={
			"user": ("in", [у for у, р in роли.items() if "Organization Manager" in р] or [""]),
			"role": ("in", РОЛИ_МЕНЕДЖЕРА),
			"status": ЧЛЕНСТВО_ДЕЙСТВУЕТ,
		},
		fields=["user", "organization"],
	):
		руководит.setdefault(строка.user, set()).add(строка.organization)
	ведёт: dict[str, set[str]] = {}
	for строка in frappe.get_all(
		"Course Instructor",
		filters={
			"parenttype": "LMS Course",
			"instructor": ("in", [у for у, р in роли.items() if "Course Creator" in р] or [""]),
		},
		fields=["instructor", "parent"],
	):
		ведёт.setdefault(строка.instructor, set()).add(строка.parent)

	день = f"{сейчас:%Y%m%d}"
	письма = []
	for куратор in sorted(включены):
		свои_роли = роли[куратор]
		видит = [
			с
			for с in ждут
			if с.member != куратор
			and (
				"Moderator" in свои_роли
				or (с.organization and с.organization in руководит.get(куратор, set()))
				or курсы.get(с.lesson) in ведёт.get(куратор, set())
			)
		]
		if not видит:
			continue
		число = len(видит)
		письма.append(
			{
				"key": f"{куратор}:{ДАЙДЖЕСТ}:{день}",
				"kind": ДАЙДЖЕСТ,
				"recipient": куратор,
				"submission": None,
				"subject": f"{число} {_домашек(число)} {'ждёт' if _одна(число) else 'ждут'} проверки",
				"message": (
					f"<p>На проверке — {число} {_домашек(число)}: ученики сдали и ждут ответа.</p>"
					f'<p><a href="{get_url(АДРЕС_ОЧЕРЕДИ)}">Открыть очередь</a></p>'
				),
			}
		)
	return письма


def _одна(число: int) -> bool:
	return число % 10 == 1 and число % 100 != 11


def _домашек(число: int) -> str:
	"""«домашка», «домашки», «домашек» — по числу."""
	if _одна(число):
		return "домашка"
	if число % 10 in (2, 3, 4) and число % 100 not in (12, 13, 14):
		return "домашки"
	return "домашек"


# --- отправка ---


def _записанные(ключи: list[str]) -> set[str]:
	"""Ключи, письма по которым уже ушли, — одной выборкой."""
	if not ключи:
		return set()
	return set(frappe.get_all(ЖУРНАЛ, filters={"key": ("in", ключи)}, pluck="key"))


ТОЧКА_ПИСЬМА = "homework_notice"


def _отправить(письмо: dict) -> None:
	"""Запись в журнал и письмо — вместе или никак.

	Запись — до отправки, в той же транзакции: очередь писем Frappe отправляет
	только закоммиченное. Сбой одного письма откатывает только его и не валит
	запуск.
	"""
	frappe.db.savepoint(ТОЧКА_ПИСЬМА)
	try:
		frappe.get_doc(
			{
				"doctype": ЖУРНАЛ,
				"key": письмо["key"],
				"kind": письмо["kind"],
				"recipient": письмо["recipient"],
				"submission": письмо["submission"],
			}
		).insert(ignore_permissions=True)
		frappe.sendmail(
			recipients=[письмо["recipient"]],
			subject=письмо["subject"],
			message=письмо["message"],
			reference_doctype=домашка.СДАЧА if письмо["submission"] else None,
			reference_name=письмо["submission"],
		)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		# Параллельный запуск успел раньше: письмо уже в очереди.
		frappe.db.rollback(save_point=ТОЧКА_ПИСЬМА)
		frappe.clear_last_message()
	except Exception:
		frappe.db.rollback(save_point=ТОЧКА_ПИСЬМА)
		frappe.log_error(title="Письмо домашки не отправлено", reference_doctype=ЖУРНАЛ)
	else:
		frappe.db.release_savepoint(ТОЧКА_ПИСЬМА)


def _названия(doctype: str, имена) -> dict[str, str]:
	имена = [и for и in имена if и]
	if not имена:
		return {}
	return dict(
		frappe.get_all(doctype, filters={"name": ("in", имена)}, fields=["name", "title"], as_list=True)
	)


def _название_курса(курс: str | None) -> str:
	return (frappe.get_cached_value("LMS Course", курс, "title") if курс else None) or курс or ""
