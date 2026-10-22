# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Чтения Learning для страниц — по правилу витрины и только персоналу (learning-services#692).

Страницы Learning берут оглавление, урок и каталог методами `lms.lms.utils`,
открытыми и гостю. Состав курса вне витрины — неопубликованного или анонса —
открыт тому, кому курс открыт (`permissions.курс_открыт`): записанному,
инструктору курса и персоналу; то же правило держат карта курса, вход в урок
и главы через `/api/resource`.

Методы подменяются хуком `override_whitelisted_methods`, а не правкой форка;
оригиналы сняты из реестра whitelisted (`whitelist_guard.py`), и под другими
путями модулей они не вызываются. Разрешённый вызов уходит в Learning как
есть.

- Оглавление (`get_course_outline`) и урок (`get_lesson`) курса, который
  пользователю не открыт, — пустой ответ, как Learning отвечает при закрытом
  гостевом доступе.
- Отзывы курса (`get_reviews`) — по тому же правилу, иначе `[]`.
- Каталог (`get_courses`, `get_course_count`) — только опубликованные курсы,
  анонсы в их числе: карточка анонса в каталоге и есть его витрина. Группы
  (`get_batches`, `get_batch_count`) — только опубликованные. Запрос
  неопубликованных — пустой ответ. Вкладка своих записей (`enrolled`) — как
  в Learning. Персоналу — без ограничения; для групп и статистики персонал —
  ещё `Batch Evaluator`, у которого в Learning своя работа с группами.
- Статистика платформы (`get_chart_data`, `get_course_completion_data`,
  `lms.lms.api.get_chart_details`) — только персоналу, остальным отказ в
  правах: её показывает только страница «Статистика» для персонала.
- Страница SPA Learning (`/lms/...`): мета-теги курса и группы, которые
  пользователю не открыты, заменяются мета-тегами сайта, а версия Frappe в
  загрузке страницы — только пользователю Desk (`страница_learning`, хук
  `update_website_context`).
"""

import frappe
from frappe.utils import cint
from lms.lms import api as learning_api
from lms.lms import utils as learning

from lms_frappe_app.agent_learning import permissions


@frappe.whitelist(allow_guest=True)
def get_course_outline(course: str, progress: bool = False) -> list:
	"""Оглавление Learning — если курс пользователю открыт, иначе `[]`."""
	if not permissions.курс_открыт(course):
		return []
	return learning.get_course_outline(course, progress)


@frappe.whitelist(allow_guest=True)
def get_lesson(course: str, chapter: int, lesson: int) -> dict:
	"""Урок Learning — если курс пользователю открыт, иначе `{}`."""
	if not permissions.курс_открыт(course):
		return {}
	return learning.get_lesson(course, chapter, lesson)


@frappe.whitelist(allow_guest=True)
def get_courses(
	filters: dict | None = None, start: int = 0, limit_page_length: int | str | None = None
) -> list:
	"""Каталог Learning по фильтрам витрины (`_фильтры_каталога`)."""
	фильтры = _фильтры_каталога(filters)
	return [] if фильтры is None else learning.get_courses(фильтры, start, limit_page_length)


@frappe.whitelist(allow_guest=True)
def get_course_count(filters: dict | None = None) -> int:
	"""Число курсов каталога по тем же фильтрам, что `get_courses`."""
	фильтры = _фильтры_каталога(filters)
	return 0 if фильтры is None else learning.get_course_count(фильтры)


@frappe.whitelist(allow_guest=True)
def get_reviews(course: str) -> list:
	"""Отзывы курса — если курс пользователю открыт, иначе `[]`."""
	if not permissions.курс_открыт(course):
		return []
	return learning.get_reviews(course)


@frappe.whitelist(allow_guest=True)
def get_batches(
	filters: dict | None = None,
	start: int = 0,
	order_by: str = "start_date",
	limit_page_length: int | str | None = None,
) -> list:
	"""Группы Learning по фильтрам витрины (`_фильтры_каталога`)."""
	фильтры = _фильтры_каталога(filters, _персонал_групп)
	return [] if фильтры is None else learning.get_batches(фильтры, start, order_by, limit_page_length)


@frappe.whitelist(allow_guest=True)
def get_batch_count(filters: dict | None = None) -> int:
	"""Число групп по тем же фильтрам, что `get_batches`."""
	фильтры = _фильтры_каталога(filters, _персонал_групп)
	return 0 if фильтры is None else learning.get_batch_count(фильтры)


@frappe.whitelist()
def get_chart_data(
	chart_name: str, timegrain: str = "Daily", from_date: str | None = None, to_date: str | None = None
):
	"""График статистики — персоналу."""
	_только_персоналу()
	return learning.get_chart_data(chart_name, timegrain, from_date, to_date)


@frappe.whitelist()
def get_course_completion_data() -> list:
	"""Доли пройденных записей на курс — персоналу."""
	_только_персоналу()
	return learning.get_course_completion_data()


@frappe.whitelist()
def get_chart_details() -> dict:
	"""Счётчики платформы — персоналу."""
	_только_персоналу()
	return learning_api.get_chart_details()


#: Роль Learning, которая ведёт группы: для групп и статистики она — персонал.
ВЕДУЩИЙ_ГРУПП = "Batch Evaluator"


def _персонал_групп(user: str) -> bool:
	return permissions.курсы_без_ограничений(user) or ВЕДУЩИЙ_ГРУПП in frappe.get_roles(user)


def _только_персоналу() -> None:
	if not _персонал_групп(frappe.session.user):
		frappe.throw(frappe._("Not permitted"), frappe.PermissionError)


def _фильтры_каталога(filters, персонал=permissions.курсы_без_ограничений) -> dict | None:
	"""Фильтры вызывающего плюс `published`, кроме вкладки своих записей и
	персонала; `None` — вызывающий просит неопубликованное, ответ пуст."""
	фильтры = dict(frappe.parse_json(filters) or {})
	if фильтры.get("enrolled") or персонал(frappe.session.user):
		return фильтры
	if "published" in фильтры and not cint(фильтры["published"]):
		return None
	фильтры["published"] = 1
	return фильтры


def страница_learning(context) -> None:
	"""Хук `update_website_context`: мета-теги и загрузка страницы SPA Learning."""
	if context.get("boot") is None or "meta" not in context:
		return
	путь = frappe.form_dict.get("app_path") or ""
	if frappe.get_cached_value("User", frappe.session.user, "user_type") != "System User":
		context.boot.pop("frappe_version", None)
	if _страница_закрыта(путь.split("/")):
		context.meta = frappe._dict(
			title=context.get("title"),
			image=context.get("favicon"),
			description=frappe.db.get_single_value("LMS Settings", "meta_description"),
			keywords=frappe.db.get_single_value("LMS Settings", "meta_keywords"),
		)


def _страница_закрыта(части: list[str]) -> bool:
	"""`courses/<курс>/…`, `batches/<группа>`, `batches/details/<группа>` — не открыты пользователю."""
	if len(части) < 2 or части[1] in ("", "new"):
		return False
	if части[0] == "courses":
		return not _курс_есть_и_открыт(части[1])
	if части[0] == "batches":
		return not _группа_открыта(части[2] if части[1] == "details" and len(части) > 2 else части[1])
	return False


def _курс_есть_и_открыт(course: str) -> bool:
	return not frappe.db.exists("LMS Course", course) or permissions.курс_открыт(course)


def _группа_открыта(batch: str) -> bool:
	user = frappe.session.user
	if not frappe.db.exists("LMS Batch", batch) or _персонал_групп(user):
		return True
	if frappe.db.get_value("LMS Batch", batch, "published"):
		return True
	return user != "Guest" and bool(
		frappe.db.exists("LMS Batch Enrollment", {"batch": batch, "member": user})
	)
