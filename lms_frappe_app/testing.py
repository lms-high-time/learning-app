# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Подготовка процесса к прогону тестов (`before_tests` в hooks) и счётчик
обращений к базе для ворот на число запросов."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

import frappe


def before_tests() -> None:
	"""Выключает индексацию поиска Frappe Learning на время тестов.

	`Why:` каждая вставка урока или курса переиндексирует SQLite-индекс
	Learning (`update_doc_index`), а открытие и закрытие файла индекса на
	томе Docker Desktop стоит по 25 мс — в профиле модуля из девяти тестов
	на индекс уходило 9 с из 14. В CI индекса нет вовсе: его строит воркер
	очереди, которого там не запускают, и `update_doc_index` выходит сразу.
	Локальный прогон уравнивается с CI в процессе, а не на диске: файл
	индекса и поиск на стенде не трогаются (lms-platform#126).
	"""
	# nosemgrep: frappe-monkey-patching-not-allowed — только в before_tests: хука на выключение индекса у Learning нет, стенд не затрагивается
	from lms.sqlite import LearningSearch

	LearningSearch.is_search_enabled = lambda self: False


@contextmanager
def запросы_блока() -> Iterator[list[str]]:
	"""Копит SQL, выполненный внутри блока, — по запросу на строку.

	`Why:` через `Database.sql` проходит **всё**: `frappe.db.get_value`,
	`frappe.get_all` и любой `frappe.qb` собираются в объект запроса, а
	исполняет его `frappe.local.db.sql` (`query_builder/utils.py`,
	`execute_query`). Второго пути к базе у Frappe нет, поэтому счётчик,
	подменяющий один метод экземпляра, видит каждое обращение и не зависит от
	того, какой API выбрал вызывающий код.

	Подменяется атрибут экземпляра, а не метод класса: `del` снимает подмену
	начисто, даже если блок вышел исключением, и параллельные тесты чужого
	процесса не затрагиваются.
	"""
	база = frappe.local.db
	исходный = база.sql
	запросы: list[str] = []

	def считающий(query, *args, **kwargs):
		запросы.append(str(query))
		return исходный(query, *args, **kwargs)

	база.sql = считающий
	try:
		yield запросы
	finally:
		del база.sql


def сколько_запросов(действие: Callable[[], object]) -> tuple[int, list[str]]:
	"""Число обращений к базе за один вызов и сам список запросов."""
	with запросы_блока() as запросы:
		действие()
	return len(запросы), запросы
