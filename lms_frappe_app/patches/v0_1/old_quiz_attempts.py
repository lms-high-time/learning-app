# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Попытки квиза по `LMS Quiz` — без следов в Learning и без зависших (learning-services#512).

Квиз идёт только по релизу. От попыток по `LMS Quiz` остаются:

- сдачи `LMS Quiz Submission`, на которые ссылается колонка `submission`
  попытки, — итоги для страниц Learning. Удаляются: это данные курсов,
  удалённых в learning-services#500. `Why:` патч идёт раньше
  `drop_removed_columns` — без колонки уже не понять, какую сдачу сделало
  приложение, а какую — сам Learning. Следы удалённых сдач — версии,
  комментарии, уведомления, задачи, просмотры — уходят вместе с ними;
- попытки без релиза в статусе `In Progress`: ответить на них нельзя, а в
  `student_detail` они висят начатыми вечно. Закрываются брошенными — так же,
  как сброс прогресса закрывает открытую попытку (`reset.сбросить_прогресс`):
  `Abandoned` и `finished_at` — сейчас, без сдвига `modified`.

Повторный запуск ничего не меняет: удалённых сдач нет, закрытые попытки не
`In Progress`. Колонку — по базе сайта (`drop_removed_columns.колонки`) — и
таблицы патч проверяет сам.

`Why:` следы сдачи `frappe.delete_doc` убирает задачей в очереди после
коммита (`delete_dynamic_links`); патч зовёт её сам, сразу: итог миграции не
зависит от того, дошёл ли воркер.
"""

import frappe
from frappe.model.delete_doc import delete_dynamic_links
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.constants import ПОПЫТКА_БРОШЕНА, ПОПЫТКА_ИДЁТ
from lms_frappe_app.patches.v0_1.drop_removed_columns import колонки

ПОПЫТКА = "Agent Quiz Attempt"
СДАЧА = "LMS Quiz Submission"


def execute():
	if not frappe.db.table_exists(ПОПЫТКА, cached=False):
		print("old_quiz_attempts: попыток квиза нет — делать нечего")
		return
	сдач = _удалить_сдачи()
	попыток = _закрыть_зависшие()
	print(f"old_quiz_attempts: сдач Learning удалено {сдач}, зависших попыток закрыто {попыток}")


def _удалить_сдачи() -> int:
	if "submission" not in колонки(ПОПЫТКА):
		return 0
	if not frappe.db.table_exists(СДАЧА, cached=False):
		return 0
	сдачи = frappe.db.sql(
		f"SELECT DISTINCT submission FROM `tab{ПОПЫТКА}` WHERE IFNULL(submission, '') != ''",
		pluck=True,
	)
	удалено = 0
	for сдача in сдачи:
		if not frappe.db.exists(СДАЧА, сдача):
			continue
		frappe.delete_doc(
			СДАЧА, сдача, force=True, ignore_permissions=True, ignore_missing=True, delete_permanently=True
		)
		delete_dynamic_links(СДАЧА, сдача)
		удалено += 1
	return удалено


def _закрыть_зависшие() -> int:
	попытки = frappe.get_all(
		ПОПЫТКА,
		filters={"release": ("is", "not set"), "status": ПОПЫТКА_ИДЁТ},
		pluck="name",
	)
	момент = now_datetime()
	for попытка in попытки:
		frappe.db.set_value(
			ПОПЫТКА,
			попытка,
			{"status": ПОПЫТКА_БРОШЕНА, "finished_at": момент},
			update_modified=False,
		)
	return len(попытки)
