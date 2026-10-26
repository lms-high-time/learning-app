# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Заголовок вкладки на страницах приложения (learning-services#693)."""

import re
from pathlib import Path

import jinja2
from frappe.tests import UnitTestCase

СТРАНИЦЫ = Path(__file__).resolve().parents[1] / "www"
ЭКРАНИРУЮЩИЕ = frozenset({"e", "escape"})
#: Шаблоны Frappe с блоком `title` через `striptags`: `web.html` наследует его
#: у `base.html`, и страница может наследовать любой из них.
БАЗОВЫЕ = re.compile(r"""{%-?\s*extends\s+["']templates/(web|base)\.html["']""")


def заголовок_экранирован(исходник: str) -> bool:
	"""Свой блок `title` есть, и каждый вывод в нём — литерал или `| e` на вершине."""
	узлы = jinja2.nodes
	блоки = [б for б in jinja2.Environment().parse(исходник).find_all(узлы.Block) if б.name == "title"]
	if not блоки:
		return False
	for вывод in блоки[0].find_all(узлы.Output):
		for узел in вывод.nodes:
			if isinstance(узел, (узлы.TemplateData, узлы.Const)):
				continue
			if not (isinstance(узел, узлы.Filter) and узел.name in ЭКРАНИРУЮЩИЕ):
				return False
	return True


class UnitTestPageTitles(UnitTestCase):
	"""Каждая страница `www/` на базовом шаблоне Frappe перекрывает его блок `title`.

	`Why:` базовый шаблон Frappe выводит `{{ title | striptags }}`, а
	`striptags` раскодирует сущности и не экранирует результат.
	"""

	def test_страницы_перекрывают_заголовок(self):
		страницы = sorted(СТРАНИЦЫ.glob("*.html"))
		self.assertTrue(страницы)
		for путь in страницы:
			текст = путь.read_text(encoding="utf-8")
			if not БАЗОВЫЕ.search(текст):
				continue
			with self.subTest(путь.name):
				self.assertTrue(заголовок_экранирован(текст))

	def test_страница_на_любом_базовом_шаблоне_проверяется(self):
		for исходник in ('{% extends "templates/web.html" %}', "{%- extends 'templates/base.html' %}"):
			with self.subTest(исходник):
				self.assertTrue(БАЗОВЫЕ.search(исходник))
		self.assertFalse(БАЗОВЫЕ.search('{% extends "lms_frappe_app/templates/x.html" %}'))

	def test_правило_ловит_заголовок_без_экранирования(self):
		случаи = {
			"{% block title %}{{ title | e }}{% endblock %}": True,
			"{% block title %}Кабинет — {{ title | e }}{% endblock %}": True,
			"{% block title %}{{ title }}{% endblock %}": False,
			"{% block title %}{{ title | striptags }}{% endblock %}": False,
			"{% block title %}{{ a ~ title | e }}{% endblock %}": False,
			"{% block page_content %}{{ title | e }}{% endblock %}": False,
		}
		for исходник, итог in случаи.items():
			with self.subTest(исходник):
				self.assertEqual(заголовок_экранирован(исходник), итог)
