# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Шапка страниц платформы, которые живут на теме сайта Frappe.

`/author` отрисовывает тема сайта, а не SPA Learning,
и её шапка по умолчанию вела логотипом «Главная» на маркетинговый сайт `/`, а
пунктов не имела вовсе: из документов не было дороги ни к курсам, ни к
занятию (lms-platform#311). Здесь шапка получает те же направления, что
сайдбар Learning, и логотип, ведущий в Learning.

Настройки сайта при этом не трогаются: контекст меняет только страница,
которая сама об этом просит, — страницы входа и прочие страницы Frappe
остаются как были.
"""

from html import escape

import frappe
from frappe import _

#: Куда ведёт логотип: в Learning, откуда ученик сюда пришёл.
ДОМОЙ = "/lms"

#: Пункты шапки — те же направления и в том же порядке, что в сайдбаре
#: Learning (`install.ПУНКТЫ_САЙДБАРА`), плюс курсы: на этих страницах
#: сайдбара нет. Подписи — английский исходник: переводит их `шапка_платформы`
#: на язык запроса (learning-services#767), те же msgid — у сайдбара.
ПУНКТЫ = (
	("Courses", "/lms/courses"),
	("Study in the browser", "/study-in-browser"),
	("Connect an assistant", "/lms/agent"),
	("My documents", "/lms/documents"),
)

#: Кабинет автора — только тем, кто собирает курсы: ученику в нём делать нечего.
РОЛИ_АВТОРА = {"Course Creator", "Moderator"}
КАБИНЕТ_АВТОРА = ("Author workspace", "/author")

#: Первый пункт меню пользователя (аватар в шапке), перед «My Account» и
#: «Log out» Frappe (learning-services#669).
АГЕНТЫ_И_СЕССИИ = ("Agents and sessions", "/connections")

#: Подвал вместо «Built on Frappe»: исходники приложения платформы открыты под
#: AGPL-3.0, и ссылка на них — то, что этим страницам положено по лицензии.
ИСХОДНЫЙ_КОД = "https://github.com/lms-high-time/learning-app"


def подпись(текст: str) -> str:
	"""Подпись пункта шапки на языке запроса. `Why:` пункты — константы
	модуля, а `_()` переводит в момент вызова; ворота переводов
	(`tests/test_translations.py`) сверяют подписи с `ru.csv` отдельно."""
	return _(текст)


def подвал() -> str:
	"""Подвал на языке запроса; ссылка — подстановкой, фраза переводится целиком."""
	return _("The platform's learning app is open source under AGPL-3.0 — {0}source code{1}").format(
		f'<a href="{ИСХОДНЫЙ_КОД}" class="text-muted">', "</a>"
	)


def шапка_платформы(context) -> None:
	"""Логотип и пункты шапки для страницы платформы."""
	пункты = list(ПУНКТЫ)
	if РОЛИ_АВТОРА & set(frappe.get_roles()):
		пункты.append(КАБИНЕТ_АВТОРА)
	context.home_page = ДОМОЙ
	# Имя, которое показывает и SPA Learning (`get_branding`). Шаблон выводит
	# `brand_html` без экранирования — поэтому экранируем здесь.
	имя = frappe.get_cached_value("Website Settings", None, "app_name")
	if имя:
		context.brand_html = escape(имя)
	context.top_bar_items = [frappe._dict(label=подпись(текст), url=адрес) for текст, адрес in пункты]
	текст, адрес = АГЕНТЫ_И_СЕССИИ
	context.post_login = [frappe._dict(label=подпись(текст), url=адрес), *(context.get("post_login") or [])]
	context.footer_powered = подвал()
