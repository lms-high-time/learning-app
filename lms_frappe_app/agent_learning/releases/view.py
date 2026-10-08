# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Просмотр релиза курса для автора (learning-services#512).

Только чтение индекса релиза (`index`), снимок не читается: у большого курса
он — мегабайты, а всё показываемое лежит в индексе. Ответы квиза здесь —
с эталонами: просмотр отдают только авторские методы. Число выборок не
зависит от размера курса — каждая часть индекса читается одной выборкой на
весь релиз.
"""

import json

import frappe

from lms_frappe_app.agent_learning.releases import document, index


def карточка(курс: str, название: str, релиз: str) -> dict:
	"""Курс и его действующий релиз: ключ, название, версия, кто и когда опубликовал."""
	запись = index.сведения(релиз)
	return {
		"id": курс,
		"key": запись.course_key,
		"title": название,
		"release": релиз,
		"version": запись.version,
		"published_at": _дата(запись.published_at),
		"published_by": запись.published_by,
		"document_key": запись.document_key,
	}


def релиз_целиком(курс: dict, релиз: str) -> dict:
	"""Действующий релиз курса: главы, уроки с целями, пунктами и квизом, документ.

	`курс` — `карточка` этого релиза.
	"""
	уроки = index.уроки(релиз)
	цели = index.цели_с_пунктами(релиз)
	вопросы = index.вопросы(релиз, с_ответами=True)
	уроки_глав: dict[str, list[str]] = {}
	for у in уроки:
		уроки_глав.setdefault(у.chapter_key, []).append(у.lesson_key)
	return {
		"course": _без_документа(курс),
		"chapters": [{**г, "lessons": уроки_глав.get(г["key"], [])} for г in index.главы(релиз)],
		"lessons": [_урок(у, цели.get(у.lesson_key, []), вопросы.get(у.lesson_key, [])) for у in уроки],
		"document": _документ(курс["id"], релиз, курс["document_key"]),
	}


def урок_релиза(курс: dict, релиз: str, ключ: str) -> dict | None:
	"""Урок действующего релиза с его срезом пакета агента и рамкой курса; нет урока — `None`.

	`agent` — рамка пакета (`frame`, `learn_about_student`, какие есть в
	релизе) и срез урока (`lesson`) как есть: их форму задаёт компилятор курса.
	"""
	урок = index.урок(релиз, ключ)
	if not урок:
		return None
	return {
		"course": _без_документа(курс),
		"lesson": _урок(
			урок,
			index.цели_с_пунктами(релиз, ключ).get(ключ, []),
			index.вопросы(релиз, ключ, с_ответами=True).get(ключ, []),
		),
		"agent": {**index.рамка(релиз), "lesson": index.пакет_урока(релиз, ключ)},
	}


def история(курс: str, действующий: str | None) -> list[dict]:
	"""Релизы курса, свежие вперёд: версия, кто и когда опубликовал, дайджест, документ."""
	return [
		{
			"release": р.name,
			"version": р.version,
			"published_at": _дата(р.published_at),
			"published_by": р.published_by,
			"digest": р.digest,
			"document_key": р.document_key,
			"active": р.name == действующий,
		}
		for р in index.история(курс)
	]


def _без_документа(курс: dict) -> dict:
	"""Карточка наружу: ключ документа отдаёт `document`."""
	return {поле: значение for поле, значение in курс.items() if поле != "document_key"}


def _урок(строка, цели: list[dict], вопросы: list[dict]) -> dict:
	return {
		"key": строка.lesson_key,
		"title": строка.title,
		"hook": строка.hook,
		"chapter": строка.chapter_key,
		"pass_percentage": строка.pass_percentage,
		"sections": json.loads(строка.section_keys or "[]"),
		"homework": json.loads(строка.homework) if строка.homework else None,
		"objectives": цели,
		"questions": вопросы,
	}


def _документ(курс: str, релиз: str, ключ: str | None) -> dict | None:
	"""Документ релиза: название и назначение — из схемы документа курса, которую
	пишет публикация этого релиза; разделы — из индекса.

	`Why:` названия и назначения документа в индексе нет, а читать ради них
	снимок — мегабайты на вызов; действующая схема с ключом документа
	действующего релиза — его проекция.
	"""
	if not ключ:
		return None
	схема = frappe.db.get_value(
		document.ДОКУМЕНТ, {"course": курс, "slug": ключ, "is_active": 1}, ["title", "purpose"], as_dict=True
	)
	return {
		"key": ключ,
		"title": схема.title if схема else None,
		"purpose": (схема.purpose or None) if схема else None,
		"sections": index.разделы_документа(релиз),
	}


def _дата(значение) -> str | None:
	return значение.isoformat() if значение else None
