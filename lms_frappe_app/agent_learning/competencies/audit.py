# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Аудит свидетельств карты компетенций (learning-services#811, дизайн §13).

Аудитор проверяет, следует ли заявленный уровень из записи, — переписки у
Cenda нет, и проверить, было ли всё так в разговоре, нельзя. На пилоте аудит
ручной и выборочный; пишет его команда платформы, ученик своим токеном ни
заключения, ни отмены записать не может (`authoring.audit_evidence`).

| Заключение | Что происходит |
|---|---|
| `sufficient` | ничего |
| `insufficient` | уровень сохраняется, компетенция — в очередь агента на повторную проверку |
| `unverifiable` | ничего |
| `contradiction` | подтверждение выводится из расчёта, карта пересчитывается; компетенция — в очередь |

Заключение — отдельная запись журнала (`Audit`) с причиной и ссылкой на
свидетельство: история не стирается. Отмена не открывает закрытый урок и не
отменяет завершение курса — она меняет только уровень.
"""

import json

import frappe

from lms_frappe_app.agent_learning.competencies import evidence
from lms_frappe_app.agent_learning.errors import Отказ

ДОСТАТОЧНО, НЕДОСТАТОЧНО, НЕЛЬЗЯ_ПРОВЕРИТЬ, ПРОТИВОРЕЧИЕ = (
	"sufficient",
	"insufficient",
	"unverifiable",
	"contradiction",
)
ЗАКЛЮЧЕНИЯ = (ДОСТАТОЧНО, НЕДОСТАТОЧНО, НЕЛЬЗЯ_ПРОВЕРИТЬ, ПРОТИВОРЕЧИЕ)

ЗАКЛЮЧЕНИЕ_НЕИЗВЕСТНО = "audit_conclusion_unknown"
СВИДЕТЕЛЬСТВО_НЕ_НАЙДЕНО = "evidence_not_found"
ПРИЧИНА_ОБЯЗАТЕЛЬНА = "audit_reason_required"

#: Предел выборки за раз: аудитор разбирает записи руками.
ВЫБОРКА_НЕ_БОЛЬШЕ = 100


def заключить(событие: str, заключение: str, причина: str) -> dict:
	"""Заключение аудита по свидетельству — или отказ до записи.

	Проверяются свидетельство и пересмотр уровня ученика, не архивные; прежнее заключение — нет.
	Причина обязательна у всех заключений, кроме «достаточно». У пересмотра заключение уровень
	не возвращает: «недостаточно» или «противоречие» ставят компетенцию в очередь агента на
	уровне снятых подтверждений — новое подтверждение вернёт уровень по обычным правилам.
	"""
	if заключение not in ЗАКЛЮЧЕНИЯ:
		raise Отказ(
			ЗАКЛЮЧЕНИЕ_НЕИЗВЕСТНО,
			"Заключение — одно из: " + ", ".join(ЗАКЛЮЧЕНИЯ),
			conclusion=заключение,
			allowed=list(ЗАКЛЮЧЕНИЯ),
		)
	запись = frappe.db.get_value(
		evidence.ЖУРНАЛ,
		{
			"name": событие,
			"kind": ("in", [evidence.СВИДЕТЕЛЬСТВО, evidence.ПЕРЕСМОТР]),
			"student": ("is", "set"),
		},
		[
			"name",
			"kind",
			"student",
			"course",
			"lesson",
			"lesson_key",
			"session",
			"release",
			"competency",
			"competency_version",
			"outcome",
			"level",
			"withdrawn",
		],
		as_dict=True,
	)
	if not запись:
		raise Отказ(СВИДЕТЕЛЬСТВО_НЕ_НАЙДЕНО, "Свидетельства с таким номером нет", event=событие)
	причина = str(причина or "").strip()
	if заключение != ДОСТАТОЧНО and not причина:
		raise Отказ(
			ПРИЧИНА_ОБЯЗАТЕЛЬНА, "Нужна причина заключения: что не следует из записи", conclusion=заключение
		)
	if len(причина) > evidence.ПРЕДЕЛ_ТЕКСТА:
		raise Отказ(
			evidence.ТЕКСТ_ДЛИННЫЙ,
			f"Причина не длиннее {evidence.ПРЕДЕЛ_ТЕКСТА} знаков",
			field="reason",
			limit=evidence.ПРЕДЕЛ_ТЕКСТА,
		)
	уровень = запись.level or 0
	if запись.kind == evidence.ПЕРЕСМОТР:
		# Повторная проверка — на уровне снятых пересмотром подтверждений.
		снятые = json.loads(запись.withdrawn) if запись.withdrawn else []
		уровень = max(
			frappe.get_all(evidence.ЖУРНАЛ, filters={"name": ("in", снятые or [""])}, pluck="level"),
			default=0,
		)
	# Отменяется только действующее подтверждение: у затруднения уровня нет, а выведенное уже вне расчёта.
	отменить = (
		запись.kind == evidence.СВИДЕТЕЛЬСТВО
		and заключение == ПРОТИВОРЕЧИЕ
		and запись.outcome == evidence.ПОДТВЕРЖДЕНО
		and запись.name
		not in evidence._выведенные(evidence._записи(запись.student, запись.course, запись.competency))
	)
	имя = (
		frappe.get_doc(
			{
				"doctype": evidence.ЖУРНАЛ,
				"kind": evidence.АУДИТ,
				"student": запись.student,
				"course": запись.course,
				"lesson": запись.lesson,
				"lesson_key": запись.lesson_key,
				"session": запись.session,
				"release": запись.release,
				"competency": запись.competency,
				"competency_version": запись.competency_version,
				"audited": запись.name,
				"conclusion": заключение,
				"level": уровень,
				"assessment": причина,
				"withdrawn": json.dumps([запись.name]) if отменить else None,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)
	return {
		"audit": имя,
		"event": запись.name,
		"conclusion": заключение,
		"withdrawn": отменить,
		"state": evidence.состояние(запись.student, запись.course).get(запись.competency, evidence.ПУСТО),
	}


def выборка(курс: str | None, предел: int) -> list[dict]:
	"""Свидетельства и пересмотры на аудит — ещё не проверенные, по приоритету (дизайн §13), внутри —
	новые первыми: пересмотры уровня (у них нет проверки на сервере, кроме «только вниз»), уровень 4,
	записанное при отказе в закрытии, косвенное, с подсказкой или исправлением, остальное.

	Проверенные исключаются и порядок считается в самом запросе: иначе после первой тысячи
	разобранных выборка опустела бы, а старые пересмотры не попали бы в неё никогда. Отдаёт тексты
	записи — аудитор проверяет, следует ли уровень из неё; ученика в ответе нет, архив сброса не входит.
	"""
	кандидаты = frappe.db.sql(
		f"""
		select e.name, e.kind, e.course, e.lesson_key, e.competency, e.outcome, e.level,
			e.task, e.student_action, e.assessment, e.uncertainties,
			e.transfer_difference, e.transfer_adaptation,
			e.task_form, e.hints, e.corrected, e.indirect, e.after_refusal, e.withdrawn, e.occurred_at
		from `tab{evidence.ЖУРНАЛ}` e
		where e.student is not null
			and e.kind in (%(evidence)s, %(revision)s)
			{"and e.course = %(course)s" if курс else ""}
			and not exists (
				select 1 from `tab{evidence.ЖУРНАЛ}` a
				where a.kind = %(audit)s and a.audited = e.name
			)
		order by
			case
				when e.kind = %(revision)s then 0
				when e.outcome = 'confirmed' and e.level = 4 then 1
				when e.after_refusal = 1 then 2
				when e.indirect = 1 then 3
				when e.hints = 1 or e.corrected = 1 then 4
				else 5
			end,
			e.occurred_at desc, e.name desc
		limit %(limit)s
		""",
		{
			"evidence": evidence.СВИДЕТЕЛЬСТВО,
			"revision": evidence.ПЕРЕСМОТР,
			"audit": evidence.АУДИТ,
			"course": курс,
			"limit": предел,
		},
		as_dict=True,
	)
	return [
		{
			"event": з.name,
			"kind": "revision" if з.kind == evidence.ПЕРЕСМОТР else "evidence",
			"course": з.course,
			"lesson": з.lesson_key,
			"competency": з.competency,
			"outcome": з.outcome or None,
			"level": з.level or None,
			"task": з.task or None,
			"student_action": з.student_action or None,
			"assessment": з.assessment or None,
			"uncertainties": з.uncertainties or None,
			"transfer_difference": з.transfer_difference or None,
			"transfer_adaptation": з.transfer_adaptation or None,
			"marks": {
				"task_form": з.task_form or None,
				"hints": bool(з.hints),
				"corrected": bool(з.corrected),
				"indirect": bool(з.indirect),
				"after_refusal": bool(з.after_refusal),
			},
			"withdrawn": json.loads(з.withdrawn) if з.withdrawn else [],
			"recorded_at": з.occurred_at.isoformat() if з.occurred_at else None,
		}
		for з in кандидаты
	]
