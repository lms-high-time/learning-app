# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""«Мои документы»: переход в SPA и выгрузка документа файлом.

Страница документа живёт в SPA Learning — `/lms/documents` (learning-services
#331): там таблица правится по клеткам, а не textarea с markdown. Этот адрес
остаётся ради старых ссылок — из веб-чата, из писем, из закладок — и ведёт
туда же, сохраняя курс и документ.

Выгрузка — здесь: markdown для чтения и xlsx, в котором реестр продолжают
вести с командой (#330). Права те же, что у чтения методом; файл собирает
`agent_learning.artifacts.document.выгрузка`.
"""

from urllib.parse import quote

import frappe

from lms_frappe_app.agent_learning import spaces as пространства
from lms_frappe_app.agent_learning.artifacts.document import выгрузка
from lms_frappe_app.api import student, текущий_пользователь

no_cache = 1

СТРАНИЦА = "/lms/documents"
ФОРМАТЫ = ("md", "xlsx")


def адрес_в_spa(course: str | None = None, artifact: str | None = None) -> str:
	if course and artifact:
		return f"{СТРАНИЦА}/{quote(course, safe='')}/{quote(artifact, safe='')}"
	if course:
		return f"{СТРАНИЦА}?course={quote(course, safe='')}"
	return СТРАНИЦА


@frappe.whitelist()
def download(course: str, artifact: str, format: str = "md", space: str | None = None):
	"""Отдаёт документ файлом: markdown или книга Excel — из пространства `space`."""
	пользователь = текущий_пользователь()
	# Выгрузка — чтение своей работы: курс в архиве её не закрывает (learning-services#500).
	student._требовать_доступ_к_курсу(пользователь, course, читать=True)
	пространство = пространства.пространство_курса(пользователь, course, space)
	frappe.response["filename"], frappe.response["filecontent"] = выгрузка(
		пользователь, course, пространство, artifact, format
	)
	frappe.response["type"] = "download"


def get_context(context):
	frappe.local.flags.redirect_location = адрес_в_spa(
		frappe.form_dict.get("course"), frappe.form_dict.get("artifact")
	)
	raise frappe.Redirect
