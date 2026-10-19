# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Отчёт «Подключения агентов»: кто из пользователей подключил коннектор и
через какого агента.

Читает токены и клиенты OAuth напрямую, своих записей не заводит. `Why:`
факт подключения — служебные данные платформы, они уже хранятся для работы
входа; отдельный журнал был бы второй копией (learning-services#586).
"""

import frappe
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.oauth_client import ВЕБ_ЧАТ, ДРУГОЙ_АГЕНТ, агент_клиента
from lms_frappe_app.install import КЛЮЧ_ЧАТА_ID

COLUMNS = [
	{"label": "Пользователь", "fieldname": "user", "fieldtype": "Link", "options": "User", "width": 240},
	{"label": "Имя", "fieldname": "full_name", "fieldtype": "Data", "width": 180},
	{"label": "Агент", "fieldname": "agent", "fieldtype": "Data", "width": 110},
	{"label": "Клиент", "fieldname": "app_name", "fieldtype": "Data", "width": 160},
	{"label": "Подключений", "fieldname": "connections", "fieldtype": "Int", "width": 110},
	{"label": "Первое подключение", "fieldname": "first_connected", "fieldtype": "Datetime", "width": 170},
	{"label": "Последняя активность", "fieldname": "last_activity", "fieldtype": "Datetime", "width": 170},
	{"label": "Токен действует", "fieldname": "live", "fieldtype": "Check", "width": 120},
	{"label": "Уроков начато", "fieldname": "lessons", "fieldtype": "Int", "width": 120},
]

ОГРАНИЧЕНИЯ = (
	"<p>Строка — пользователь и агент. «Подключений» — сколько раз агент заново "
	"регистрировал клиента: каждое переподключение коннектора заводит новый. "
	"«Последняя активность» — последняя выдача токена: агент обновляет его, пока "
	"пользуется коннектором.</p>"
	"<p>Чего отчёт не видит: коннектор ученика и авторинга у Claude — один и тот же "
	"клиент, их не различить; отключение коннектора на стороне агента до платформы "
	"не доходит; токены удаляются через 30 дней после истечения, и давно "
	"отключившихся пользователей здесь нет. «Уроков начато» у Claude и ChatGPT — "
	"общее число уроков через агентов: занятие не помнит, каким агентом начато.</p>"
)


def execute(filters=None):
	frappe.only_for("System Manager")
	return COLUMNS, строки(), ОГРАНИЧЕНИЯ


def строки() -> list[dict]:
	"""Подключения, свёрнутые до пары (пользователь, агент), свежие вперёд.

	Клиенты незнакомых агентов не сворачиваются друг с другом: строка —
	отдельный `app_name`.
	"""
	клиент_чата = frappe.conf.get(КЛЮЧ_ЧАТА_ID)
	строки: dict[tuple, dict] = {}
	for п in _по_клиентам():
		агент = агент_клиента(п.client, п.redirect_uris, клиент_чата)
		ключ = (п.user, агент, п.app_name if агент == ДРУГОЙ_АГЕНТ else None)
		с = строки.get(ключ)
		if с is None:
			строки[ключ] = {
				"user": п.user,
				"full_name": п.full_name,
				"agent": агент,
				"app_name": п.app_name,
				"connections": 1,
				"first_connected": п.first_connected,
				"last_activity": п.last_activity,
				"live": п.live,
			}
			continue
		с["connections"] += 1
		с["first_connected"] = min(с["first_connected"], п.first_connected)
		с["last_activity"] = max(с["last_activity"], п.last_activity)
		с["live"] = с["live"] or п.live

	уроков = _уроков_начато({с["user"] for с in строки.values()})
	for с in строки.values():
		с["lessons"] = уроков.get((с["user"], с["agent"] == ВЕБ_ЧАТ), 0)
	return sorted(строки.values(), key=lambda с: с["last_activity"], reverse=True)


def _по_клиентам() -> list:
	"""По строке на пару (пользователь, клиент), у которой есть токен.

	Первое подключение — регистрация клиента: токены удаляются через 30 дней
	после истечения, а клиент остаётся. Клиента нет — по первому токену.
	"""
	return frappe.db.sql(
		"""
		SELECT t.user, t.client, MIN(u.full_name) AS full_name, MIN(c.app_name) AS app_name,
			MIN(c.redirect_uris) AS redirect_uris,
			COALESCE(MIN(c.creation), MIN(t.creation)) AS first_connected,
			MAX(t.creation) AS last_activity,
			MAX(t.status = 'Active' AND t.expiration_time > %(now)s) AS live
		FROM `tabOAuth Bearer Token` t
		LEFT JOIN `tabOAuth Client` c ON c.name = t.client
		LEFT JOIN `tabUser` u ON u.name = t.user
		WHERE t.user IS NOT NULL
		GROUP BY t.user, t.client
		""",
		{"now": now_datetime()},
		as_dict=True,
	)


def _уроков_начато(пользователи: set[str]) -> dict[tuple[str, bool], int]:
	"""Число разных уроков в занятиях пользователя — отдельно через веб-чат и
	через агента."""
	if not пользователи:
		return {}
	return {
		(р.student, bool(р.web_chat)): р.lessons
		for р in frappe.db.sql(
			"""
			SELECT student, web_chat, COUNT(DISTINCT lesson) AS lessons
			FROM `tabAgent Learning Session`
			WHERE student IN %(users)s
			GROUP BY student, web_chat
			""",
			{"users": tuple(пользователи)},
			as_dict=True,
		)
	}
