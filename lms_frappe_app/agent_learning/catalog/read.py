# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Чтение опубликованного каталога компетенций (learning-services#825).

Каталог пишет только публикация (`catalog.service`); здесь — чтение
последнего выпуска.
"""

import json

import frappe

ВЫПУСК = "Agent Competency Catalog Release"
КОМПЕТЕНЦИЯ = "Agent Competency"
ОБЛАСТЬ = "Agent Competency Area"


def имя_выпуска(версия: int) -> str:
	"""Имя записи выпуска каталога версии `версия` — `autoname` доктайпа `catalog-{version}`."""
	return f"catalog-{int(версия)}"


def последний() -> dict:
	"""Последний выпуск каталога: `{catalog_release, version, digest, commit, published_at,
	published_by, catalog, versions}`; `catalog` — снимок выпуска, `versions` — текущая
	версия каждой компетенции. Каталог ещё не публиковали — те же ключи с `None` и `{}`."""
	найдено = frappe.get_all(
		ВЫПУСК,
		fields=["name", "version", "digest", "source_commit", "published_at", "published_by", "snapshot"],
		order_by="version desc",
		limit=1,
	)
	if not найдено:
		return {
			"catalog_release": None,
			"version": None,
			"digest": None,
			"commit": None,
			"published_at": None,
			"published_by": None,
			"catalog": None,
			"versions": {},
		}
	выпуск = найдено[0]
	return {
		"catalog_release": выпуск.name,
		"version": выпуск.version,
		"digest": выпуск.digest,
		"commit": выпуск.source_commit or None,
		"published_at": выпуск.published_at.isoformat(),
		"published_by": выпуск.published_by,
		"catalog": json.loads(выпуск.snapshot),
		"versions": dict(frappe.get_all(КОМПЕТЕНЦИЯ, fields=["name", "version"], as_list=True)),
	}
