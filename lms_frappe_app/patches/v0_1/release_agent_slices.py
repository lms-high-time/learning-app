# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Срезы пакета агента — у релизов, опубликованных до них (learning-services#506).

Публикация раскладывает пакет агента по индексу релиза: срез урока — в
строку урока, рамку курса — в запись релиза (`index.строки`). Релизы,
вышедшие раньше, получают их из своего снимка.

Пакет старого релиза публикация не проверяла на ответы квиза: с ними
(`checks.ответы_в_пакете`) релиз получает пустые срезы, а в журнал ошибок —
пути до ключей. `Why:` срезы отдаются токену ученика, а релиз неизменяем:
исправление — новый релиз автора.

Пишет `db.set_value`, а не сохранением: релиз неизменяем, и его `validate`
откажет любому сохранению существующей записи. Пишет только расхождения,
поэтому запускать снова безопасно.
"""

import json

import frappe

from lms_frappe_app.agent_learning.releases import checks, index


def execute():
	for релиз in frappe.get_all(index.РЕЛИЗ, fields=["name", "agent_frame"]):
		рамка, срезы = _срезы(релиз.name)
		if _прочитать(релиз.agent_frame) != рамка:
			frappe.db.set_value(
				index.РЕЛИЗ, релиз.name, "agent_frame", index.в_json(рамка), update_modified=False
			)
		for строка in frappe.get_all(
			index.УРОК,
			filters={"parenttype": index.РЕЛИЗ, "parent": релиз.name},
			fields=["name", "lesson_key", "agent"],
		):
			срез = срезы.get(строка.lesson_key, {})
			if _прочитать(строка.agent) != срез:
				frappe.db.set_value(
					index.УРОК, строка.name, "agent", index.в_json(срез), update_modified=False
				)


def _срезы(релиз: str) -> tuple[dict, dict[str, dict]]:
	"""Рамка и срезы уроков из снимка; пакет с ответами квиза — пусто, с записью в журнал."""
	снимок = json.loads(frappe.db.get_value(index.РЕЛИЗ, релиз, "snapshot"))
	if утечки := checks.ответы_в_пакете(снимок.get("agent", {})):
		frappe.log_error(
			title="Пакет агента релиза с ответами квиза: срезы пустые (learning-services#506)",
			message="\n".join(у["where"] for у in утечки),
			reference_doctype=index.РЕЛИЗ,
			reference_name=релиз,
		)
		return {}, {}
	return index.срезы(снимок)


def _прочитать(значение) -> dict | None:
	"""Записанное значение поля; пусто — `None`: пустой срез тоже записывается, `{}`."""
	return json.loads(значение) if значение else None
