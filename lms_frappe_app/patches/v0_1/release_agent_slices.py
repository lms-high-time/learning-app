# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Срезы пакета агента — у релизов, опубликованных до них (learning-services#506).

Публикация раскладывает пакет агента по индексу релиза: срез урока — в
строку урока, рамку курса — в запись релиза (`index.строки`). Релизы,
вышедшие раньше, получают их из своего снимка.

Пишет `db.set_value`, а не сохранением: релиз неизменяем, и его `validate`
откажет любому сохранению существующей записи. Пишет только расхождения,
поэтому запускать снова безопасно.
"""

import json

import frappe

from lms_frappe_app.agent_learning.releases import index


def execute():
	for релиз in frappe.get_all(index.РЕЛИЗ, fields=["name", "agent_frame"]):
		рамка, срезы = index.срезы(json.loads(frappe.db.get_value(index.РЕЛИЗ, релиз.name, "snapshot")))
		if _прочитать(релиз.agent_frame) != рамка:
			frappe.db.set_value(
				index.РЕЛИЗ, релиз.name, "agent_frame", _записать(рамка), update_modified=False
			)
		for строка in frappe.get_all(
			index.УРОК,
			filters={"parenttype": index.РЕЛИЗ, "parent": релиз.name},
			fields=["name", "lesson_key", "agent"],
		):
			срез = срезы.get(строка.lesson_key, {})
			if _прочитать(строка.agent) != срез:
				frappe.db.set_value(index.УРОК, строка.name, "agent", _записать(срез), update_modified=False)


def _прочитать(значение) -> dict | None:
	"""Записанное значение поля; пусто — `None`: пустой срез тоже записывается, `{}`."""
	return json.loads(значение) if значение else None


def _записать(значение: dict) -> str:
	return json.dumps(значение, ensure_ascii=False)
