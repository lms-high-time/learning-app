# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Ученикам с `en` по ошибке — язык системы (learning-services#633).

До `default_language` новые пользователи получали `en` от умолчания мастера
настройки, а не по своему выбору. Патч переводит учеников с `en` на язык
системных настроек, кроме тех, кто язык менял сам: смена видна в журнале
версий профиля (у `User` он ведётся). Решение владельца — вариант А в #633.
Повторный запуск ничего не меняет: переведённые уже не на `en`.
"""

import json

import frappe

from lms_frappe_app.install import согласовать_умолчание_языка

ОШИБОЧНЫЙ = "en"


def execute():
	согласовать_умолчание_языка()
	язык = frappe.db.get_single_value("System Settings", "language")
	if not язык or язык == ОШИБОЧНЫЙ:
		return
	ученики = frappe.get_all(
		"User", filters={"user_type": "Website User", "language": ОШИБОЧНЫЙ}, pluck="name"
	)
	выбрали = выбиравшие_язык(ученики)
	for ученик in ученики:
		if ученик in выбрали:
			continue
		frappe.db.set_value("User", ученик, "language", язык, update_modified=False)
		frappe.clear_cache(user=ученик)
	print(f"students_language: на {язык} — {len(ученики) - len(выбрали)}, выбрали сами — {len(выбрали)}")


def выбиравшие_язык(пользователи: list[str]) -> set[str]:
	"""Кто из пользователей менял язык в профиле — по журналу версий."""
	if not пользователи:
		return set()
	версии = frappe.get_all(
		"Version",
		filters={"ref_doctype": "User", "docname": ["in", пользователи], "data": ["like", '%"language"%']},
		fields=["docname", "data"],
	)
	return {
		в.docname
		for в in версии
		if any(поле == "language" for поле, *_ in json.loads(в.data).get("changed", []))
	}
