# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Проверка перед тем, как `Agent Competency Event.competency` станет ссылкой на
`Agent Competency` (learning-services#839): каждое значение в журнале — ключ
компетенции каталога.

Патч `pre_model_sync`: идёт раньше синхронизации схемы доктайпов. Есть
значение, которого нет в каталоге, — патч падает и перечисляет такие значения с
числом записей, а `migrate` останавливается до синхронизации: ни схема
доктайпов, ни данные не тронуты. Журнал патч не чистит: запись свидетельства —
работа ученика, и решать, куда её перепривязать, должен человек. Только чтение
прямым SQL; повторный запуск после перепривязки проходит. Архив сброса (пустой
`student`) проверяется так же: ссылка у него та же.

Ключи сравниваются с учётом регистра, как их сравнивает приложение
(`catalog.read.проблемы_релиза`): сравнение строк в MariaDB регистр не различает,
и ключ в другом регистре прошёл бы проверку ссылки, но не нашёлся бы в карте курса.
"""

import frappe

ЖУРНАЛ = "Agent Competency Event"
КОМПЕТЕНЦИЯ = "Agent Competency"


def неизвестные() -> dict[str, int]:
	"""Значения `competency` журнала, которых нет среди ключей каталога: значение → число записей."""
	if not frappe.db.table_exists(ЖУРНАЛ):
		return {}
	ключи = set()
	if frappe.db.table_exists(КОМПЕТЕНЦИЯ):
		ключи = set(frappe.db.sql(f"select name from `tab{КОМПЕТЕНЦИЯ}`", pluck=True))
	return {
		значение or "": число
		for значение, число in frappe.db.sql(
			f"select min(competency), count(*) from `tab{ЖУРНАЛ}` group by binary competency"
		)
		if значение not in ключи
	}


def execute():
	if найдено := неизвестные():
		raise frappe.ValidationError(
			"competency_event_catalog_keys: в журнале компетенций есть ключи не из каталога — "
			"перепривяжите записи на ключи каталога; схема доктайпов и данные не тронуты:\n"
			+ "\n".join(
				f"{значение or '(пусто)'}: записей {число}" for значение, число in sorted(найдено.items())
			)
		)
	print("competency_event_catalog_keys: все ключи журнала компетенций — ключи каталога")
