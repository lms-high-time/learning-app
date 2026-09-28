# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Роли организации не открывают desk (learning-services#374).

С `desk_access` роль, выданная по членству, делала руководителя System User:
после входа он попадал в воркспейс сотрудников платформы, а не в «Команду».

Патч, а не только фикстура: миграция синхронизирует фикстуры после патчей,
а `user_type` Frappe пересчитывает только при сохранении пользователя —
у тех, кому роль уже выдана, он сам не поменяется.
"""

import frappe

РОЛИ = ("Organization Manager", "Organization Admin")


def execute():
	for роль in РОЛИ:
		if frappe.db.exists("Role", роль):
			frappe.db.set_value("Role", роль, "desk_access", 0, update_modified=False)

	# Пересохранение пересчитывает тип сам: у кого есть другая роль с desk
	# (сотрудник платформы), тот остаётся System User.
	for имя in frappe.get_all(
		"Has Role",
		filters={"role": ("in", РОЛИ), "parenttype": "User"},
		pluck="parent",
		distinct=True,
	):
		пользователь = frappe.get_doc("User", имя)
		if пользователь.user_type != "System User":
			continue
		пользователь.flags.ignore_permissions = True
		пользователь.save()
