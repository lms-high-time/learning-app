# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Главы и уроки, снятые прежними версиями курсов, — убраны (learning-services#514).

Публикация убирает снятое ею самой (`projection.убрать`); патч убирает
накопленное до неё: у каждого курса с ключом и действующим релизом — главы и
уроки вне релиза (`projection.вне_релиза`). Запись без ссылок удаляется, со
ссылками остаётся вне оглавления. Повторный запуск проверяет оставленные
снова и удаляет те, ссылки на которые ушли. Итог — по курсу.

Удаление необратимо, а строки индекса неосвобождённых прежних версий держат
свои главы и уроки: пока не выполнен (`patch_log.выполнен`)
`free_release_content` и патчи, которых он ждёт, патч ничего не трогает и
печатает, какие не выполнены. `Why:` миграция с `--skip-failing` идёт дальше
упавшего патча, а отработавший впустую патч в `Patch Log` сам больше не
запустится. Восстановление — после исправления `bench --site <сайт>
run-patch --force` для пропущенных, затем для `prune_projection`.
"""

import frappe

from lms_frappe_app.agent_learning.releases import projection
from lms_frappe_app.patches.v0_1 import free_release_content
from lms_frappe_app.patches.v0_1.patch_log import выполнен

#: Патчи, после которых снятое держат только данные учеников, — по порядку `patches.txt`.
ЖДЁТ = (*free_release_content.ЖДЁТ, "free_release_content")


def execute():
	if не_выполнены := [патч for патч in ЖДЁТ if not выполнен(патч)]:
		print("prune_projection: снятое не убрано — не выполнены патчи " + ", ".join(не_выполнены))
		return
	курсы = frappe.get_all(
		"LMS Course",
		filters={"course_key": ("is", "set"), "active_release": ("is", "set")},
		pluck="name",
		order_by="name",
	)
	for курс in курсы:
		уборка = projection.убрать(projection.вне_релиза(курс))
		print(
			f"prune_projection: {курс} — удалено глав: {len(уборка.удалено['chapters'])}, "
			f"уроков: {len(уборка.удалено['lessons'])}; со ссылками осталось глав: "
			f"{len(уборка.оставлено['chapters'])}, уроков: {len(уборка.оставлено['lessons'])}"
		)
	if not курсы:
		print("prune_projection: курсов из релиза нет")
