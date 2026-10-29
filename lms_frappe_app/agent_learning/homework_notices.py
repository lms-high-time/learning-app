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

Тема и текст — на языке получателя, срок — по нему же (learning-services#768):
рассылку зовёт задача, и язык запроса тут ничей.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _
from frappe.query_builder import Order
from frappe.utils import escape_html, get_url, now_datetime

from lms_frappe_app import languages
from lms_frappe_app.agent_learning import course_translations as переводы_курса
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

РУКОВОДИТЕЛЬ = "Organization Manager"

ЖУРНАЛ = "Homework Notice"
НАПОМИНАНИЕ = "reminder"
ВЕРНУЛИ = "returned"
ДАЙДЖЕСТ = "digest"

#: Дайджест — первым запуском после этого часа по времени платформы, в котором
#: у куратора есть ожидающие.
ЧАС_ДАЙДЖЕСТА = 9
#: Возвраты старше этого письма не получают: письмо о давнем возврате — шум,
#: а журнал без предела пришлось бы перечитывать целиком каждый час.
ДНЕЙ_ВОЗВРАТА = 7
#: Адрес очереди «Ждут проверки» в SPA.
АДРЕС_ОЧЕРЕДИ = "/lms/homework?tab=queue"


def разослать() -> None:
	"""Ежечасная задача: напоминания, «вернули» и дайджест.

	Сбой одного вида писем не останавливает остальные, сбой одного письма —
	остальные письма того же вида. `Why:` одна битая сдача иначе молча
	оставляла бы без писем всю платформу — час за часом.
	"""
	if not notices.почта_есть():
		return
	сейчас = now_datetime()
	получатели = _Получатели()
	for сборщик in (_напоминания, _возвраты, _дайджесты):
		try:
			письма = сборщик(сейчас, получатели)
		except Exception:
			_в_лог("Письма домашки не собраны")
			continue
		for письмо in письма:
			_отправить(письмо)


def _по_одному(записи, построить) -> list[dict]:
	"""Письма по записям; запись, на которой сборка упала, — в лог, остальные дальше."""
	письма = []
	for запись in записи:
		try:
			if письмо := построить(запись):
				письма.append(письмо)
		except Exception:
			_в_лог("Письмо домашки не собрано")
	return письма


# --- кому можно писать ---


class _Получатели:
	"""Проверки адресата с кешем на запуск: доступ к курсу стоит запросов."""

	def __init__(self):
		self._курсы: dict[str, set[str]] = {}
		self._членства: dict[tuple[str, str], bool] = {}
		self._адреса: dict[str, dict[str, str]] = {}
		self._языки: dict[str, str] = {}

	def язык(self, user: str) -> str:
		"""Язык письма — получателя; раз на получателя за запуск."""
		if user not in self._языки:
			self._языки[user] = languages.язык_пользователя(user)
		return self._языки[user]

	def ученику_можно(self, сдача, курс: str | None) -> bool:
		"""Ученик активен, курс ему открыт, а у сдачи организации — он в ней состоит."""
		if not курс or not _включён(сдача.member):
			return False
		if сдача.member not in self._курсы:
			self._курсы[сдача.member] = {к["course"] for к in курсы_ученика(сдача.member)}
		if курс not in self._курсы[сдача.member]:
			return False
		if not сдача.organization:
			return True
		пара = (сдача.member, сдача.organization)
		if пара not in self._членства:
			self._членства[пара] = bool(
				frappe.db.exists(
					"Organization Membership",
					{"user": сдача.member, "organization": сдача.organization, "status": ЧЛЕНСТВО_ДЕЙСТВУЕТ},
				)
			)
		return self._членства[пара]

	def адрес(self, lesson: str, курс: str) -> str:
		"""Ссылка на блок домашки урока. Порядок уроков — раз на курс."""
		if курс not in self._адреса:
			self._адреса[курс] = домашка.адреса_уроков(курс)
		путь = self._адреса[курс].get(lesson) or f"/lms/courses/{курс}"
		return get_url(f"{путь}#homework")


def _в_лог(заголовок: str) -> None:
	"""Сбой — в лог и сразу в базу.

	`Why:` лог пишется в транзакции рассылки, и полный откат после сбоя
	следующего письма (`_откатить_письмо`) снял бы и его — сбой пропал бы
	без следа.
	"""
	frappe.log_error(title=заголовок, reference_doctype=ЖУРНАЛ)
	frappe.db.commit()


def _включён(user: str) -> bool:
	return bool(frappe.get_cached_value("User", user, "enabled"))


def _новые(записи: list, ключ) -> list:
	"""Записи, писем по которым ещё не было, — до проверок адресата: те стоят запросов."""
	записаны = _записанные([ключ(з) for з in записи])
	return [з for з in записи if ключ(з) not in записаны]


# --- напоминание ---


def _ключ_напоминания(сдача) -> str:
	return f"{сдача.name}:{НАПОМИНАНИЕ}:{сдача.due_at:%Y%m%d%H%M}"


def _напоминания(сейчас, получатели: _Получатели) -> list[dict]:
	"""Сдачи, от которых ждут действия, со сроком впереди — пора ли напомнить.

	Порог — `deadline_reminder_days`; если от начала до срока меньше, то
	середина промежутка: иначе письмо ушло бы сразу после выдачи. Начало —
	выдача, у сдачи до выдачи — сохранение.
	"""
	дней = notices.дней_напоминания()
	if not дней:
		return []
	порог = timedelta(days=дней)
	# Why: срок дальше порога напоминания не ждёт ни при каком промежутке —
	# выборка каждый час не тянет все будущие сроки платформы.
	сдачи = frappe.get_all(
		домашка.СДАЧА,
		filters=[
			["status", "in", [ДОМАШКА_ВЫДАНА, ДОМАШКА_ВОЗВРАЩЕНА]],
			["member", "is", "set"],
			["due_at", ">", сейчас],
			["due_at", "<=", сейчас + порог],
		],
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
	пора = []
	for сдача in сдачи:
		начало = сдача.assigned_at or сдача.submitted_at or сдача.creation
		промежуток = сдача.due_at - начало
		if сейчас >= сдача.due_at - (порог if промежуток >= порог else промежуток / 2):
			пора.append(сдача)
	пора = _новые(пора, _ключ_напоминания)
	if not пора:
		return []
	уроки = домашка.уроки_с_курсом({с.lesson for с in пора})
	задания = домашка.названия(домашка.ЗАДАНИЕ, {с.homework for с in пора})

	def построить(сдача) -> dict | None:
		урок = уроки.get(сдача.lesson) or frappe._dict()
		if not получатели.ученику_можно(сдача, урок.course):
			return None
		задание = задания.get(сдача.homework) or ""
		язык = получатели.язык(сдача.member)
		курс = _название_курса(урок.course, язык)
		return {
			"key": _ключ_напоминания(сдача),
			"kind": НАПОМИНАНИЕ,
			"recipient": сдача.member,
			"submission": сдача.name,
			"subject": _("The homework “{0}” is due soon", lang=язык).format(задание),
			"message": notices.абзацы(
				_(
					"The homework “{0}” for the lesson “{1}” in the course “{2}” is due {3}.", lang=язык
				).format(
					escape_html(задание),
					escape_html(урок.title or ""),
					escape_html(курс),
					escape_html(languages.дата_время(сдача.due_at, язык)),
				),
				notices.ссылка(
					получатели.адрес(сдача.lesson, урок.course), _("Open the homework", lang=язык)
				),
			),
		}

	return _по_одному(пора, построить)


# --- «вернули на доработку» ---


def _ключ_возврата(строка) -> str:
	return f"{строка.row}:{ВЕРНУЛИ}"


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
		.where(сдача.member.isnotnull() & (сдача.member != ""))
		.orderby(событие.idx, order=Order.asc)
	).run(as_dict=True)
	последние = _новые(list({строка.name: строка for строка in строки}.values()), _ключ_возврата)
	if not последние:
		return []
	уроки = домашка.уроки_с_курсом({с.lesson for с in последние})
	задания = домашка.названия(домашка.ЗАДАНИЕ, {с.homework for с in последние})

	def построить(строка) -> dict | None:
		урок = уроки.get(строка.lesson) or frappe._dict()
		if not получатели.ученику_можно(строка, урок.course):
			return None
		задание = задания.get(строка.homework) or ""
		язык = получатели.язык(строка.member)
		названия = (
			escape_html(задание),
			escape_html(урок.title or ""),
			escape_html(_название_курса(урок.course, язык)),
		)
		# Отмена приёма и возврат — разные предложения целиком, а не вставка в одно.
		что_случилось = (
			_(
				"Acceptance of your homework “{0}” for the lesson “{1}” in the course “{2}” has been revoked. What to redo:",
				lang=язык,
			)
			if строка.event == "reopened"
			else _(
				"Your homework “{0}” for the lesson “{1}” in the course “{2}” has been returned. What to redo:",
				lang=язык,
			)
		).format(*названия)
		комментарий = escape_html(строка.comment or "").replace("\n", "<br>")
		return {
			"key": _ключ_возврата(строка),
			"kind": ВЕРНУЛИ,
			"recipient": строка.member,
			"submission": строка.name,
			"subject": _("The homework “{0}” needs more work", lang=язык).format(задание),
			"message": (
				f"<p>{что_случилось}</p>"
				f"<blockquote>{комментарий}</blockquote>"
				+ notices.абзацы(
					notices.ссылка(
						получатели.адрес(строка.lesson, урок.course), _("Open the homework", lang=язык)
					)
				)
			),
		}

	return _по_одному(последние, построить)


# --- дайджест куратору ---


def _кураторы() -> dict[str, set[str]]:
	"""Активные пользователи с ролью куратора и их кураторские роли."""
	роли: dict[str, set[str]] = {}
	for строка in frappe.get_all(
		"Has Role",
		filters={"parenttype": "User", "role": ("in", [РУКОВОДИТЕЛЬ, *МЕТОДИСТЫ])},
		fields=["parent", "role"],
	):
		роли.setdefault(строка.parent, set()).add(строка.role)
	if not роли:
		return {}
	включены = frappe.get_all("User", filters={"name": ("in", list(роли)), "enabled": 1}, pluck="name")
	return {куратор: роли[куратор] for куратор in включены}


def _ждут_проверки() -> list:
	"""Обязательные сдачи на проверке. `Why:` практику ученик сдаёт по желанию,
	и торопить куратора письмом о ней незачем — в очереди она есть
	(learning-services#627)."""
	return frappe.get_all(
		домашка.СДАЧА,
		filters={"status": ДОМАШКА_СДАНА, "member": ("is", "set"), "required": 1},
		fields=["name", "lesson", "member", "organization"],
	)


def _дайджесты(сейчас, _получатели=None) -> list[dict]:
	"""«N домашек ждут проверки» — раз в день, первым запуском после часа
	дайджеста, в котором у куратора есть ожидающие.

	Кто что видит — то же правило, что у очереди (`api/review._отбор`):
	руководителю — сдачи пространств его организаций, модератору — все.
	Методисту — отличие от очереди: только курсы, где он инструктор, чтобы не
	получать чужие. Свои сдачи куратору не считаются, практика — тоже
	(`_ждут_проверки`). Пустая очередь — письма нет.
	"""
	if сейчас.hour < ЧАС_ДАЙДЖЕСТА:
		return []
	день = f"{сейчас:%Y%m%d}"

	def ключ(куратор: str) -> str:
		return f"{куратор}:{ДАЙДЖЕСТ}:{день}"

	роли = _кураторы()
	# Why: сначала — кому ещё не писали сегодня: после первого письма дня
	# ожидающие всей платформы каждый час не выбираются.
	кураторы = _новые(sorted(роли), ключ)
	if not кураторы:
		return []
	ждут = _ждут_проверки()
	if not ждут:
		return []
	курсы = {имя: урок.course for имя, урок in домашка.уроки_с_курсом({с.lesson for с in ждут}).items()}
	руководит: dict[str, set[str]] = {}
	for строка in frappe.get_all(
		"Organization Membership",
		filters={
			"user": ("in", [у for у in кураторы if РУКОВОДИТЕЛЬ in роли[у]] or [""]),
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
			"instructor": ("in", [у for у in кураторы if "Course Creator" in роли[у]] or [""]),
		},
		fields=["instructor", "parent"],
	):
		ведёт.setdefault(строка.instructor, set()).add(строка.parent)

	# Сдачи ушедших из организации её руководителю не считаются
	# (learning-services#585).
	действующие = {
		(строка.user, строка.organization)
		for строка in frappe.get_all(
			"Organization Membership",
			filters={
				"user": ("in", list({с.member for с in ждут if с.organization}) or [""]),
				"status": ЧЛЕНСТВО_ДЕЙСТВУЕТ,
			},
			fields=["user", "organization"],
		)
	}

	def построить(куратор: str) -> dict | None:
		свои_роли = роли[куратор]
		видит = [
			с
			for с in ждут
			if с.member != куратор
			and (
				"Moderator" in свои_роли
				or (
					с.organization in руководит.get(куратор, set())
					and (с.member, с.organization) in действующие
				)
				or курсы.get(с.lesson) in ведёт.get(куратор, set())
			)
		]
		if not видит:
			return None
		язык = languages.язык_пользователя(куратор)
		return {
			"key": ключ(куратор),
			"kind": ДАЙДЖЕСТ,
			"recipient": куратор,
			"submission": None,
			**_дайджест(len(видит), _где(свои_роли, руководит.get(куратор), ведёт.get(куратор)), язык),
		}

	return _по_одному(кураторы, построить)


#: Чья очередь в дайджесте (`_где`).
ВСЯ = ""
В_КУРСАХ = "courses"
В_КОМАНДАХ = "teams"


def _где(роли: set[str], организации, курсы) -> str:
	"""Чья очередь в письме: у одной роли — её область, у нескольких — без уточнения."""
	if "Moderator" in роли or (организации and курсы):
		return ВСЯ
	if курсы:
		return В_КУРСАХ
	if организации:
		return В_КОМАНДАХ
	return ВСЯ


def _дайджест(число: int, где: str, язык: str) -> dict[str, str]:
	"""Тема и текст дайджеста — «N домашек ждут проверки» — на языке куратора.

	Единственное и множественное — разные msgid, форма множественного языка
	— контекст перевода (`languages.форма_числа`): «1 домашка ждёт», «3
	домашки ждут», «5 домашек ждут». Каждый вариант — предложение целиком.
	"""
	форма = languages.форма_числа(число, язык)
	одна = форма == "one"
	if одна:
		тема = _("{0} homework assignment is awaiting review", lang=язык, context=форма)
	else:
		тема = _("{0} homework assignments are awaiting review", lang=язык, context=форма)
	if где == В_КУРСАХ and одна:
		текст = _(
			"In courses you author, {0} homework assignment is awaiting review: students have submitted their work and are waiting for a response.",
			lang=язык,
			context=форма,
		)
	elif где == В_КУРСАХ:
		текст = _(
			"In courses you author, {0} homework assignments are awaiting review: students have submitted their work and are waiting for a response.",
			lang=язык,
			context=форма,
		)
	elif где == В_КОМАНДАХ and одна:
		текст = _(
			"In your teams, {0} homework assignment is awaiting review: students have submitted their work and are waiting for a response.",
			lang=язык,
			context=форма,
		)
	elif где == В_КОМАНДАХ:
		текст = _(
			"In your teams, {0} homework assignments are awaiting review: students have submitted their work and are waiting for a response.",
			lang=язык,
			context=форма,
		)
	elif одна:
		текст = _(
			"{0} homework assignment is awaiting review: students have submitted their work and are waiting for a response.",
			lang=язык,
			context=форма,
		)
	else:
		текст = _(
			"{0} homework assignments are awaiting review: students have submitted their work and are waiting for a response.",
			lang=язык,
			context=форма,
		)
	return {
		"subject": тема.format(число),
		"message": notices.абзацы(
			текст.format(число),
			notices.ссылка(get_url(АДРЕС_ОЧЕРЕДИ), _("Open the review queue", lang=язык)),
		),
	}


# --- отправка ---


def _записанные(ключи: list[str]) -> set[str]:
	"""Ключи, письма по которым уже ушли, — одной выборкой."""
	if not ключи:
		return set()
	return set(frappe.get_all(ЖУРНАЛ, filters={"key": ("in", ключи)}, pluck="key"))


ТОЧКА_ПИСЬМА = "homework_notice"


def _отправить(письмо: dict) -> None:
	"""Запись в журнал и письмо — вместе или никак, и сразу в базу.

	Запись — до отправки, в той же транзакции: очередь писем Frappe отправляет
	только закоммиченное. Коммит — после каждого письма: сбой следующего не
	откатит уже отправленные. Взаимоблокировка или таймаут блокировки MariaDB
	откатывают транзакцию целиком, и точки сохранения больше нет — тогда откат
	всей транзакции: в ней ничего, кроме этого письма, уже нет.
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
		_откатить_письмо()
		frappe.clear_last_message()
	except Exception:
		_откатить_письмо()
		_в_лог("Письмо домашки не отправлено")
	else:
		frappe.db.release_savepoint(ТОЧКА_ПИСЬМА)
		frappe.db.commit()


def _откатить_письмо() -> None:
	try:
		frappe.db.rollback(save_point=ТОЧКА_ПИСЬМА)
	except Exception:
		frappe.db.rollback()


def _название_курса(курс: str | None, язык: str) -> str:
	return переводы_курса.название(курс, languages.основной(язык))
