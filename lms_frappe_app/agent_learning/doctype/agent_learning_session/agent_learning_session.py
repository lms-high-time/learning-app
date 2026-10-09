# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_to_date, now_datetime

from lms_frappe_app.agent_learning.constants import (
	ЗАВЕРШЁННЫЕ,
	ЗАНЯТИЕ_БРОШЕНО,
	ОТКРЫТЫЕ,
	ПЕРЕХОДЫ,
	СОБЫТИЕ_ЗАНЯТИЕ_БРОШЕНО,
)

#: Запасной порог бездействия, если настройки почему-то недоступны.
СРОК_БЕЗДЕЙСТВИЯ_ЧАСОВ = 6


def _срок_бездействия() -> int:
	"""Через сколько часов занятие считается брошенным.

	Величина общая для всех организаций: она техническая, а не педагогическая.
	Строгость зачёта у клиентов разная — но это про квиз, и живёт в политике
	организации.
	"""
	return frappe.get_cached_value(
		"Agent Learning Settings", "Agent Learning Settings", "session_timeout_hours"
	) or СРОК_БЕЗДЕЙСТВИЯ_ЧАСОВ


class AgentLearningSession(Document):
	"""Факт занятия: кто, какой урок, в каком состоянии.

	Ученик видит только свои занятия — ограничение стоит на правах Frappe,
	а не на проверках в вызывающем коде. Поэтому чужой `session` отклоняется
	одинаково, из какого бы канала ни пришёл запрос.
	"""

	def before_insert(self):
		сейчас = now_datetime()
		self.started_at = self.started_at or сейчас
		self.last_activity_at = self.last_activity_at or сейчас
		if not self.course:
			self.course = курс_урока(self.lesson)

	def validate(self):
		self._проверить_переход()
		if self.status in ЗАВЕРШЁННЫЕ and not self.finished_at:
			self.finished_at = now_datetime()

	def _проверить_переход(self) -> None:
		if self.is_new():
			return
		прежний = self.get_doc_before_save()
		if not прежний or прежний.status == self.status:
			return
		if self.status not in ПЕРЕХОДЫ[прежний.status]:
			frappe.throw(
				frappe._("Занятие нельзя перевести из «{0}» в «{1}»").format(
					прежний.status, self.status
				),
				frappe.ValidationError,
			)

	def отметить_активность(self) -> None:
		"""Двигает отметку активности, не трогая остальное.

		set_value вместо сохранения документа: отметка обновляется на каждое
		событие занятия, и полное сохранение ради одного поля дало бы лишние
		версии в истории изменений.
		"""
		frappe.db.set_value(
			self.doctype, self.name, "last_activity_at", now_datetime(), update_modified=False
		)

	def записать_событие(self, kind: str, note: str | None = None) -> Document:
		"""Пишет событие занятия и двигает отметку активности."""
		событие = frappe.get_doc(
			{"doctype": "Agent Session Event", "session": self.name, "kind": kind, "note": note}
		).insert(ignore_permissions=True)
		self.отметить_активность()
		return событие


def курс_урока(lesson: str | None) -> str | None:
	"""Курс, которому принадлежит урок, — через главу.

	`None` вместо отказа: занятие, заведённое по уроку без главы, оставляет
	поле курса пустым, а учебный поток на том же `None` отказывает агенту
	своим кодом. Правило «через главу» при этом одно — реализаций было две.
	"""
	if not lesson:
		return None
	chapter = frappe.db.get_value("Course Lesson", lesson, "chapter")
	return frappe.db.get_value("Course Chapter", chapter, "course") if chapter else None


def закрыть_брошенные_занятия() -> int:
	"""Закрывает занятия, в которых давно ничего не происходило.

	Без этого незакрытые занятия копятся и портят отчётность: ученик,
	закрывший ноутбук на середине урока, навсегда остаётся «в процессе».
	Возвращает число закрытых — по нему видно, что задача действительно
	отработала.
	"""
	порог = add_to_date(now_datetime(), hours=-_срок_бездействия())
	просроченные = frappe.get_all(
		"Agent Learning Session",
		filters={"status": ("in", ОТКРЫТЫЕ), "last_activity_at": ("<", порог)},
		pluck="name",
	)
	for name in просроченные:
		занятие = frappe.get_doc("Agent Learning Session", name)
		занятие.status = ЗАНЯТИЕ_БРОШЕНО
		занятие.save(ignore_permissions=True)
		занятие.записать_событие(СОБЫТИЕ_ЗАНЯТИЕ_БРОШЕНО, "закрыто по бездействию")
		# Попытку квиза не трогаем. Why: пробовалось закрывать её вместе с
		# занятием — и ученик, отвлёкшийся на середине квиза дольше порога,
		# терял все ответы, попытку из лимита и запускал отсчёт паузы. При
		# лимите в одну попытку это блокировало курс навсегда. Открытая
		# попытка ничего не ломает: вернувшийся ученик продолжит её, а
		# публикация новой версии курса переносит её или аннулирует
		# (`release_quiz.перенести_попытки`) — прежнюю версию она не держит.
	return len(просроченные)


