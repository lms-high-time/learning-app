# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""«Мои документы»: переход в SPA и выгрузка документа файлом.

Страница документа живёт в SPA Learning — `/lms/documents` (learning-services
#331): там таблица правится по клеткам, а не textarea с markdown. Этот адрес
остаётся ради старых ссылок — из веб-чата, из писем, из закладок — и ведёт
туда же, сохраняя курс и документ.

Выгрузка — здесь: markdown для чтения и xlsx, в котором реестр продолжают
вести с командой (#330). Права те же, что у чтения методом.
"""

from urllib.parse import quote

import frappe

from lms_frappe_app.agent_learning import spaces as пространства
from lms_frappe_app.agent_learning.artifacts import export, values
from lms_frappe_app.api import student, текущий_пользователь

no_cache = 1

СТРАНИЦА = "/lms/documents"
ПУСТОЙ_БЛОК = "_Не заполнено._"
ФОРМАТЫ = ("md", "xlsx")


def адрес_в_spa(course: str | None = None, artifact: str | None = None) -> str:
	if course and artifact:
		return f"{СТРАНИЦА}/{quote(course, safe='')}/{quote(artifact, safe='')}"
	if course:
		return f"{СТРАНИЦА}?course={quote(course, safe='')}"
	return СТРАНИЦА


def собрать_markdown(документ: dict) -> str:
	"""Документ одним файлом: заголовок и блоки в порядке схемы.

	Таблица документа выводится у блока, который её заводит, со всеми
	колонками; у остальных её блоков — поля и заметка. Файл блока сюда не
	вкладывается — только его имя и срез: markdown остаётся текстом.
	"""
	таблицы = документ.get("tables") or {}
	поля = документ.get("fields") or {}
	части = [f"# {документ['title']}"]
	for блок in документ["blocks"]:
		части.append(f"## {блок['title']}")
		куски = []
		значения = [
			f"- {поле['title']}: {export.ячейка_текстом(поле, поля.get(поле['key']))}"
			for поле in блок.get("fields") or []
			if not values.пусто(поля.get(поле["key"]))
		]
		if значения:
			куски.append("\n".join(значения))
		таблица = таблицы.get(блок.get("table") or "")
		if таблица and таблица["owner"] == блок["key"] and таблица["markdown"]:
			куски.append(таблица["markdown"])
		if блок.get("url"):
			куски.append(f"Ссылка: {блок['url']}")
		if блок.get("file"):
			куски.append(f"Файл: {блок['file']['name']}")
			if блок.get("preview"):
				куски.append(блок["preview"])
		if (блок["content"] or "").strip():
			куски.append(блок["content"].strip())
		части.append("\n\n".join(куски) or ПУСТОЙ_БЛОК)
	return "\n\n".join(части) + "\n"


@frappe.whitelist()
def download(course: str, artifact: str, format: str = "md", space: str | None = None):
	"""Отдаёт документ файлом: markdown или книга Excel — из пространства `space`."""
	пользователь = текущий_пользователь()
	student._требовать_доступ_к_курсу(пользователь, course)
	пространство = пространства.пространство_курса(пользователь, course, space)
	документ = student._артефакт_целиком(пользователь, course, пространство, artifact)
	if format == "xlsx":
		схема = student._действующая_схема(course, artifact)
		экземпляр = student._экземпляр(пользователь, course, схема.slug, пространство)
		frappe.response["filename"] = f"{документ['artifact']}.xlsx"
		frappe.response["filecontent"] = export.книга_xlsx(
			документ["title"], схема.blocks, student._данные(экземпляр)
		)
	else:
		frappe.response["filename"] = f"{документ['artifact']}.md"
		frappe.response["filecontent"] = собрать_markdown(документ)
	frappe.response["type"] = "download"


def get_context(context):
	frappe.local.flags.redirect_location = адрес_в_spa(
		frappe.form_dict.get("course"), frappe.form_dict.get("artifact")
	)
	raise frappe.Redirect
