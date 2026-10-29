# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Строки интерфейса — с английским исходником и русским переводом.

Исходник строки — английский, как у Frappe и Learning; русский — перевод в
`translations/ru.csv` (learning-services#737). Выбравший на cenda.app
английский видит исходник, остальные — перевод.

`Why:` строка без перевода молча показывается русскому интерфейсу
по-английски, а зашитая по-русски — английскому по-русски. Ни то, ни другое
не видно ни в коде, ни в тестах поведения (learning-services#741).
"""

import ast
import re
from pathlib import Path

import frappe
from frappe.tests import UnitTestCase
from babel.messages.pofile import read_po
from frappe.translate import get_translations_from_csv

ПРИЛОЖЕНИЕ = Path(frappe.get_app_path("lms_frappe_app"))

#: `_("…")`, `_('…')` и `frappe._("…")` с литералом. Склейки сюда не попадают —
#: их ловит ревью; f-строку ловит `ВЫЗОВ_F`: переводу у неё не из чего взяться.
ВЫЗОВ = re.compile(r"""(?:(?<![\w.])|(?<=frappe\.))_\(\s*(["'])((?:\\.|(?!\1)[^\\])*)\1\s*[,)]""")
ВЫЗОВ_F = re.compile(r"""(?:(?<![\w.])|(?<=frappe\.))_\(\s*f["']""")
ПЛЕЙСХОЛДЕР = re.compile(r"\{\d+\}")
КИРИЛЛИЦА = re.compile(r"[А-Яа-яЁё]")

#: Страницы пути входа (learning-services#737): гость видит их на языке,
#: выбранном на сайте, и русского в разметке быть не должно.
СТРАНИЦЫ_ВХОДА = (
	"www/consent.html",
	"www/update-password.html",
	"templates/includes/oauth_confirmation.html",
)
#: Модули, отказы которых человек видит в интерфейсе Learning: SPA показывает
#: `error.message` как есть (learning-services#766). Значение — функции, в
#: которых отказы интерфейса; `None` — весь модуль. Отказы только для агента
#: (урок, квиз, релизы), авторинг и кабинет автора остаются русскими — их
#: модулей и функций здесь нет.
ОТКАЗЫ_ИНТЕРФЕЙСА: dict[str, tuple[str, ...] | None] = {
	"api/__init__.py": None,
	"api/team.py": None,
	"api/review.py": None,
	"api/manager.py": None,
	"api/public.py": None,
	"api/student.py": (
		"enroll",
		"notify_when_released",
		"remember",
		"forget",
		"my_profile",
		"submit_homework",
		"_список_домашки",
		"_требовать_доступ_к_курсу",
		"_курс_урока",
	),
	"agent_learning/doctype/course_allocation/course_allocation.py": None,
	"agent_learning/doctype/organization_membership/organization_membership.py": None,
	"agent_learning/doctype/agent_lesson_homework/agent_lesson_homework.py": ("проверить_срок", "_дата"),
	"agent_learning/browser_progress.py": None,
	"agent_learning/homework.py": None,
	"agent_learning/artifacts/data.py": None,
	"agent_learning/artifacts/values.py": None,
	"agent_learning/artifacts/write.py": None,
	"agent_learning/artifacts/course.py": ("_действующая_схема", "_блок_схемы"),
	"agent_learning/spaces.py": None,
	"agent_learning/programs.py": None,
	"agent_learning/write_limits.py": None,
	"agent_learning/file_guard.py": None,
}
КОММЕНТАРИЙ = re.compile(r"\{#.*?#\}|<!--.*?-->", re.S)
СКРИПТ = re.compile(r"(<script\b[^>]*>)(.*?)</script\b[^>]*>", re.S | re.I)
ВЫРАЖЕНИЕ = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
#: Строковый литерал — в выражениях Jinja и в коде скриптов.
ЛИТЕРАЛ = re.compile(r"""(["'`])((?:\\.|(?!\1)[^\\])*)\1""", re.S)
КОММЕНТАРИЙ_JS = re.compile(r"/\*.*?\*/|(?<![:\\])//[^\n]*", re.S)


def видимый_русский(разметка: str) -> list[str]:
	"""Русский, который увидит человек: текст разметки и строковые литералы
	в выражениях Jinja и в скриптах. Имена переменных и комментарии — нет:
	кириллица в них — соглашение кода, а не текст интерфейса."""
	разметка = КОММЕНТАРИЙ.sub("", разметка)
	найдено = []
	for _тег, код in СКРИПТ.findall(разметка):
		код = КОММЕНТАРИЙ_JS.sub("", код)
		найдено += [строка for _к, строка in ЛИТЕРАЛ.findall(код) if КИРИЛЛИЦА.search(строка)]
	разметка = СКРИПТ.sub("", разметка)
	for выражение in ВЫРАЖЕНИЕ.findall(разметка):
		найдено += [строка for _к, строка in ЛИТЕРАЛ.findall(выражение) if КИРИЛЛИЦА.search(строка)]
	# Куски между `<` и `>`: и текст, и атрибуты тегов — `placeholder`,
	# `aria-label`, `title` человек тоже видит или слышит.
	куски = re.split(r"[<>]", ВЫРАЖЕНИЕ.sub(" ", разметка))
	найдено += [" ".join(кусок.split()) for кусок in куски if КИРИЛЛИЦА.search(кусок)]
	return найдено


def _имя_вызова(вызов: ast.Call) -> str | None:
	if isinstance(вызов.func, ast.Name):
		return вызов.func.id
	if isinstance(вызов.func, ast.Attribute):
		return вызов.func.attr
	return None


def _с_кириллицей(узел: ast.AST) -> bool:
	"""Литерал, f-строка или склейка с русским текстом — не вызов `_()`."""
	if isinstance(узел, ast.Call):
		return False
	return any(
		isinstance(часть, ast.Constant) and isinstance(часть.value, str) and КИРИЛЛИЦА.search(часть.value)
		for часть in ast.walk(узел)
	)


def русские_отказы(исходник: str, функции: tuple[str, ...] | None = None) -> list[str]:
	"""Русский текст там, где его увидит человек: msgid `_()`, сообщение
	`Отказ(код, …)` и `frappe.throw(…)`. `функции` — где искать отказы; `_()`
	с русским msgid ищется во всём модуле."""
	дерево = ast.parse(исходник)
	области = (
		[дерево]
		if функции is None
		else [
			узел
			for узел in ast.walk(дерево)
			if isinstance(узел, ast.FunctionDef | ast.AsyncFunctionDef) and узел.name in функции
		]
	)
	найдено = set()
	for узел in ast.walk(дерево):
		if isinstance(узел, ast.Call) and _имя_вызова(узел) == "_" and узел.args and _с_кириллицей(узел.args[0]):
			найдено.add((узел.lineno, ast.unparse(узел)))
	for область in области:
		for узел in ast.walk(область):
			if not isinstance(узел, ast.Call):
				continue
			имя = _имя_вызова(узел)
			сообщение = (
				узел.args[1]
				if имя == "Отказ" and len(узел.args) > 1
				else узел.args[0]
				if имя == "throw" and узел.args
				else None
			)
			if сообщение is not None and _с_кириллицей(сообщение):
				найдено.add((узел.lineno, ast.unparse(сообщение)))
	return [f"{строка}: {текст}" for строка, текст in sorted(найдено)]


def русские_переводы() -> dict[str, str]:
	"""Русские переводы всех приложений — из исходников: `ru.csv` и `locale/ru.po`.

	`Why:` Frappe читает переводы `.po` только скомпилированными в `.mo`
	(`sites/assets/locale`), а CI их не компилирует: ворота видели бы перевод
	Frappe и Learning на одной машине и не видели на другой."""
	переводы: dict[str, str] = {}
	for приложение in frappe.get_installed_apps():
		файл = Path(frappe.get_app_path(приложение, "locale", "ru.po"))
		if файл.exists():
			with файл.open("rb") as po:
				переводы |= {
					сообщение.id: сообщение.string
					for сообщение in read_po(po)
					if isinstance(сообщение.id, str) and сообщение.string and not сообщение.fuzzy
				}
		переводы |= get_translations_from_csv("ru", приложение) or {}
	return переводы


def исходники():
	for путь in sorted(ПРИЛОЖЕНИЕ.rglob("*")):
		if путь.suffix not in (".py", ".html") or "tests" in путь.parts or путь.name.startswith("test_"):
			continue
		yield путь


def строки_интерфейса() -> dict[str, list[str]]:
	"""Английские литералы `_()` → где встречаются."""
	найдено: dict[str, list[str]] = {}
	for путь in исходники():
		for _кавычка, строка in ВЫЗОВ.findall(путь.read_text(encoding="utf-8")):
			if not КИРИЛЛИЦА.search(строка):
				найдено.setdefault(строка, []).append(str(путь.relative_to(ПРИЛОЖЕНИЕ)))
	return найдено


class UnitTestTranslations(UnitTestCase):
	def test_у_каждой_строки_интерфейса_есть_русский_перевод(self):
		"""Перевод — из `ru.csv` приложения или из переводов Frappe и Learning."""
		перевод = русские_переводы()
		без_перевода = {
			строка: места for строка, места in строки_интерфейса().items() if not перевод.get(строка)
		}
		self.assertEqual(без_перевода, {}, "добавьте строки в translations/ru.csv")

	def test_строки_без_f_строк_в_переводе(self):
		места = [
			str(путь.relative_to(ПРИЛОЖЕНИЕ))
			for путь in исходники()
			if ВЫЗОВ_F.search(путь.read_text(encoding="utf-8"))
		]
		self.assertEqual(места, [], "_(f\"…\") не переводится: плейсхолдеры {0} и .format()")

	def test_перевод_сохраняет_плейсхолдеры(self):
		"""Перевод без `{0}` молча теряет имя агента или ссылку."""
		разные = {
			исходник: перевод
			for исходник, перевод in (get_translations_from_csv("ru", "lms_frappe_app") or {}).items()
			if sorted(ПЛЕЙСХОЛДЕР.findall(исходник)) != sorted(ПЛЕЙСХОЛДЕР.findall(перевод))
		}
		self.assertEqual(разные, {})

	def test_отказы_интерфейса_с_английским_исходником(self):
		"""Английский исходник, русский — перевод в `ru.csv` (learning-services#766)."""
		for имя, функции in ОТКАЗЫ_ИНТЕРФЕЙСА.items():
			with self.subTest(модуль=имя):
				исходник = (ПРИЛОЖЕНИЕ / имя).read_text(encoding="utf-8")
				if функции is not None:
					определены = {
						узел.name for узел in ast.walk(ast.parse(исходник)) if isinstance(узел, ast.FunctionDef)
					}
					self.assertEqual(set(функции) - определены, set(), "функции нет в модуле — поправьте список")
				self.assertEqual(русские_отказы(исходник, функции), [], "текст — _() с английским исходником")

	def test_русские_отказы_находит_литерал_f_строку_и_msgid(self):
		исходник = (
			"def метод():\n"
			'\traise Отказ("code", "Нет курса")\n'
			'\traise Отказ("code", f"Нет {курс}")\n'
			'\traise Отказ("code", _("No course"))\n'
			'\tfrappe.throw(frappe._("Нет доступа"))\n'
			'\tfrappe.throw("Нельзя")\n'
			"def агенту():\n"
			'\traise Отказ("code", "Только агенту")\n'
		)
		self.assertEqual(
			русские_отказы(исходник, ("метод",)),
			[
				"2: 'Нет курса'",
				"3: f'Нет {курс}'",
				"5: frappe._('Нет доступа')",
				"6: 'Нельзя'",
			],
		)

	def test_страницы_входа_без_зашитого_русского(self):
		for имя in СТРАНИЦЫ_ВХОДА:
			with self.subTest(страница=имя):
				строки = видимый_русский((ПРИЛОЖЕНИЕ / имя).read_text(encoding="utf-8"))
				self.assertEqual(строки, [], "строки страницы — через _() с английским исходником")

	def test_видимый_русский_без_имён_и_комментариев(self):
		разметка = (
			'{# комментарий #}<h1>{{ агент or "агента" }}</h1>{% set имя = 1 %}<p>Привет</p>'
			'<input placeholder="Почта">'
			'<script>// комментарий\nconst текст = "Готово"; const ok = "ok";</script>'
		)
		self.assertEqual(видимый_русский(разметка), ["Готово", "агента", "Привет", 'input placeholder="Почта"'])
