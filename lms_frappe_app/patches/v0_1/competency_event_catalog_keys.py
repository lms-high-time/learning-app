# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Проверка перед тем, как карта компетенций курса станет только картой каталога
(learning-services#839): `Agent Competency Event.competency` — ссылка на
`Agent Competency`, а поле `Agent Course Release.competency_map` уходит.

Патч `pre_model_sync`: идёт раньше синхронизации схемы доктайпов. Находит —
падает и перечисляет найденное, а `migrate` останавливается до синхронизации:
ни схема доктайпов, ни данные не тронуты, колонка карты на месте
(`drop_competency_map` ждёт этого патча). Ничего не чистит: запись
свидетельства — работа ученика, а релиз — неизменяемая версия курса, и решать,
куда их перепривязать, должен человек. Только чтение прямым SQL; повторный
запуск после перепривязки проходит. Находки:

- значение `competency` журнала, которого нет среди ключей каталога. Ключи
  сравниваются с учётом регистра, как их сравнивает приложение
  (`catalog.read.проблемы_релиза`): сравнение строк в MariaDB регистр не
  различает, и ключ в другом регистре прошёл бы проверку ссылки, но не
  нашёлся бы в карте курса;
- запись журнала без версии компетенции (`competency_version` пусто или
  меньше 1), даже с ключом каталога: журнал по компетенции читается во всех
  курсах ученика, и такая запись стала бы общей — засчёт, очередь, отчёты;
- неосвобождённый релиз (`snapshot` есть) без выпуска каталога, у уроков
  которого есть цели по компетенциям: карта курса строится только из
  каталога, и такой урок было бы не закрыть;
- релиз с непустой картой в `competency_map`: колонку удалит
  `drop_competency_map`.

Архив сброса (пустой `student`) проверяется так же, как записи учеников.
"""

import frappe

from lms_frappe_app.patches.v0_1.drop_removed_columns import колонки

ЖУРНАЛ = "Agent Competency Event"
КОМПЕТЕНЦИЯ = "Agent Competency"
РЕЛИЗ = "Agent Course Release"
УРОК = "Agent Release Lesson"
#: Пустое значение JSON-поля: NULL, пустая строка или пустой список.
ПУСТО = ("", "[]")


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


def без_версии() -> dict[str, int]:
	"""Записи журнала без версии компетенции каталога: значение `competency` → число записей."""
	if not frappe.db.table_exists(ЖУРНАЛ):
		return {}
	return {
		значение or "": число
		for значение, число in frappe.db.sql(
			f"""select min(competency), count(*) from `tab{ЖУРНАЛ}`
			where coalesce(competency_version, 0) < 1 group by binary competency"""
		)
	}


def релизы_без_каталога() -> list[str]:
	"""Неосвобождённые релизы без выпуска каталога, у уроков которых есть цели по компетенциям."""
	if not frappe.db.table_exists(РЕЛИЗ) or "competencies" not in колонки(УРОК):
		return []
	return frappe.db.sql(
		f"""select distinct р.name from `tab{РЕЛИЗ}` р
		join `tab{УРОК}` у on у.parent = р.name and у.parenttype = %(тип)s
		where р.snapshot is not null and coalesce(р.competency_catalog, '') = ''
			and у.competencies is not null and trim(у.competencies) not in %(пусто)s
		order by р.name""",
		{"тип": РЕЛИЗ, "пусто": ПУСТО},
		pluck=True,
	)


def релизы_с_картой() -> list[str]:
	"""Релизы с непустой картой компетенций в колонке `competency_map`; колонки нет — пусто."""
	if "competency_map" not in колонки(РЕЛИЗ):
		return []
	return frappe.db.sql(
		f"""select name from `tab{РЕЛИЗ}`
		where competency_map is not null and trim(competency_map) not in %(пусто)s order by name""",
		{"пусто": ПУСТО},
		pluck=True,
	)


def находки() -> list[str]:
	"""Всё, из-за чего переход на карту только из каталога сломал бы данные, — строками отчёта."""
	строки = []
	for заголовок, найдено in (
		("ключи журнала не из каталога", неизвестные()),
		("записи журнала без версии компетенции", без_версии()),
	):
		строки += [
			f"{заголовок} — {значение or '(пусто)'}: записей {число}"
			for значение, число in sorted(найдено.items())
		]
	строки += [f"релиз без каталога с целями уроков по компетенциям — {имя}" for имя in релизы_без_каталога()]
	строки += [f"релиз с картой компетенций внутри — {имя}" for имя in релизы_с_картой()]
	return строки


def execute():
	if строки := находки():
		raise frappe.ValidationError(
			"competency_event_catalog_keys: журнал или релизы не на каталоге компетенций — "
			"перепривяжите их на каталог; схема доктайпов и данные не тронуты:\n" + "\n".join(строки)
		)
	print("competency_event_catalog_keys: журнал и релизы компетенций — на каталоге")
