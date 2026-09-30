# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import re
from datetime import date

from frappe.model.document import Document
from frappe.utils import cint

from lms_frappe_app.agent_learning.constants import ВИДЫ_ОТВЕТА, ВИДЫ_СРОКА
from lms_frappe_app.agent_learning.errors import Отказ

НЕВЕРНЫЙ_ВИД_ОТВЕТА = "invalid_answer_mode"
НЕВЕРНЫЙ_СРОК = "invalid_due"
ФОРМАТ_ДАТЫ = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class AgentLessonHomework(Document):
	"""Домашнее задание урока от автора: одно на урок, одинаковое для всех.

	Сдачи ссылаются на задание, а не копируют его: исправленная формулировка
	видна всем сразу, прошлые редакции хранит `track_changes`. Проверка полей
	живёт здесь — одна на desk и на авторинг (learning-services#439).
	"""

	def validate(self):
		if self.answer_mode not in ВИДЫ_ОТВЕТА:
			raise Отказ(НЕВЕРНЫЙ_ВИД_ОТВЕТА, "Вид ответа: text, files или text_and_files", answer_mode=self.answer_mode)
		self.due_days, self.due_date = проверить_срок(self.due_mode, self.due_days, self.due_date)


def проверить_срок(due_mode, due_days, due_date, *, режимы=ВИДЫ_СРОКА) -> tuple[int | None, str | None]:
	"""Правило срока — у задания и у строки назначения (learning-services#452).

	Отдаёт `due_days` и `due_date`, нормализованные под режим, или отказ
	`invalid_due`. У строки назначения режима `none` нет: «как у автора» — это
	отсутствие строки.
	"""
	if due_mode not in режимы:
		raise Отказ(НЕВЕРНЫЙ_СРОК, f"Срок: {', '.join(режимы)}", due_mode=due_mode)
	if due_mode == "relative" and cint(due_days) <= 0:
		raise Отказ(НЕВЕРНЫЙ_СРОК, "Относительному сроку нужно число дней больше нуля", due_mode=due_mode)
	if due_mode == "absolute" and not due_date:
		raise Отказ(НЕВЕРНЫЙ_СРОК, "Абсолютному сроку нужна дата", due_mode=due_mode)
	# Why: лишнее поле другого режима читалось бы как действующий срок.
	return (
		cint(due_days) if due_mode == "relative" else None,
		_дата(due_date) if due_mode == "absolute" else None,
	)


def _дата(значение) -> str:
	"""Дата срока строго `ГГГГ-ММ-ДД`.

	`Why:` без проверки «завтра» или 30 февраля доходили до базы, и MariaDB
	отвечала ошибкой — 500 вместо отказа контракта (learning-services#452).
	`getdate` не годится: он угадывает формат и принимает то, чего контракт не
	обещает.
	"""
	текст = str(значение)
	# Why: `fromisoformat` с Python 3.11 принимает и `20300501`, и `2030-W01-1`.
	if not ФОРМАТ_ДАТЫ.fullmatch(текст):
		raise Отказ(НЕВЕРНЫЙ_СРОК, "Дата — ГГГГ-ММ-ДД", due_date=текст)
	try:
		return date.fromisoformat(текст).isoformat()
	except ValueError:
		raise Отказ(НЕВЕРНЫЙ_СРОК, "Дата — ГГГГ-ММ-ДД", due_date=текст)
