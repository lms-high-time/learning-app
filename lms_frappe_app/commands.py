# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Команды bench приложения. Frappe находит их по списку `commands`."""

import sys

import click
import frappe
from frappe.commands import get_site, pass_context


@click.command("check-artifact-catalog")
@pass_context
def проверить_каталог_документов(context):
	"""Проверяет действующие схемы документов курсов движком; беды — выход с кодом 1.

	То же проверяет `after_migrate`; команда — чтобы проверить каталог без
	миграции: на стенде разработчика перед PR, меняющим движок (#377).
	"""
	# Импорт здесь, а не наверху: bench читает этот модуль при каждом
	# запуске любой команды, и ошибка импорта движка сломала бы их все.
	from lms_frappe_app.agent_learning.artifacts.catalog import отчёт

	frappe.init(get_site(context))
	frappe.connect()
	try:
		код = отчёт(click.echo)
	finally:
		frappe.destroy()
	sys.exit(код)


commands = [проверить_каталог_документов]
