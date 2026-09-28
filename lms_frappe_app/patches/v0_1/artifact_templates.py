# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Документы курсов становятся привязками к шаблонам (learning-services#370).

Каждая действующая схема документа без шаблона даёт шаблон версии 1 — её
название, раскладку, холст и блоки без уроков, — а сама становится привязкой:
уроки блоков уходят в правки курса. Итоговая схема не меняется, и новая
версия схемы курса не заводится: ученик ничего не замечает.

Ключ шаблона — ключ документа через дефис. Документ с тем же ключом в другом
курсе, но с другой схемой, получает свой шаблон с именем курса в ключе:
сливать две разные схемы в один шаблон патч не вправе.

Собранное из шаблона и правок сверяется с тем, что есть; не сошлось — курс
остаётся без шаблона, и патч говорит почему. Повторный запуск берёт только
документы без шаблона, поэтому идемпотентен.
"""

import json
import re

import frappe

from lms_frappe_app.agent_learning.artifacts import overlay, templates
from lms_frappe_app.agent_learning.artifacts.canvas import проверить_холст, холст
from lms_frappe_app.agent_learning.artifacts.codes import НЕВЕРНЫЙ_ШАБЛОН
from lms_frappe_app.agent_learning.artifacts.course import _блоки_схем, блок_наружу, строки_схемы
from lms_frappe_app.agent_learning.errors import Отказ

#: Сколько запасных ключей перебрать, пока не найдётся свободный.
ЗАПАСНЫХ_КЛЮЧЕЙ = 50


def execute():
	привязать_документы()


def привязать_документы(фильтры: dict | None = None) -> None:
	"""Действующие документы без шаблона — привязками; `фильтры` сужают выборку.

	`Why:` тест проверяет патч на своём курсе, а не на всей базе стенда.
	"""
	действующие = frappe.get_all(
		"Agent Course Artifact",
		filters={**(фильтры or {}), "is_active": 1, "template": ("is", "not set")},
		fields=["name", "course", "slug", "title", "layout", "canvas"],
		order_by="creation asc",
	)
	if not действующие:
		return
	блоки = _блоки_схем([д.name for д in действующие])
	for документ in действующие:
		try:
			_привязать(документ, [блок_наружу(с) for с in блоки.get(документ.name, [])])
		except Отказ as причина:
			print(
				f"artifact_templates: {документ.course}/{документ.slug} остаётся без шаблона — "
				f"{причина.код}: {причина.сообщение} {причина.подробности}"
			)


def _привязать(документ, блоки: list[dict]) -> None:
	шаблон = {
		"title": документ.title,
		"layout": документ.layout or "sections",
		"blocks": [{к: в for к, в in блок.items() if к != "lesson"} for блок in блоки],
		"canvas": холст(документ.canvas),
	}
	правки = {"blocks": {б["key"]: {"lesson": б["lesson"]} for б in блоки if б["lesson"]}}
	if not правки["blocks"]:
		правки = {}

	собранное = overlay.собрать(шаблон, правки)
	было = _канон(документ.title, документ.layout or "sections", блоки, шаблон["canvas"])
	стало = _канон(собранное["title"], собранное["layout"], собранное["blocks"], собранное["canvas"])
	if стало != было:
		print(f"artifact_templates: {документ.course}/{документ.slug} — сборка расходится со схемой")
		return

	ключ, версия = _шаблон_для(документ, шаблон)
	frappe.db.set_value(
		"Agent Course Artifact",
		документ.name,
		{
			"template": ключ,
			"template_version": версия,
			"overlay": json.dumps(правки, ensure_ascii=False),
		},
		update_modified=False,
	)
	print(f"artifact_templates: {документ.course}/{документ.slug} → {ключ} v{версия}")


def _канон(название, раскладка, блоки: list[dict], холст_схемы) -> tuple:
	"""Схема в том виде, в каком её пишет `записать_схему`: сверять надо то,
	что увидит ученик, а не форму, в которой её прислали."""
	строки = строки_схемы(блоки)
	return название, раскладка, строки, проверить_холст(холст_схемы, строки)


def _шаблон_для(документ, шаблон: dict) -> tuple[str, int]:
	"""Ключ и версия шаблона с такой же схемой — найденного или заведённого.

	Перебираются ключ документа, он же с курсом, затем с номером: первый
	свободный ключ даёт новый шаблон, занятый с той же схемой — готовый.
	"""
	основа = re.sub(r"[^a-z0-9_-]+", "-", документ.slug.replace("_", "-")).strip("-")[:50]
	if not основа[:1].isalpha():
		основа = f"doc-{основа}"[:50]
	кандидаты = [основа, f"{основа}-{документ.course}".lower()]
	кандидаты += [f"{основа}-{номер}" for номер in range(2, ЗАПАСНЫХ_КЛЮЧЕЙ + 2)]
	блоки, холст_шаблона = templates.проверить_шаблон(шаблон["blocks"], шаблон["canvas"])
	for ключ in кандидаты:
		if not templates.КЛЮЧ_ШАБЛОНА.match(ключ):
			continue
		if not frappe.db.exists(templates.DOCTYPE, {"template": ключ}):
			записано = templates.записать_шаблон(
				ключ,
				шаблон["title"],
				шаблон["blocks"],
				шаблон["layout"],
				шаблон["canvas"],
				f"Из курса {frappe.db.get_value('LMS Course', документ.course, 'title') or документ.course}",
			)
			return ключ, записано["version"]
		готовый = templates.шаблон(ключ)
		if (готовый["title"], готовый["layout"], готовый["blocks"], готовый["canvas"]) == (
			str(шаблон["title"]).strip(),
			шаблон["layout"],
			блоки,
			холст_шаблона,
		):
			return ключ, готовый["version"]
	raise Отказ(НЕВЕРНЫЙ_ШАБЛОН, "Не нашлось свободного ключа шаблона", template=основа)
