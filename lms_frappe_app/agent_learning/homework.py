# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Домашние задания к урокам (learning-services#439).

Задание — `Agent Lesson Homework`, одно на урок, от автора. Сдача —
`Agent Homework Submission`, одна живая на «задание + ученик + пространство».
Все изменения сдачи идут через этот модуль: он пишет журнал и версии, а схема
доктайпа запись разрешает только System Manager.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, time, timedelta

import frappe
from frappe.core.api.file import get_max_file_size
from frappe.core.doctype.file.exceptions import MaxFileSizeReachedError
from frappe.utils import get_datetime, getdate, now_datetime

from lms_frappe_app.agent_learning.artifacts import files as файлы_платформы
from lms_frappe_app.agent_learning.constants import (
	ДОМАШКА_ВОЗВРАЩЕНА,
	ДОМАШКА_ВЫДАНА,
	ДОМАШКА_ПРИНЯТА,
	ДОМАШКА_СДАНА,
)
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.structure import уроки_курса, уроки_по_главам

ЗАДАНИЕ = "Agent Lesson Homework"
СДАЧА = "Agent Homework Submission"

#: Предел файлов в одном ответе. Размер файла — `artifact_file_max_mb`.
ПРЕДЕЛ_ФАЙЛОВ = 10
#: Сумма новых файлов одного сохранения. `Why:` nginx стенда пропускает тело до
#: 50 МБ (`CLIENT_MAX_BODY_SIZE`), а MCP шлёт base64 (+33%): больше — 413 вместо отказа.
ПРЕДЕЛ_СОХРАНЕНИЯ_МБ = 30

ЗАДАНИЯ_НЕТ = "no_homework"
ПУСТОЙ_ОТВЕТ = "empty_answer"
НЕ_ТОТ_ВИД_ОТВЕТА = "answer_mode"
СЛИШКОМ_МНОГО_ФАЙЛОВ = "too_many_files"
ФАЙЛ_ВЕЛИК = "file_too_large"
ОТВЕТ_ВЕЛИК = "answer_too_large"
ФАЙЛ_ОТКЛОНЁН = "file_rejected"
ФАЙЛ_ПУСТ = "file_missing"
НЕВЕРНЫЕ_ФАЙЛЫ = "invalid_files"
УЖЕ_ПРИНЯТА = "accepted_locked"
ЗАНЯТО = "busy"

ТОЧКА_СОХРАНЕНИЯ = "agent_homework_save"
ТОЧКА_ВСТАВКИ = "agent_homework_insert"

ПОЛЯ_ЗАДАНИЯ = ["name", "lesson", "title", "description", "answer_mode", "due_mode", "due_days", "due_date"]


def задание_урока(lesson: str) -> frappe._dict | None:
	return frappe.db.get_value(ЗАДАНИЕ, {"lesson": lesson}, ПОЛЯ_ЗАДАНИЯ, as_dict=True)


def найти_сдачу(
	homework: str, ученик: str, организация: str | None, *, for_update: bool = False
) -> str | None:
	return frappe.db.get_value(
		СДАЧА,
		{"homework": homework, "member": ученик, "organization": организация or ("is", "not set")},
		"name",
		for_update=for_update,
	)


def срок(задание, от: datetime) -> datetime | None:
	"""Срок по правилу — задания или строки назначения. Абсолютный — конец дня
	по времени платформы."""
	if задание.due_mode == "absolute" and задание.due_date:
		return datetime.combine(getdate(задание.due_date), time(23, 59, 59))
	if задание.due_mode == "relative" and задание.due_days:
		return от + timedelta(days=int(задание.due_days))
	return None


def _правила_назначений(задание, ученик: str, организация: str) -> list:
	"""Строки сроков этого задания в назначениях организации, покрывающих ученика.

	Назначения — те же, что дают ученику дедлайн курса, включая выбранные им
	самим (`chosen_by_member`).
	"""
	# Why: импорт внутри — `CourseAllocation.проверить_сроки_домашек` импортирует
	# этот модуль, и на уровне модулей они замкнулись бы в цикл.
	from lms_frappe_app.agent_learning.doctype.course_allocation.course_allocation import (
		СРОКИ_ДОМАШЕК,
		назначения_пользователя,
	)

	курс = frappe.db.get_value("Course Lesson", задание.lesson, "course")
	if not курс:
		return []
	назначения = [н.name for н in назначения_пользователя(ученик, course=курс) if н.organization == организация]
	if not назначения:
		return []
	return frappe.get_all(
		СРОКИ_ДОМАШЕК,
		filters={
			"parent": ("in", назначения),
			"parenttype": "Course Allocation",
			"homework": задание.name,
		},
		fields=["due_mode", "due_days", "due_date"],
	)


def _действующие_правила(задание, ученик: str, организация: str | None) -> tuple[list, str]:
	"""Правила срока сдачи и их источник: назначения организации сдачи, иначе автор.

	Правило назначения перекрывает автора для всей группы; личная сдача — только
	автор: назначения бывают лишь у организаций (learning-services#452).
	"""
	if организация and (правила := _правила_назначений(задание, ученик, организация)):
		return правила, "allocation"
	return [задание], "author"


def срок_для(задание, ученик: str, организация: str | None, от: datetime) -> tuple[datetime | None, str]:
	"""Срок выдачи: ближайший из правил назначений организации сдачи, иначе автора.

	Назначений у ученика бывает несколько — берётся ближайший срок, как дедлайн
	курса в `access._условия_по_курсам`.
	"""
	правила, источник = _действующие_правила(задание, ученик, организация)
	return _ближайший(правила, от), источник


def _ближайший(правила, от: datetime) -> datetime | None:
	сроки = [с for с in (срок(п, от) for п in правила) if с]
	return min(сроки) if сроки else None


def _организация_записи(запись) -> str | None:
	"""Пространство занятия; у попытки квиза — через её занятие."""
	if запись.doctype == "Agent Quiz Attempt":
		return frappe.db.get_value("Agent Learning Session", запись.session, "organization") or None
	return getattr(запись, "organization", None) or None


def _вставить(документ):
	"""Вставить новую сдачу — или взять ту, что уже вставил параллельный вызов.

	Отдаёт пару «сдача, вставлена ли эта». Дубль ловит уникальный индекс
	(`install.обеспечить_индекс_домашек`); откат к точке снимает только эту
	вставку, и сдача перечитывается с блокировкой.

	`Why:` перечитывание помогает, только когда чужая сдача видна снимку этой
	транзакции — дубль в ней же или сдача, зафиксированная до её начала. При
	настоящей гонке MariaDB стенда (`innodb_snapshot_isolation`) отвечает на
	блокирующее чтение новой строки взаимоблокировкой, и она откатывает
	транзакцию целиком: `submit_homework` отдаёт `busy`, фоновая выдача
	повторяет себя (`выдать_по_записи`).
	"""
	frappe.db.savepoint(ТОЧКА_ВСТАВКИ)
	try:
		документ.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		frappe.db.rollback(save_point=ТОЧКА_ВСТАВКИ)
		имя = найти_сдачу(документ.homework, документ.member, документ.organization, for_update=True)
		if not имя:
			raise
		# Сообщение Frappe «must be unique» ответ не несёт: дубль разрешён.
		frappe.clear_last_message()
		return frappe.get_doc(СДАЧА, имя, for_update=True), False
	frappe.db.release_savepoint(ТОЧКА_ВСТАВКИ)
	return документ, True


def _отметить_выдачу(документ, задание, ученик: str, сейчас: datetime) -> None:
	документ.assigned_at = сейчас
	if документ.due_at:
		# Why: срок, выставленный до выдачи, не пересчитывается — и его источник
		# тоже: правило могли снять, и пересчёт приписал бы срок автору.
		источник = _источник_срока(документ)
	else:
		документ.due_at, источник = срок_для(задание, ученик, документ.organization, сейчас)
	документ.append(
		"history",
		{
			"event": "assigned",
			"by_user": ученик,
			"at": сейчас,
			"due_at": документ.due_at,
			"due_source": источник if документ.due_at else None,
		},
	)


def _источник_срока(документ) -> str | None:
	"""Источник выставленного срока — из журнала, где его записали вместе со сроком."""
	for строка in reversed(документ.history):
		if строка.due_source:
			return строка.due_source
	return None


def выдать(запись) -> None:
	"""Выдать домашку урока при его закрытии — занятием или сданным квизом.

	Повторное закрытие и закрытие после сохранения ничего не ломают: уже
	выданная сдача не трогается, а сохранённая до закрытия получает время
	выдачи и относительный срок. `Why:` сроки не пересчитываются задним
	числом — ученик не получает просрочку за чужую правку правила.

	Сдачу, вставленную параллельным сохранением, выдача берёт и отмечает, а не
	падает. `Why:` выдача идёт внутри закрытия урока и зачёта квиза — они не
	должны срываться из-за домашки.
	"""
	задание = задание_урока(запись.lesson)
	if not задание:
		return
	организация = _организация_записи(запись)
	сейчас = now_datetime()
	if имя := найти_сдачу(задание.name, запись.student, организация):
		документ = frappe.get_doc(СДАЧА, имя, for_update=True)
	else:
		новая = frappe.get_doc(
			{
				"doctype": СДАЧА,
				"homework": задание.name,
				"lesson": запись.lesson,
				"member": запись.student,
				"organization": организация,
				"status": ДОМАШКА_ВЫДАНА,
				"version": 0,
			}
		)
		_отметить_выдачу(новая, задание, запись.student, сейчас)
		документ, вставлена = _вставить(новая)
		if вставлена:
			return
	if документ.assigned_at:
		return
	_отметить_выдачу(документ, задание, запись.student, сейчас)
	документ.save(ignore_permissions=True)


#: Сколько раз фоновая выдача повторяет себя после гонки за сдачу.
ПОВТОРОВ_ВЫДАЧИ = 2


def выдать_по_записи(doctype: str, name: str) -> None:
	"""Фоновая выдача по занятию или попытке квиза — после коммита их закрытия.

	Гонка за сдачу (параллельное сохранение, второе закрытие) даёт дубль на
	вставке или взаимоблокировку — MariaDB со снимочной изоляцией отдаёт так и
	блокирующее чтение строки, появившейся после снимка. Обе откатывают
	транзакцию: повтор идёт с нового снимка, где чужая сдача уже видна. Не
	вышло и после повторов — в лог: урок закрыт, а сдачу ученик заведёт
	сохранением сам.
	"""
	for попытка in range(ПОВТОРОВ_ВЫДАЧИ + 1):
		try:
			выдать(frappe.get_doc(doctype, name))
			return
		except (frappe.QueryDeadlockError, frappe.UniqueValidationError, frappe.DuplicateEntryError):
			frappe.db.rollback()
			if попытка == ПОВТОРОВ_ВЫДАЧИ:
				frappe.log_error(title="Домашка не выдана", reference_doctype=doctype, reference_name=name)


def _новая_сдача(задание, ученик: str, организация: str | None, сейчас: datetime):
	# Why: абсолютный срок известен сразу, относительный — только при выдаче.
	# Среди действующих правил есть относительное — срок ставит выдача:
	# ближайшим он может оказаться только тогда.
	правила, источник = _действующие_правила(задание, ученик, организация)
	абсолютные = all(п.due_mode == "absolute" for п in правила)
	документ = frappe.get_doc(
		{
			"doctype": СДАЧА,
			"homework": задание.name,
			"lesson": задание.lesson,
			"member": ученик,
			"organization": организация,
			"status": ДОМАШКА_СДАНА,
			"version": 0,
			"due_at": _ближайший(правила, сейчас) if абсолютные else None,
		}
	)
	# Источник срока пишется в журнал первым сохранением (`сохранить`).
	документ.flags.due_source = источник if документ.due_at else None
	return документ


def _ответ(документ, answer: str | None, новые: Sequence[tuple[str, bytes]], убрать: Sequence[str]):
	"""Текст и прежние файлы нового ответа — или отказ, до первой записи."""
	if документ.status == ДОМАШКА_ПРИНЯТА:
		raise Отказ(УЖЕ_ПРИНЯТА, "Домашку уже приняли — её больше не правят", submission=документ.name)
	текст = (документ.answer or "") if answer is None else answer.strip()
	убираемые = set(убрать)
	файлы = [строка.file for строка in документ.files if строка.file not in убираемые]
	if len(файлы) + len(новые) > ПРЕДЕЛ_ФАЙЛОВ:
		raise Отказ(СЛИШКОМ_МНОГО_ФАЙЛОВ, f"В ответе не больше {ПРЕДЕЛ_ФАЙЛОВ} файлов", limit=ПРЕДЕЛ_ФАЙЛОВ)
	if not текст and not файлы and not новые:
		raise Отказ(ПУСТОЙ_ОТВЕТ, "Ответ пустой: нужен текст или файл")
	return текст, файлы


def сохранить(
	ученик: str,
	lesson: str,
	организация: str | None,
	*,
	answer: str | None = None,
	новые: Sequence[tuple[str, bytes]] = (),
	убрать: Sequence[str] = (),
):
	"""Сохранить ответ ученика: одна версия на вызов, статус — «сдана».

	`answer=None` оставляет текст как был: окно файлов и сдача текста через
	агента меняют каждое своё. Неполный ответ (`text_and_files` без файлов)
	принимается — оценки нет, его видит и возвращает куратор. Доступ к курсу и
	пространство проверяет вызывающий метод.

	Ограничение (learning-services#439): каждое сохранение перезаписывает все
	строки `versions` и `history` — так `save()` Frappe пишет дочерние таблицы,
	и цена растёт с числом версий. `Why:` версий у домашки единицы-десятки, а
	второй путь записи мимо `save()` пришлось бы держать в согласии с журналом.
	Станет дорого — вставлять новые строки `db_insert`, а не через родителя.
	"""
	задание = задание_урока(lesson)
	if not задание:
		raise Отказ(ЗАДАНИЯ_НЕТ, "У этого урока нет домашнего задания", lesson=lesson)
	текст_можно = задание.answer_mode in ("text", "text_and_files")
	файлы_можно = задание.answer_mode in ("files", "text_and_files")
	if answer and answer.strip() and not текст_можно:
		raise Отказ(НЕ_ТОТ_ВИД_ОТВЕТА, "Это задание сдаётся файлами", answer_mode=задание.answer_mode)
	if новые and not файлы_можно:
		raise Отказ(НЕ_ТОТ_ВИД_ОТВЕТА, "Это задание сдаётся текстом", answer_mode=задание.answer_mode)
	предел = файлы_платформы.предел_байт()
	for имя_файла, данные in новые:
		# Why: пустой или битый base64 приходит пустыми байтами — выброшенный
		# молча, файл пропал бы из ответа, а ученик считал бы его сданным.
		if not данные:
			raise Отказ(ФАЙЛ_ПУСТ, "Файл не передан или пуст", file=имя_файла)
		if len(данные) > предел:
			raise Отказ(ФАЙЛ_ВЕЛИК, "Файл больше допустимого", file=имя_файла, limit_mb=предел // (1024 * 1024))
	if sum(len(данные) for _, данные in новые) > ПРЕДЕЛ_СОХРАНЕНИЯ_МБ * 1024 * 1024:
		raise Отказ(
			ОТВЕТ_ВЕЛИК,
			f"За одно сохранение — не больше {ПРЕДЕЛ_СОХРАНЕНИЯ_МБ} МБ файлов",
			limit_mb=ПРЕДЕЛ_СОХРАНЕНИЯ_МБ,
		)

	сейчас = now_datetime()
	if имя := найти_сдачу(задание.name, ученик, организация):
		документ = frappe.get_doc(СДАЧА, имя, for_update=True)
	else:
		документ = _новая_сдача(задание, ученик, организация, сейчас)
	новая = not документ.name
	текст, файлы = _ответ(документ, answer, новые, убрать)

	# Все отказы, кроме отказов Frappe на файл (`file_rejected`, `file_too_large`
	# по его пределу), — выше, до первой записи. Отклонённый файл откатывает
	# сохранение целиком. `Why:` `@контракт` отдаёт отказ успешным ответом, и
	# транзакция запроса коммитится — без точки сохранения осталась бы новая
	# сдача без версии и файлы, принятые до отклонённого.
	frappe.db.savepoint(ТОЧКА_СОХРАНЕНИЯ)
	# Why: файл привязывается к записи по имени — вставленный раньше неё остаётся
	# без привязки (#343). Не `is_new()`: у записи из `frappe.get_doc({...})`
	# нет `__islocal`, и проверка отвечает «не новая».
	if not документ.name:
		документ, вставлена = _вставить(документ)
		if not вставлена:
			# Сдачу вставил параллельный вызов: ответ собирается на ней заново.
			текст, файлы = _ответ(документ, answer, новые, убрать)
	вложенные = []
	имя_файла = None
	try:
		for имя_файла, данные in новые:
			файлы.append(_вложить_файл(документ, имя_файла, данные, вложенные).name)
	except Отказ:
		_откатить(вложенные)
		raise
	except MaxFileSizeReachedError as ошибка:
		_откатить(вложенные)
		# Why: предел Frappe (`max_file_size`) бывает меньше нашего.
		raise Отказ(
			ФАЙЛ_ВЕЛИК,
			"Файл больше допустимого",
			file=имя_файла,
			limit_mb=get_max_file_size() // (1024 * 1024),
		) from ошибка
	except frappe.ValidationError as ошибка:
		_откатить(вложенные)
		# Why: запрещённый тип Frappe отдаёт 417 без кода — агенту нужен код контракта.
		raise Отказ(ФАЙЛ_ОТКЛОНЁН, "Такой файл сдать нельзя", file=имя_файла) from ошибка
	frappe.db.release_savepoint(ТОЧКА_СОХРАНЕНИЯ)

	документ.answer = текст
	документ.set("files", [{"file": ф} for ф in файлы])
	документ.version = (документ.version or 0) + 1
	документ.status = ДОМАШКА_СДАНА
	документ.submitted_at = сейчас
	документ.append(
		"versions",
		{"version": документ.version, "saved_at": сейчас, "answer": текст, "files": json.dumps(файлы)},
	)
	строка = {"event": "submitted", "by_user": ученик, "at": сейчас, "version": документ.version}
	if новая and документ.due_at and документ.flags.due_source:
		# Why: срок, выставленный первым сохранением, получает источник в журнале,
		# как у выдачи: выдача потом берёт его отсюда (learning-services#452).
		строка.update(due_at=документ.due_at, due_source=документ.flags.due_source)
	документ.append("history", строка)
	документ.save(ignore_permissions=True)
	return документ


def _вложить_файл(документ, имя: str, данные: bytes, вложенные: list):
	"""Приватный `File`, привязанный к сдаче: права на него Frappe берёт у сдачи.

	Файл попадает в `вложенные` до вставки: `File.before_insert` пишет байты на
	диск раньше, чем вставка может упасть.
	"""
	файл = файлы_платформы.новый_файл(документ, имя or "file", данные)
	вложенные.append(файл)
	return файл.insert(ignore_permissions=True)


def _откатить(вложенные: list) -> None:
	"""Откат сохранения к точке и байты его файлов — с диска.

	`Why:` откат к точке сохранения не зовёт `frappe.db.after_rollback`, а
	`File.before_insert` уже записал байты: без уборки они остались бы на диске
	без записи о них. `on_rollback` удаляет файл, только если на то же
	содержимое не ссылается другой `File` (`File._delete_file_on_disk`), — общий
	файл прежней версии цел. Зовётся после отката: откаченные записи о файлах
	уже не считаются ссылками.
	"""
	frappe.db.rollback(save_point=ТОЧКА_СОХРАНЕНИЯ)
	for файл in вложенные:
		if файл.flags.new_file:
			файл.on_rollback()


# --- проверка куратором (learning-services#452) ---

НЕ_ТОТ_СТАТУС = "wrong_status"
НУЖЕН_КОММЕНТАРИЙ = "comment_required"
ВЕРСИЯ_УСТАРЕЛА = "stale_version"

#: Действие куратора: из статуса, в статус, событие журнала, нужен ли комментарий.
ПЕРЕХОДЫ = {
	"accept": (ДОМАШКА_СДАНА, ДОМАШКА_ПРИНЯТА, "accepted", False),
	"send_back": (ДОМАШКА_СДАНА, ДОМАШКА_ВОЗВРАЩЕНА, "returned", True),
	"reopen": (ДОМАШКА_ПРИНЯТА, ДОМАШКА_ВОЗВРАЩЕНА, "reopened", True),
}
#: События, после которых версия считается проверенной.
СОБЫТИЯ_ПРОВЕРКИ = ("returned", "accepted", "reopened")
#: События с комментарием куратора — что ученику доделать.
СОБЫТИЯ_С_КОММЕНТАРИЕМ = ("returned", "reopened")


def проверить(куратор: str, имя: str, действие: str, version: int, comment: str | None = None):
	"""Принять, вернуть или отменить приём. Права проверяет вызывающий метод.

	`version` — версия, которую куратор видел: ученик успел сохранить новую —
	отказ, а не приём непрочитанного. Все отказы — до первой записи: `@контракт`
	коммитит транзакцию и при отказе.
	"""
	из_статуса, в_статус, событие, нужен = ПЕРЕХОДЫ[действие]
	текст = (comment or "").strip()
	if нужен and not текст:
		raise Отказ(НУЖЕН_КОММЕНТАРИЙ, "Напишите, что доделать", submission=имя)
	документ = frappe.get_doc(СДАЧА, имя, for_update=True)
	if not документ.member:
		# Why: сброс прогресса архивирует сдачу, пока куратор на неё смотрит.
		raise Отказ(НЕ_ТОТ_СТАТУС, "Сдача больше не активна", submission=имя, status=документ.status)
	if int(version) != (документ.version or 0):
		raise Отказ(
			ВЕРСИЯ_УСТАРЕЛА,
			"Ученик сохранил новую версию — посмотрите её",
			submission=имя,
			version=документ.version,
		)
	if документ.status != из_статуса:
		raise Отказ(НЕ_ТОТ_СТАТУС, "Сдачу уже проверили", submission=имя, status=документ.status)
	документ.status = в_статус
	документ.append(
		"history",
		{
			"event": событие,
			"by_user": куратор,
			"at": now_datetime(),
			"version": документ.version,
			"comment": текст or None,
		},
	)
	документ.save(ignore_permissions=True)
	return документ


# --- представление ---


def адреса_уроков(курс: str) -> dict[str, str]:
	"""Адреса уроков курса в SPA: `/lms/courses/<курс>/learn/<глава>-<урок>`.

	`Why:` порядок глав и уроков стоит запросов на каждую главу — перечень
	домашек платит его раз на курс, а не на сдачу.
	"""
	адреса: dict[str, str] = {}
	for номер_главы, глава in enumerate(уроки_по_главам(курс), 1):
		for номер_урока, урок in enumerate(глава["lessons"], 1):
			адреса.setdefault(урок, f"/lms/courses/{курс}/learn/{номер_главы}-{номер_урока}")
	return адреса


def адрес_урока(lesson: str, курс: str) -> str | None:
	return адреса_уроков(курс).get(lesson)


def уроки_с_курсом(уроки) -> dict[str, frappe._dict]:
	"""Название и курс каждого урока — одним запросом. Курс — через главу, как
	у `курс_урока`."""
	if not уроки:
		return {}
	урок = frappe.qb.DocType("Course Lesson")
	глава = frappe.qb.DocType("Course Chapter")
	строки = (
		frappe.qb.from_(урок)
		.left_join(глава)
		.on(глава.name == урок.chapter)
		.select(урок.name, урок.title, глава.course)
		.where(урок.name.isin(list(уроки)))
	).run(as_dict=True)
	return {строка.name: строка for строка in строки}


def _последние_события(сдачи: list[str], события: Sequence[str], поле: str) -> dict:
	"""`поле` последней строки журнала с одним из `события` у каждой сдачи — одной выборкой."""
	if not сдачи:
		return {}
	значения: dict = {}
	for строка in frappe.get_all(
		"Agent Homework Event",
		filters={
			"parenttype": СДАЧА,
			"parentfield": "history",
			"parent": ("in", сдачи),
			"event": ("in", list(события)),
		},
		fields=["parent", поле],
		order_by="idx asc",
	):
		значения[строка.parent] = строка[поле]
	return значения


def названия(doctype: str, имена, поле: str = "title") -> dict[str, str]:
	"""`поле` записей по имени — одной выборкой; пустые имена отбрасываются."""
	имена = [и for и in имена if и]
	if not имена:
		return {}
	return dict(frappe.get_all(doctype, filters={"name": ("in", имена)}, fields=["name", поле], as_list=True))


def последние_комментарии(сдачи: list[str]) -> dict[str, str | None]:
	"""Комментарий последнего возврата или отмены приёма каждой сдачи — одной выборкой.

	То же, что `последний_комментарий`, но без чтения сдач целиком.
	"""
	return _последние_события(сдачи, СОБЫТИЯ_С_КОММЕНТАРИЕМ, "comment")


def проверенные_версии(сдачи: list[str]) -> dict[str, int]:
	"""Последняя проверенная версия каждой сдачи — одной выборкой. Не проверяли — сдачи нет в ответе."""
	return _последние_события(сдачи, СОБЫТИЯ_ПРОВЕРКИ, "version")


def описание_задания(задание) -> dict:
	return {
		"lesson": задание.lesson,
		"title": задание.title,
		"description": задание.description,
		"answer_mode": задание.answer_mode,
		"due": {
			"mode": задание.due_mode,
			"days": задание.due_days or None,
			"date": str(задание.due_date) if задание.due_date else None,
		},
	}


def _файлы(имена: list[str], сведения: dict[str, dict]) -> list[dict]:
	"""Файлы ответа по порядку: `id` — имя `File` (для `remove_files`), `name` — имя файла."""
	return [{"id": имя, **сведения[имя]} for имя in имена if имя in сведения]


#: Как назвать в журнале куратора без имени в профиле.
КУРАТОР = "Куратор"
#: Как назвать в журнале куратора ученика без имени в профиле.
УЧЕНИК = "Ученик"


def _автор_события(user: str | None, читатель: str, ученик: str | None = None) -> dict:
	"""`by` и `by_name` строки журнала для `читатель`.

	`Why:` журнал читает ученик — на странице урока и через агента, и почта
	куратора ему ни к чему (learning-services#439). Почта остаётся только у
	своих событий: по ней SPA пишет «Вы». Остальным — имя, без имени — «Куратор»,
	а события `ученик` сдачи — «Ученик»: журнал читает и куратор, и почту
	ученика ему тоже не отдаём (learning-services#452).
	"""
	if not user:
		return {"by": None, "by_name": None}
	имя = frappe.get_cached_value("User", user, "full_name")
	if user == читатель:
		return {"by": user, "by_name": имя or user}
	return {"by": None, "by_name": имя or (УЧЕНИК if user == ученик else КУРАТОР)}


def _время(значение) -> str | None:
	"""Метка времени, как во всём контракте: ISO, без зоны, в поясе сайта."""
	return get_datetime(значение).isoformat() if значение else None


def просрочена(документ) -> bool:
	"""Просрочка — признак, а не статус: срок прошёл, а от ученика ждут действия."""
	return (
		bool(документ.due_at)
		and документ.status in (ДОМАШКА_ВЫДАНА, ДОМАШКА_ВОЗВРАЩЕНА)
		and get_datetime(документ.due_at) < now_datetime()
	)


def описание_сдачи(
	документ, *, полное: bool = True, с_версиями: bool = False, читатель: str | None = None
) -> dict:
	"""Сдача для ответа. Коротко — для лёгкого старта и списка.

	`читатель` — кому отдаётся журнал (по умолчанию пользователь сессии): от
	него зависит, чья почта в `history` видна (`_автор_события`).
	"""
	короткое = {
		"id": документ.name,
		"status": документ.status,
		"due_at": _время(документ.due_at),
		"overdue": просрочена(документ),
		"version": документ.version,
	}
	if not полное:
		return короткое
	имена = [с.file for с in документ.files]
	ученик = документ.member or документ.archived_student
	версии = [(в, json.loads(в.files or "[]")) for в in документ.versions] if с_версиями else []
	# Файлы ответа и всех версий — одним запросом, а не запросом на версию.
	сведения = файлы_платформы.сведения_о_файлах(list({*имена, *(и for _, файлы in версии for и in файлы)}))
	ответ = {
		**короткое,
		"assigned_at": _время(документ.assigned_at),
		"submitted_at": _время(документ.submitted_at),
		"answer": документ.answer or "",
		"files": _файлы(имена, сведения),
		"history": [
			{
				"event": с.event,
				**_автор_события(с.by_user, читатель or frappe.session.user, ученик),
				"at": _время(с.at),
				"version": с.version or None,
				"comment": с.comment or None,
				"due_at": _время(с.due_at),
			}
			for с in документ.history
		],
	}
	if с_версиями:
		ответ["versions"] = [
			{
				"version": в.version,
				"saved_at": _время(в.saved_at),
				"answer": в.answer or "",
				"files": _файлы(файлы, сведения),
			}
			for в, файлы in версии
		]
	return ответ


def последний_комментарий(документ) -> str | None:
	"""Комментарий последнего возврата на доработку или отмены приёма."""
	for строка in reversed(документ.history):
		if строка.event in СОБЫТИЯ_С_КОММЕНТАРИЕМ:
			return строка.comment
	return None


def проверенная_версия(документ) -> int | None:
	"""Версия, которую куратор проверял последней: с ней карточка сравнивает текущую."""
	for строка in reversed(документ.history):
		if строка.event in СОБЫТИЯ_ПРОВЕРКИ:
			return строка.version
	return None


def задания_курса(курс: str) -> dict[str, frappe._dict]:
	"""Задания уроков курса по уроку — одним запросом, сразу всеми полями."""
	задание = frappe.qb.DocType(ЗАДАНИЕ)
	урок = frappe.qb.DocType("Course Lesson")
	строки = (
		frappe.qb.from_(задание)
		.join(урок)
		.on(урок.name == задание.lesson)
		.select(*(задание[поле] for поле in ПОЛЯ_ЗАДАНИЯ))
		.where(урок.course == курс)
	).run(as_dict=True)
	return {строка.lesson: строка for строка in строки}


def для_старта(ученик: str, lesson: str, курс: str, организация: str | None, *, полное: bool) -> dict:
	"""`homework` и `previous_homework` для `start_lesson`.

	Прошлый урок — предыдущий по порядку курса, независимо от того, есть ли
	задание у текущего. Сдача — в пространстве занятия.
	"""
	# Why: порядок уроков стоит запросов на каждую главу, а на каждом старте его
	# не платим (student.py, `_место_урока`). У курса без заданий старт платит
	# один запрос — узнать, что их нет.
	задания = задания_курса(курс)
	if not задания:
		return {"homework": None, "previous_homework": None}
	текущее = задания.get(lesson)
	прошлое = None
	уроки = уроки_курса(курс)
	номер = уроки.index(lesson) if lesson in уроки else -1
	if номер > 0 and (задание := задания.get(уроки[номер - 1])):
		имя = найти_сдачу(задание.name, ученик, организация)
		сдача = frappe.get_doc(СДАЧА, имя) if имя else None
		прошлое = {
			"lesson": задание.lesson,
			"title": задание.title,
			"submission": описание_сдачи(сдача, полное=полное, читатель=ученик) if сдача else None,
			"last_comment": последний_комментарий(сдача) if сдача else None,
			**({"homework": описание_задания(задание)} if полное else {}),
		}
	# Why: задание текущего урока нужно и лёгкому старту, а описание короткое —
	# отдаём целиком. Ответ ученика по прошлому уроку в лёгком старте не отдаём:
	# он длинный, его читает `my_homework(lesson=…)`.
	return {"homework": описание_задания(текущее) if текущее else None, "previous_homework": прошлое}
