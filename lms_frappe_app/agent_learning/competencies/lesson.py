# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Компетенции урока для агента (learning-services#807).

Цели урока по компетенциям — в индексе релиза (`index.компетенции_урока`),
определения компетенций — в каталоге (learning-services#825), карта курса —
по ним (`index.карта_компетенций`); тексты агенту по компетенциям урока — в
срезе пакета агента (`competencies`). Старт урока
отдаёт агенту компетенции урока с целью и составляющие его интегративных:
по ним агент диагностирует неудачу целостной задачи, цели в уроке у них нет.
Содержание текстов приложение не разбирает (CLAUDE.md §10).
"""

from lms_frappe_app.agent_learning.releases import index


def компетенции_старта(карта: list[dict], цели: list[dict], тексты: dict) -> list[dict]:
	"""Компетенции урока по порядку целей, затем составляющие его интегративных, которых в
	уроке нет: `{key, kind, title, description, target, parts, text, levels, version}`.

	`target` — цель урока, у составляющей без цели — `None`; `parts` — составляющие
	`{competency, from_level}`, у базовой — `[]`; `text` — текст агенту из среза, нет —
	`None`; `levels` — признаки уровней `{"1": …, "4": …}` и `version` — текущая версия
	компетенции в каталоге. Компетенция цели, которой нет в карте, пропускается: релиз с такой
	ссылкой публикация не принимает (`catalog.read.проблемы_релиза`).
	"""
	по_ключу = {к["key"]: к for к in карта if isinstance(к, dict) and "key" in к}
	целевые = {ц["competency"]: ц["target"] for ц in цели if ц.get("competency") in по_ключу}
	порядок = list(целевые)
	for ключ in list(порядок):
		for часть in по_ключу[ключ].get("parts") or []:
			if часть["competency"] in по_ключу and часть["competency"] not in порядок:
				порядок.append(часть["competency"])
	тексты = тексты if isinstance(тексты, dict) else {}
	return [
		{
			"key": ключ,
			"kind": по_ключу[ключ]["kind"],
			"title": по_ключу[ключ]["title"],
			"description": по_ключу[ключ].get("description") or "",
			"target": целевые.get(ключ),
			"parts": list(по_ключу[ключ].get("parts") or []),
			"text": тексты.get(ключ) if isinstance(тексты.get(ключ), str) else None,
			"levels": по_ключу[ключ]["levels"],
			"version": по_ключу[ключ]["version"],
		}
		for ключ in порядок
	]


def для_старта(релиз: str, цели: list[dict], пакет: dict) -> list[dict]:
	"""Компетенции урока для `start_lesson` по его целям (`index.пакет_и_компетенции_урока`); у урока
	без целей — `[]` без запроса, иначе карта курса (`index.карта_компетенций`) — без запросов,
	если её уже прочитали в этом запросе, а иначе её выборками."""
	if not цели:
		return []
	return компетенции_старта(index.карта_компетенций(релиз), цели, пакет.get("competencies"))
