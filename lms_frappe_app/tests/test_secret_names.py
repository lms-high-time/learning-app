# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Ворота: секреты не лежат в переменных с кириллическим именем
(learning-services#664).

`Why:` Frappe пишет в Error Log и Sentry трассировку с переменными каждого
кадра (`frappe.get_traceback(with_context=True)`) и прячет значение, только
если в имени переменной есть латинское `password`, `secret`, `token`, `key`
или `pwd`. Переменная `пароль` уходит в журнал как есть.
"""

import ast
import re
from pathlib import Path

import frappe
from frappe.tests import UnitTestCase

ПРИЛОЖЕНИЕ = Path(frappe.get_app_path("lms_frappe_app"))

#: Во всём приложении: пароль, секрет и токен называются латиницей. Константы
#: (`НЕВЕРНЫЙ_ПАРОЛЬ` — код отказа) написаны заглавными и не задеваются.
ВЕЗДЕ = re.compile(r"парол|секрет|токен")
#: В модулях OAuth ещё и код авторизации: `code_token`, а не `код`.
OAUTH = (
	"agent_learning/oauth_server.py",
	"agent_learning/oauth_client.py",
	"agent_learning/cimd.py",
	"google_login.py",
)
КОД = re.compile(r"^коды?$")


def имена(дерево: ast.AST):
	"""Имена параметров и переменных, которым присваивается значение, — те,
	что попадают в кадр трассировки."""
	for узел in ast.walk(дерево):
		if isinstance(узел, ast.arg):
			yield узел.arg, узел.lineno
		elif isinstance(узел, ast.Name) and isinstance(узел.ctx, ast.Store):
			yield узел.id, узел.lineno
		elif isinstance(узел, ast.ExceptHandler) and узел.name:
			yield узел.name, узел.lineno


def нарушения(путь: Path, шаблоны) -> list[str]:
	дерево = ast.parse(путь.read_text(encoding="utf-8"))
	return [
		f"{путь.relative_to(ПРИЛОЖЕНИЕ)}:{строка} {имя}"
		for имя, строка in имена(дерево)
		if any(ш.search(имя) for ш in шаблоны)
	]


class UnitTestSecretNames(UnitTestCase):
	def test_секреты_названы_латиницей(self):
		найдено = []
		for путь in sorted(ПРИЛОЖЕНИЕ.rglob("*.py")):
			if путь.name.startswith("test_") or "tests" in путь.parts:
				continue
			шаблоны = [ВЕЗДЕ]
			if str(путь.relative_to(ПРИЛОЖЕНИЕ)) in OAUTH:
				шаблоны.append(КОД)
			найдено += нарушения(путь, шаблоны)
		self.assertFalse(
			найдено,
			"Значение этих переменных Frappe не спрячет в трассировке — назовите "
			"их латиницей с password/secret/token/key:\n" + "\n".join(найдено),
		)

	def test_ворота_видят_кириллический_секрет(self):
		дерево = ast.parse("def f(пароль):\n\tкод = 1\n\tfor токен in []: pass\n")
		self.assertEqual(
			{имя for имя, _ in имена(дерево) if ВЕЗДЕ.search(имя) or КОД.search(имя)},
			{"пароль", "код", "токен"},
		)
