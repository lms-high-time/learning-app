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
from datetime import datetime, time, timedelta

import frappe
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
УЖЕ_ПРИНЯТА = "accepted_locked"

ПОЛЯ_ЗАДАНИЯ = ["name", "lesson", "title", "description", "answer_mode", "due_mode", "due_days", "due_date"]


def задание_урока(lesson: str) -> frappe._dict | None:
	return frappe.db.get_value(ЗАДАНИЕ, {"lesson": lesson}, ПОЛЯ_ЗАДАНИЯ, as_dict=True)


def найти_сдачу(homework: str, ученик: str, организация: str | None) -> str | None:
	return frappe.db.get_value(
		СДАЧА,
		{"homework": homework, "member": ученик, "organization": организация or ("is", "not set")},
		"name",
	)


def срок(задание, от: datetime) -> datetime | None:
	"""Срок по правилу автора. Абсолютный — конец дня по времени платформы."""
	if задание.due_mode == "absolute" and задание.due_date:
		return datetime.combine(getdate(задание.due_date), time(23, 59, 59))
	if задание.due_mode == "relative" and задание.due_days:
		return от + timedelta(days=int(задание.due_days))
	return None


def _организация_записи(запись) -> str | None:
	"""Пространство занятия; у попытки квиза — через её занятие."""
	if запись.doctype == "Agent Quiz Attempt":
		return frappe.db.get_value("Agent Learning Session", запись.session, "organization") or None
	return getattr(запись, "organization", None) or None


def выдать(запись) -> None:
	"""Выдать домашку урока при его закрытии — занятием или сданным квизом.

	Повторное закрытие и закрытие после сохранения ничего не ломают: уже
	выданная сдача не трогается, а сохранённая до закрытия получает время
	выдачи и относительный срок. `Why:` сроки не пересчитываются задним
	числом — ученик не получает просрочку за чужую правку правила.
	"""
	задание = задание_урока(запись.lesson)
	if not задание:
		return
	организация = _организация_записи(запись)
	сейчас = now_datetime()
	if имя := найти_сдачу(задание.name, запись.student, организация):
		документ = frappe.get_doc(СДАЧА, имя)
		if документ.assigned_at:
			return
	else:
		документ = frappe.get_doc(
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
	документ.assigned_at = сейчас
	if not документ.due_at:
		документ.due_at = срок(задание, сейчас)
	документ.append(
		"history",
		{
			"event": "assigned",
			"by_user": запись.student,
			"at": сейчас,
			"due_at": документ.due_at,
			"due_source": "author" if документ.due_at else None,
		},
	)
	документ.save(ignore_permissions=True)


def сохранить(
	ученик: str,
	lesson: str,
	организация: str | None,
	*,
	answer: str | None = None,
	новые: list[tuple[str, bytes]] = (),
	убрать: list[str] = (),
):
	"""Сохранить ответ ученика: одна версия на вызов, статус — «сдана».

	`answer=None` оставляет текст как был: окно файлов и сдача текста через
	агента меняют каждое своё. Неполный ответ (`text_and_files` без файлов)
	принимается — оценки нет, его видит и возвращает куратор. Доступ к курсу и
	пространство проверяет вызывающий метод.
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

	сейчас = now_datetime()
	if имя := найти_сдачу(задание.name, ученик, организация):
		документ = frappe.get_doc(СДАЧА, имя)
		if документ.status == ДОМАШКА_ПРИНЯТА:
			raise Отказ(УЖЕ_ПРИНЯТА, "Домашку уже приняли — её больше не правят", submission=имя)
	else:
		документ = frappe.get_doc(
			{
				"doctype": СДАЧА,
				"homework": задание.name,
				"lesson": lesson,
				"member": ученик,
				"organization": организация,
				"status": ДОМАШКА_СДАНА,
				"version": 0,
				# Why: абсолютный срок известен сразу, относительный — только при выдаче.
				"due_at": срок(задание, сейчас) if задание.due_mode == "absolute" else None,
			}
		)

	текст = (документ.answer or "") if answer is None else answer.strip()
	убрать = set(убрать or ())
	файлы = [строка.file for строка in документ.files if строка.file not in убрать]
	новые = [(имя_файла, данные) for имя_файла, данные in новые if данные]
	if len(файлы) + len(новые) > ПРЕДЕЛ_ФАЙЛОВ:
		raise Отказ(СЛИШКОМ_МНОГО_ФАЙЛОВ, f"В ответе не больше {ПРЕДЕЛ_ФАЙЛОВ} файлов", limit=ПРЕДЕЛ_ФАЙЛОВ)
	предел = файлы_платформы.предел_байт()
	for имя_файла, данные in новые:
		if len(данные) > предел:
			raise Отказ(ФАЙЛ_ВЕЛИК, "Файл больше допустимого", file=имя_файла, limit_mb=предел // (1024 * 1024))
	if sum(len(данные) for _, данные in новые) > ПРЕДЕЛ_СОХРАНЕНИЯ_МБ * 1024 * 1024:
		raise Отказ(
			ОТВЕТ_ВЕЛИК,
			f"За одно сохранение — не больше {ПРЕДЕЛ_СОХРАНЕНИЯ_МБ} МБ файлов",
			limit_mb=ПРЕДЕЛ_СОХРАНЕНИЯ_МБ,
		)
	if not текст and not файлы and not новые:
		raise Отказ(ПУСТОЙ_ОТВЕТ, "Ответ пустой: нужен текст или файл")

	# Why: файл привязывается к записи по имени — вставленный раньше неё остаётся
	# без привязки (#343). Не `is_new()`: у записи из `frappe.get_doc({...})`
	# нет `__islocal`, и проверка отвечает «не новая».
	if not документ.name:
		документ.insert(ignore_permissions=True)
	for имя_файла, данные in новые:
		try:
			файл = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": имя_файла or "file",
					"content": данные,
					"is_private": 1,
					"attached_to_doctype": СДАЧА,
					"attached_to_name": документ.name,
				}
			).insert(ignore_permissions=True)
		except frappe.ValidationError as ошибка:
			# Why: запрещённый тип Frappe отдаёт 417 без кода — агенту нужен код контракта.
			raise Отказ(ФАЙЛ_ОТКЛОНЁН, "Такой файл сдать нельзя", file=имя_файла) from ошибка
		файлы.append(файл.name)

	документ.answer = текст
	документ.set("files", [{"file": ф} for ф in файлы])
	документ.version = (документ.version or 0) + 1
	документ.status = ДОМАШКА_СДАНА
	документ.submitted_at = сейчас
	документ.append(
		"versions",
		{"version": документ.version, "saved_at": сейчас, "answer": текст, "files": json.dumps(файлы)},
	)
	документ.append("history", {"event": "submitted", "by_user": ученик, "at": сейчас, "version": документ.version})
	документ.save(ignore_permissions=True)
	return документ


# --- представление ---


def адрес_урока(lesson: str, курс: str) -> str | None:
	"""Адрес урока в SPA: `/lms/courses/<курс>/learn/<глава>-<урок>`."""
	for номер_главы, глава in enumerate(уроки_по_главам(курс), 1):
		if lesson in глава["lessons"]:
			return f"/lms/courses/{курс}/learn/{номер_главы}-{глава['lessons'].index(lesson) + 1}"
	return None


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


def _файлы(имена: list[str]) -> list[dict]:
	"""Файлы ответа по порядку: `id` — имя `File` (для `remove_files`), `name` — имя файла."""
	сведения = файлы_платформы.сведения_о_файлах(имена)
	return [{"id": имя, **сведения[имя]} for имя in имена if имя in сведения]


def _время(значение) -> str | None:
	return str(значение) if значение else None


def просрочена(документ) -> bool:
	"""Просрочка — признак, а не статус: срок прошёл, а от ученика ждут действия."""
	return (
		bool(документ.due_at)
		and документ.status in (ДОМАШКА_ВЫДАНА, ДОМАШКА_ВОЗВРАЩЕНА)
		and get_datetime(документ.due_at) < now_datetime()
	)


def описание_сдачи(документ, *, полное: bool = True, с_версиями: bool = False) -> dict:
	"""Сдача для ответа. Коротко — для лёгкого старта и списка."""
	короткое = {
		"id": документ.name,
		"status": документ.status,
		"due_at": _время(документ.due_at),
		"overdue": просрочена(документ),
		"version": документ.version,
	}
	if not полное:
		return короткое
	ответ = {
		**короткое,
		"assigned_at": _время(документ.assigned_at),
		"submitted_at": _время(документ.submitted_at),
		"answer": документ.answer or "",
		"files": _файлы([с.file for с in документ.files]),
		"history": [
			{
				"event": с.event,
				"by": с.by_user,
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
				"files": _файлы(json.loads(в.files or "[]")),
			}
			for в in документ.versions
		]
	return ответ


def последний_комментарий(документ) -> str | None:
	"""Комментарий последнего возврата на доработку."""
	for строка in reversed(документ.history):
		if строка.event == "returned":
			return строка.comment
	return None


def _уроки_с_заданием(курс: str) -> list[str]:
	"""Уроки курса, у которых есть задание, — одним запросом."""
	задание = frappe.qb.DocType(ЗАДАНИЕ)
	урок = frappe.qb.DocType("Course Lesson")
	return (
		frappe.qb.from_(задание)
		.join(урок)
		.on(урок.name == задание.lesson)
		.select(задание.lesson)
		.where(урок.course == курс)
	).run(pluck=True)


def для_старта(ученик: str, lesson: str, курс: str, организация: str | None, *, полное: bool) -> dict:
	"""`homework` и `previous_homework` для `start_lesson`.

	Прошлый урок — предыдущий по порядку курса, независимо от того, есть ли
	задание у текущего. Сдача — в пространстве занятия.
	"""
	# Why: порядок уроков стоит запросов на каждую главу, а на каждом старте его
	# не платим (student.py, `_место_урока`). У курса без заданий старт платит
	# один запрос — узнать, что их нет.
	с_заданием = _уроки_с_заданием(курс)
	if not с_заданием:
		return {"homework": None, "previous_homework": None}
	текущее = задание_урока(lesson) if lesson in с_заданием else None
	прошлое = None
	уроки = уроки_курса(курс)
	номер = уроки.index(lesson) if lesson in уроки else -1
	if номер > 0 and уроки[номер - 1] in с_заданием and (задание := задание_урока(уроки[номер - 1])):
		имя = найти_сдачу(задание.name, ученик, организация)
		сдача = frappe.get_doc(СДАЧА, имя) if имя else None
		прошлое = {
			"lesson": задание.lesson,
			"title": задание.title,
			"submission": описание_сдачи(сдача, полное=полное) if сдача else None,
			"last_comment": последний_комментарий(сдача) if сдача else None,
			**({"homework": описание_задания(задание)} if полное else {}),
		}
	# Why: задание текущего урока агент озвучивает в конце занятия, и в лёгком
	# старте тоже — описание короткое, отдаём целиком. Ответ ученика по прошлому
	# уроку в лёгком старте не отдаём: агент берёт его `my_homework(lesson=…)`.
	return {"homework": описание_задания(текущее) if текущее else None, "previous_homework": прошлое}
