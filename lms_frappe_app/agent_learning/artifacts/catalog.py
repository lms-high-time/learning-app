# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Проверка каталога документов: каждый шаблон и каждая схема курса из базы —
через проверку и сборку движка (learning-services#377).

Схемы проверяются при записи, но движок меняется и после неё: новое правило
проверки, другая сборка правок. Каталог, записанный старым движком, новый
может не принять или собрать иначе — и увидел бы это ученик, открыв один из
сорока документов. Здесь это видно сразу:

- версия шаблона проходит проверку схемы;
- наследник, собранный заново из закреплённой версии родителя и своих правок,
  совпадает с записанным;
- действующая схема курса из шаблона, собранная заново из закреплённой версии
  и правок курса, проходит проверку и совпадает с записанной — название,
  раскладка, блоки с уроками, холст;
- действующая схема курса без шаблона проходит проверку.

Проверку зовут команда `bench --site <сайт> check-artifact-catalog` и
`after_migrate`: упавший `bench migrate` останавливает выкатку до
переключения релиза. `Why:` снимок каталога в фикстурах тестов был бы проще,
но шаблоны — закрытое содержание курсов, а его в открытом приложении не держат.
"""

from __future__ import annotations

import json

import frappe

from lms_frappe_app.agent_learning.artifacts import overlay, templates
from lms_frappe_app.agent_learning.artifacts.canvas import проверить_холст, холст
from lms_frappe_app.agent_learning.artifacts.course import _блоки_схем, блок_наружу, строки_схемы
from lms_frappe_app.agent_learning.errors import Отказ

#: Коды бед каталога, кроме кодов отказов движка — их беда несёт как есть.
РАСХОДИТСЯ = "catalog_mismatch"
НЕТ_ИСТОЧНИКА = "catalog_source_missing"
СБОЙ = "catalog_error"


def проверить_каталог() -> list[dict]:
	"""Беды каталога: версии шаблонов, затем действующие схемы курсов.

	Беда — `{doctype, name, code, message, details}` и где она: у шаблона
	`template` и `version`, у схемы курса `course`, `artifact` и `version`.
	Пустой список — каталог в порядке.
	"""
	return _беды_шаблонов() + _беды_курсов()


def отчёт(вывести) -> int:
	"""Беды каталога — строками через `вывести`; код выхода команды: 1, если беды есть."""
	беды = проверить_каталог()
	for беда in беды:
		вывести(описать(беда))
	if беды:
		вывести(f"Каталог документов: бед — {len(беды)}")
		return 1
	вывести("Каталог документов в порядке")
	return 0


def после_миграции() -> None:
	"""Сломанный каталог валит `bench migrate`, а с ним и выкатку.

	`Why:` сторож выкатки уже не переключает релиз, если `migrate` упал, —
	проверка каталога встаёт туда же без правки сторожа. На сайте без
	документов бед нет: свежая установка и тестовый сайт мигрируют как прежде.
	"""
	беды = проверить_каталог()
	if беды:
		raise frappe.ValidationError(
			"Каталог документов не проходит проверку движка:\n" + "\n".join(описать(б) for б in беды)
		)
	print("Каталог документов в порядке")


def описать(беда: dict) -> str:
	if беда["doctype"] == templates.DOCTYPE:
		где = f"шаблон {беда['template']} v{беда['version']}"
	else:
		где = f"документ {беда['artifact']} курса {беда['course']} v{беда['version']}"
	подробности = json.dumps(беда["details"], ensure_ascii=False, default=str) if беда["details"] else ""
	return f"{где} ({беда['name']}): {беда['code']} — {беда['message']} {подробности}".rstrip()


def _беда(doctype: str, запись, где: dict, код: str, сообщение: str, **подробности) -> dict:
	return {
		"doctype": doctype,
		"name": запись.name,
		**где,
		"code": код,
		"message": сообщение,
		"details": подробности,
	}


def _проверить(doctype: str, запись, где: dict, проверка) -> list[dict]:
	"""Одна проверка одной записи: отказ движка или сбой — бедой, а не исключением.

	`Why:` сбой на одном документе не должен прятать беды остальных: автору
	нужен весь список, а не первая строка трассировки.
	"""
	try:
		return проверка()
	except Отказ as отказ:
		return [_беда(doctype, запись, где, отказ.код, отказ.сообщение, **отказ.подробности)]
	except Exception as сбой:
		return [_беда(doctype, запись, где, СБОЙ, f"{type(сбой).__name__}: {сбой}")]


def _json(значение):
	return json.loads(значение) if isinstance(значение, str) and значение.strip() else значение


# --- шаблоны ---


def _беды_шаблонов() -> list[dict]:
	записи = frappe.get_all(
		templates.DOCTYPE,
		fields=[
			"name",
			"template",
			"version",
			"title",
			"layout",
			"blocks",
			"canvas",
			"extends",
			"extends_version",
			"overlay",
		],
		order_by="template asc, version asc",
	)
	по_версиям = {(з.template, з.version): з for з in записи}
	беды = []
	for запись in записи:
		где = {"template": запись.template, "version": запись.version}
		беды += _проверить(
			templates.DOCTYPE, запись, где, lambda запись=запись, где=где: _шаблон(запись, где, по_версиям)
		)
	return беды


def _схема_шаблона(запись) -> dict:
	"""Версия шаблона в той форме, в какой её отдаёт `templates.шаблон`."""
	return {
		"template": запись.template,
		"version": запись.version,
		"title": запись.title,
		"layout": запись.layout or "sections",
		"blocks": _json(запись.blocks) or [],
		"canvas": холст(запись.canvas),
	}


def _шаблон(запись, где: dict, по_версиям: dict) -> list[dict]:
	схема = _схема_шаблона(запись)
	блоки, холст_шаблона = templates.проверить_шаблон(схема["blocks"], схема["canvas"])
	if not запись.extends:
		return []
	родитель = по_версиям.get((запись.extends, запись.extends_version))
	if родитель is None:
		return [
			_беда(
				templates.DOCTYPE,
				запись,
				где,
				НЕТ_ИСТОЧНИКА,
				"Версии родителя нет",
				extends=запись.extends,
				extends_version=запись.extends_version,
			)
		]
	if родитель.extends:
		return [
			_беда(
				templates.DOCTYPE,
				запись,
				где,
				НЕТ_ИСТОЧНИКА,
				"Родитель сам наследник",
				extends=родитель.extends,
			)
		]
	собранное = templates.собрать_наследника(_схема_шаблона(родитель), _json(запись.overlay) or {})
	записанное = {"layout": схема["layout"], "blocks": блоки, "canvas": холст_шаблона}
	return _сверить(templates.DOCTYPE, запись, где, собранное, записанное)


def _сверить(doctype: str, запись, где: dict, собранное: dict, записанное: dict) -> list[dict]:
	разошлись = [часть for часть in записанное if собранное.get(часть) != записанное[часть]]
	if not разошлись:
		return []
	return [
		_беда(
			doctype,
			запись,
			где,
			РАСХОДИТСЯ,
			"Собранная заново схема расходится с записанной",
			parts=разошлись,
		)
	]


# --- схемы курсов ---


def _беды_курсов() -> list[dict]:
	действующие = frappe.get_all(
		"Agent Course Artifact",
		filters={"is_active": 1},
		fields=[
			"name",
			"course",
			"slug",
			"version",
			"title",
			"layout",
			"canvas",
			"template",
			"template_version",
			"overlay",
		],
		order_by="course asc, slug asc",
	)
	if not действующие:
		return []
	блоки = _блоки_схем([д.name for д in действующие])
	беды = []
	for запись in действующие:
		где = {"course": запись.course, "artifact": запись.slug, "version": запись.version}
		беды += _проверить(
			"Agent Course Artifact",
			запись,
			где,
			lambda запись=запись, где=где: _схема_курса(запись, где, блоки.get(запись.name, [])),
		)
	return беды


def _схема_курса(запись, где: dict, строки: list) -> list[dict]:
	"""Действующая схема курса: записанная проходит проверку; из шаблона —
	ещё и собирается заново в то же самое."""
	записанное = _канон(
		запись.title,
		запись.layout or "sections",
		[блок_наружу(с) for с in строки],
		холст(запись.canvas),
	)
	if not запись.template:
		return []
	try:
		исходный = templates.шаблон(запись.template, запись.template_version)
	except Отказ as отказ:
		return [
			_беда(
				"Agent Course Artifact",
				запись,
				где,
				НЕТ_ИСТОЧНИКА,
				отказ.сообщение,
				template=запись.template,
				template_version=запись.template_version,
			)
		]
	собранное = overlay.собрать(исходный, templates.разобрать_правки(запись.overlay))
	собранное = _канон(
		собранное["title"], собранное["layout"] or "sections", собранное["blocks"], собранное["canvas"]
	)
	return _сверить("Agent Course Artifact", запись, где, собранное, записанное)


def _канон(название, раскладка, блоки: list, холст_схемы) -> dict:
	"""Схема так, как её пишет `записать_схему`, — проверенной: сверять надо
	то, что увидит ученик, а не форму, в которой её прислали."""
	строки = строки_схемы(блоки)
	return {
		"title": название,
		"layout": раскладка,
		"blocks": [блок_наружу(с) for с in строки],
		"canvas": проверить_холст(холст_схемы, строки),
	}
