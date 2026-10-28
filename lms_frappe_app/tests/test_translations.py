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
