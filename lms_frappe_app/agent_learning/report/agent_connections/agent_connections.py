# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Отчёт «Подключения агентов»: кто из пользователей подключил коннектор и
через какого агента.

Читает токены и клиенты OAuth напрямую, своих записей не заводит. `Why:`
факт подключения — служебные данные платформы, они уже хранятся для работы
входа; отдельный журнал был бы второй копией (learning-services#586).
"""

from collections import Counter

import frappe

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
	{"label": "Уроков начато", "fieldname": "lessons", "fieldtype": "Int", "width": 120},
]

ОГРАНИЧЕНИЯ = (
	"<p>Строка — пользователь и агент. «Подключений» — сколько раз агент заново "
	"регистрировал клиента: каждое переподключение коннектора заводит новый. "
	"«Последняя активность» — последняя выдача токена: агент получает новый "
	"каждый час, пока пользуется коннектором.</p>"
	"<p>Чего отчёт не видит. Коннектор ученика и авторинга у Claude — один и тот же "
	"клиент, их не различить. Отключение коннектора на стороне агента до платформы "
	"не доходит. Токены удаляются после истечения (срок — в Log Settings, по "
	"умолчанию 30 дней), отозванные — в течение часа: давно отключившихся "
	"пользователей здесь нет.</p>"
	"<p>«Уроков начато» — разные уроки в текущих занятиях пользователя, без "
	"сброшенных. У агентов это общее число: занятие не помнит, каким агентом "
	"начато. Урок, начатый агентом и продолженный в веб-чате, считается у "
	"веб-чата.</p>"
)


def execute(filters=None):
	# Роли в JSON отчёта закрывают страницу, а `execute` можно позвать и из
	# консоли или кода: в отчёте видно, кто из пользователей чем подключён.
	frappe.only_for("System Manager")
	return COLUMNS, строки(), ОГРАНИЧЕНИЯ


def строки() -> list[dict]:
	"""Подключения, свёрнутые до пары (пользователь, агент), свежие вперёд.

	Клиенты незнакомых агентов не сворачиваются друг с другом: строка —
	отдельный `app_name`. «Клиент» свёрнутой строки — у самого свежего клиента.
	"""
	клиент_чата = frappe.conf.get(КЛЮЧ_ЧАТА_ID)
	подключения = _по_клиентам()
	пользователей = Counter(п.client for п in подключения)
	строки: dict[tuple, dict] = {}
	for п in подключения:
		агент = агент_клиента(п.client, п.redirect_uris, клиент_чата)
		# Регистрация клиента — первое подключение, только если клиент свой:
		# токены удаляются, а клиент остаётся. Общий клиент (веб-чат) заведён
		# при настройке платформы, и его дата — не вход пользователя.
		свой = агент != ВЕБ_ЧАТ and пользователей[п.client] == 1
		первое = п.registered if свой else п.first_token
		ключ = (п.user, агент, п.app_name if агент == ДРУГОЙ_АГЕНТ else None)
		с = строки.get(ключ)
		if с is None:
			строки[ключ] = {
				"user": п.user,
				"full_name": п.full_name,
				"agent": агент,
				"app_name": п.app_name,
				"connections": 1,
				"first_connected": первое,
				"last_activity": п.last_activity,
			}
			continue
		с["connections"] += 1
		с["first_connected"] = min(с["first_connected"], первое)

	уроков = _уроков_начато({с["user"] for с in строки.values()})
	for с in строки.values():
		с["lessons"] = уроков.get((с["user"], с["agent"] == ВЕБ_ЧАТ), 0)
	return sorted(строки.values(), key=lambda с: (с["last_activity"], с["user"], с["agent"]), reverse=True)


def _по_клиентам() -> list:
	"""По строке на пару (пользователь, клиент), у которой есть токен, свежие
	вперёд. Клиента без токенов здесь нет: регистрация прошла, а вход — нет."""
	return frappe.db.sql(
		"""
		SELECT t.user, t.client, MIN(u.full_name) AS full_name, MIN(c.app_name) AS app_name,
			MIN(c.redirect_uris) AS redirect_uris, MIN(c.creation) AS registered,
			MIN(t.creation) AS first_token, MAX(t.creation) AS last_activity
		FROM `tabOAuth Bearer Token` t
		JOIN `tabOAuth Client` c ON c.name = t.client
		LEFT JOIN `tabUser` u ON u.name = t.user
		WHERE t.user IS NOT NULL
		GROUP BY t.user, t.client
		ORDER BY last_activity DESC, t.client
		""",
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
