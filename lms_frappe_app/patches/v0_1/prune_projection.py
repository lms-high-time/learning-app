# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Главы и уроки, снятые прежними версиями курсов, — убраны (learning-services#514).

Публикация убирает снятое ею самой (`projection.убрать`); патч убирает
накопленное до неё: у каждого курса с ключом и действующим релизом — главы и
уроки вне релиза (`projection.вне_релиза`). Запись без ссылок удаляется, со
ссылками остаётся вне оглавления. Повторный запуск проверяет оставленные
снова и удаляет те, ссылки на которые ушли. Итог — по курсу и по каждой
записи: имя, ключ и название, у оставленной — чем она держится. `Why:`
удаление необратимо, а лог миграции — единственный его протокол.

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

_ДОКТАЙП = {"chapters": projection.ГЛАВА, "lessons": projection.УРОК}
_ВИД = {"chapters": "глава", "lessons": "урок"}

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
		записи = projection.вне_релиза(курс)
		сведения = _сведения(записи)
		уборка = projection.убрать(записи)
		print(
			f"prune_projection: {курс} — удалено глав: {len(уборка.удалено['chapters'])}, "
			f"уроков: {len(уборка.удалено['lessons'])}; со ссылками осталось глав: "
			f"{len(уборка.оставлено['chapters'])}, уроков: {len(уборка.оставлено['lessons'])}"
		)
		for вид in ("lessons", "chapters"):
			for имя in уборка.удалено[вид]:
				print(f"prune_projection: {курс} — {_запись(вид, имя, сведения)}: удалена")
			for имя in уборка.оставлено[вид]:
				держат = ", ".join(f"{доктайп} — {число}" for доктайп, число in уборка.держат[имя].items())
				print(f"prune_projection: {курс} — {_запись(вид, имя, сведения)}: оставлена, держат {держат}")
	if not курсы:
		print("prune_projection: курсов из релиза нет")


def _сведения(записи: dict[str, list[str]]) -> dict[str, tuple[str | None, str]]:
	"""Запись → (ключ, название) — до удаления, по выборке на вид."""
	сведения = {}
	for вид, имена in записи.items():
		if not имена:
			continue
		доктайп = _ДОКТАЙП[вид]
		for имя, ключ, название in frappe.get_all(
			доктайп,
			filters={"name": ("in", имена)},
			fields=["name", projection.ПОЛЕ_КЛЮЧА[доктайп], "title"],
			as_list=True,
		):
			сведения[имя] = (ключ, название)
	return сведения


def _запись(вид: str, имя: str, сведения: dict) -> str:
	ключ, название = сведения[имя]
	return f"{_ВИД[вид]} {имя} «{название}», ключ {ключ or 'нет'}"
