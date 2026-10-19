# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Навигация в desk: приложение на стартовом экране, workspace и сайдбар.

`Why:` Frappe 16 держит навигацию на постоянном сайдбаре, который
автогенерируется по модулю и берёт не больше трёх доктайпов, а на стартовый
экран приложение попадает только через `add_to_apps_screen`. Без своих
записей разделы приложения открывались только по прямой ссылке — так их и
нашли на проде.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import создать_ученика

WORKSPACE = "Agent Learning"
МОДУЛЬ = "Agent Learning"


class IntegrationTestDeskNavigation(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def test_workspace_модуля(self):
		"""Точка входа в desk: без неё сайдбару нечего показывать. Карточки ведут
		на доктайпы, на обзоре — вход в кабинет автора (#261) и счёт
		неразобранных репортов."""
		workspace = frappe.get_doc("Workspace", WORKSPACE)

		with self.subTest("публичный"):
			self.assertTrue(workspace.public)
			self.assertEqual(workspace.module, МОДУЛЬ)
			self.assertFalse(workspace.is_hidden)

		with self.subTest("карточки с доктайпами"):
			карточки = [с.label for с in workspace.links if с.type == "Card Break"]
			ссылки = {с.link_to for с in workspace.links if с.type == "Link"}

			self.assertEqual(карточки, ["Курс", "Ученики", "Команды"])
			self.assertIn("Agent Student Artifact", ссылки)
			self.assertIn("Agent Course Report", ссылки)

		with self.subTest("#261: на обзоре вход в кабинет автора"):
			# `Why:` в сайдбар Learning кабинет не положить — пункты сайдбара видят
			# все, и ученики тоже; workspace видят только авторские роли.
			content = json.loads(workspace.content)
			абзацы = [блок["data"]["text"] for блок in content if блок["type"] == "paragraph"]

			self.assertTrue(any('href="/author"' in текст for текст in абзацы), абзацы)

		with self.subTest("на обзоре виден счёт неразобранных репортов"):
			# `Why:` репорт разбирают, только если видят, что он пришёл. Ссылка
			# в карточке ведёт в список, но молчит о том, есть ли там новое, а
			# ярлык со `stats_filter` Frappe считает сам — своего виджета не нужно.
			ярлыки = {я.link_to: я for я in workspace.shortcuts}

			self.assertIn("Agent Course Report", ярлыки)
			фильтр = json.loads(ярлыки["Agent Course Report"].stats_filter)
			self.assertEqual(фильтр, {"status": ["=", "New"]})
			# Статус сверяется со схемой, а не с копией себя: переименованный в
			# доктайпе, он оставит ярлык навсегда на «0 новых», и молча.
			статусы = frappe.get_meta("Agent Course Report").get_field("status").options.split("\n")
			self.assertIn(фильтр["status"][1], статусы)

	def test_сайдбар_модуля(self):
		"""Сайдбар начинается с обзора и ведёт на каждый доктайп и отчёт модуля.
		Пин на состав: новый доктайп без строки в сайдбаре снова станет
		невидимым, и заметят это по прямой ссылке, как в прошлый раз."""
		сайдбар = frappe.get_doc("Workspace Sidebar", WORKSPACE)

		with self.subTest("ведёт на каждый доктайп модуля"):
			ссылки = {п.link_to for п in сайдбар.items if п.type == "Link" and п.link_type == "DocType"}
			доктайпы = set(frappe.get_all("DocType", filters={"module": МОДУЛЬ, "istable": 0}, pluck="name"))

			self.assertTrue(сайдбар.standard)
			self.assertEqual(доктайпы - ссылки, set(), "доктайпы модуля без пункта в сайдбаре")

		with self.subTest("ведёт на каждый отчёт модуля"):
			ссылки = {п.link_to for п in сайдбар.items if п.type == "Link" and п.link_type == "Report"}
			отчёты = set(
				frappe.get_all("Report", filters={"module": МОДУЛЬ, "is_standard": "Yes"}, pluck="name")
			)

			self.assertEqual(отчёты - ссылки, set(), "отчёты модуля без пункта в сайдбаре")
			self.assertIn("Agent Connections", отчёты)

		with self.subTest("начинается с обзора"):
			первый = сайдбар.items[0]

			self.assertEqual((первый.link_type, первый.link_to), ("Workspace", WORKSPACE))

	def test_приложение_объявлено_для_стартового_экрана(self):
		приложения = {п["name"]: п for п in frappe.get_hooks("add_to_apps_screen")}

		self.assertIn("lms_frappe_app", приложения)
		self.assertEqual(приложения["lms_frappe_app"]["route"], "/desk/agent-learning")
		self.assertTrue(frappe.get_attr(приложения["lms_frappe_app"]["has_permission"]))

	def test_стартовый_экран_виден_администратору_и_скрыт_от_ученика(self):
		"""Ученик живёт в интерфейсе Frappe Learning; ссылка в desk для него —
		дверь в 403."""
		from lms_frappe_app.agent_learning.permissions import доступен_desk

		ученик = создать_ученика(f"desk-{frappe.generate_hash(length=6)}@example.com")

		frappe.set_user("Administrator")
		self.assertTrue(доступен_desk())
		frappe.set_user(ученик)
		self.assertFalse(доступен_desk())

	def test_плитка_на_стартовом_экране_ведёт_в_сайдбар_модуля(self):
		"""`Why:` плитки стартового экрана — записи Desktop Icon, и
		автогенерация из add_to_apps_screen для нового приложения при
		миграции не сработала: на проде стартовый экран показывал только
		Framework и Frappe Learning. Плитка типа Link не требует файла
		логотипа, а видна тем, кому доступен хоть один пункт сайдбара."""
		плитка = frappe.get_doc("Desktop Icon", WORKSPACE)

		self.assertTrue(плитка.standard)
		self.assertFalse(плитка.hidden)
		self.assertEqual((плитка.link_type, плитка.link_to), ("Workspace Sidebar", WORKSPACE))

	def test_логотип_отдаётся_методом_с_типом_svg(self):
		"""`Why:` /assets приложения до стенда не доезжают, а logo_url —
		короткое поле: единственный путь для картинки — метод с готовым
		Response, который Frappe отдаёт без JSON-обёртки."""
		from lms_frappe_app.agent_learning.branding import АДРЕС, logo

		ответ = logo()

		self.assertEqual(ответ.mimetype, "image/svg+xml")
		self.assertIn(b"<svg", ответ.get_data())
		self.assertEqual(frappe.get_doc("Desktop Icon", WORKSPACE).logo_url, АДРЕС)
		self.assertEqual(
			next(п for п in frappe.get_hooks("add_to_apps_screen") if п["name"] == "lms_frappe_app")["logo"],
			АДРЕС,
		)
