# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
import frappe
from frappe.tests import IntegrationTestCase

# (route пункта, заголовок, куда уводит редирект)
ПУНКТЫ = (
	("agent-sidebar", "Подключить ассистента", "/lms/agent"),
	# Чат живёт в MCP-сервисе на том же домене, что и сайт, — как `/mcp`.
	("study-in-browser", "Заниматься в браузере", "/chat"),
	("artifacts-sidebar", "Мои документы", "/lms/documents"),
)


class IntegrationTestSidebarItems(IntegrationTestCase):
	"""Пункты платформы в сайдбаре Frappe Learning и куда они ведут."""

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_пункты_сайдбара_ставятся_один_раз(self):
		from lms_frappe_app.install import обеспечить_пункты_сайдбара

		frappe.set_user("Administrator")
		обеспечить_пункты_сайдбара()
		обеспечить_пункты_сайдбара()
		for route, заголовок, _ in ПУНКТЫ:
			with self.subTest(route=route):
				пункты = frappe.get_all(
					"LMS Sidebar Item",
					{"parenttype": "LMS Settings", "parentfield": "sidebar_items", "route": route},
					["title", "web_page"],
				)
				self.assertEqual(len(пункты), 1)
				self.assertEqual(пункты[0].title, заголовок)
				# Web Page у пункта обязателен, но это заглушка: сайдбар ведёт по её
				# route, а на настоящую страницу уводит редирект. Публиковать нельзя.
				self.assertFalse(frappe.db.get_value("Web Page", пункты[0].web_page, "published"))

	def test_маршруты_пунктов_ведут_на_страницы(self):
		# `route` пункта — это route его Web Page (fetch_from); без редиректа
		# сайдбар привёл бы на неопубликованную заглушку и 404.
		редиректы = {п["source"]: п["target"] for п in frappe.get_hooks("website_redirects")}
		for route, _, куда in ПУНКТЫ:
			with self.subTest(route=route):
				self.assertEqual(редиректы.get(f"/{route}"), куда)

	def test_прежний_адрес_страницы_ведёт_в_learning(self):
		# `/agent` разошёлся по ссылкам и закладкам, пока страница жила на теме сайта.
		редиректы = {п["source"]: п["target"] for п in frappe.get_hooks("website_redirects")}
		self.assertEqual(редиректы.get("/agent"), "/lms/agent")

	# --- веб-чат (lms-platform#141) ---

	def test_состав_пунктов_сайдбара(self):
		# Пин на состав: пункт, добавленный или убранный мимо ревью, виден здесь.
		from lms_frappe_app.install import ПУНКТЫ_САЙДБАРА

		self.assertEqual(
			[п["route"] for п in ПУНКТЫ_САЙДБАРА],
			["study-in-browser", "agent-sidebar", "artifacts-sidebar"],
		)

	# --- порядок и иконки пунктов (lms-platform#311) ---

	def _строки_сайдбара(self) -> list[tuple[str, str]]:
		маршрут = {
			имя: route for имя, route in frappe.get_all("Web Page", fields=["name", "route"], as_list=True)
		}
		return [
			(маршрут.get(строка.web_page), строка.icon)
			for строка in frappe.get_single("LMS Settings").sidebar_items
		]

	def test_пункты_платформы_первыми_по_порядку_и_с_иконками(self):
		from lms_frappe_app.install import ПУНКТЫ_САЙДБАРА, обеспечить_пункты_сайдбара

		frappe.set_user("Administrator")
		настройки = frappe.get_single("LMS Settings")
		# Как на стенде до правки: обратный порядок и иконки в kebab-case,
		# которых десктопный сайдбар не находит.
		прежние = list(reversed(настройки.sidebar_items))
		настройки.set("sidebar_items", [])
		for строка in прежние:
			настройки.append("sidebar_items", {"web_page": строка.web_page, "icon": "bot"})
		настройки.save(ignore_permissions=True)

		обеспечить_пункты_сайдбара()

		self.assertEqual(
			self._строки_сайдбара()[: len(ПУНКТЫ_САЙДБАРА)],
			[(п["route"], п["icon"]) for п in ПУНКТЫ_САЙДБАРА],
		)

	def test_иконка_и_пункт_админа_остаются(self):
		from lms_frappe_app.install import ПУНКТЫ_САЙДБАРА, обеспечить_пункты_сайдбара

		frappe.set_user("Administrator")
		свой = frappe.get_doc(
			{
				"doctype": "Web Page",
				"published": 1,
				"title": f"Правила {frappe.generate_hash(length=6)}",
				"route": f"rules-{frappe.generate_hash(length=6)}",
			}
		).insert(ignore_permissions=True)
		настройки = frappe.get_single("LMS Settings")
		строки = list(настройки.sidebar_items)
		строки[0].icon = "Sparkles"
		настройки.set("sidebar_items", [])
		настройки.append("sidebar_items", {"web_page": свой.name, "icon": "Scale"})
		for строка in строки:
			настройки.append("sidebar_items", {"web_page": строка.web_page, "icon": строка.icon})
		настройки.save(ignore_permissions=True)
		выбранная = (frappe.db.get_value("Web Page", строки[0].web_page, "route"), "Sparkles")

		обеспечить_пункты_сайдбара()

		итог = self._строки_сайдбара()
		self.assertIn(выбранная, итог, "иконку, выбранную админом, установка не трогает")
		self.assertEqual(итог[len(ПУНКТЫ_САЙДБАРА)], (свой.route, "Scale"), "пункт админа — следом за нашими")

	def test_заглушки_не_попадают_в_пути_mcp_сервиса(self):
		"""Traefik сопоставляет пути сервиса mcp по префиксу.

		`Why:` заглушка `/chat-sidebar` уходила в MCP-сервис по правилу `/chat`
		и отвечала «Not Found» вместо переадресации в чат (lms-platform#142).
		"""
		from lms_frappe_app.install import ПУНКТЫ_САЙДБАРА

		пути_mcp = ("mcp", "authoring", "chat", ".well-known")
		for пункт in ПУНКТЫ_САЙДБАРА:
			self.assertFalse(пункт["route"].startswith(пути_mcp), пункт["route"])

	def test_патч_убирает_заглушку_под_путём_чата(self):
		from lms_frappe_app.install import обеспечить_пункты_сайдбара
		from lms_frappe_app.patches.v0_1.chat_sidebar_route import execute

		frappe.set_user("Administrator")
		заглушка = frappe.get_doc(
			{
				"doctype": "Web Page",
				"published": 0,
				"content_type": "Rich Text",
				"main_section": "<p>Заглушка</p>",
				# Заголовок другой: имя Web Page строится из него, а новая
				# заглушка с тем же заголовком уже заведена миграцией. На стенде
				# столкновения нет — патч идёт раньше after_migrate.
				"title": "Заниматься в браузере под путём чата",
				"route": "chat-sidebar",
			}
		).insert(ignore_permissions=True)
		настройки = frappe.get_single("LMS Settings")
		настройки.append("sidebar_items", {"web_page": заглушка.name, "icon": "message-circle"})
		настройки.save(ignore_permissions=True)
		обеспечить_пункты_сайдбара()

		execute()
		execute()  # повторный запуск не падает

		self.assertFalse(frappe.db.exists("Web Page", {"route": "chat-sidebar"}))
		self.assertFalse(frappe.db.exists("LMS Sidebar Item", {"web_page": заглушка.name}))
		self.assertTrue(
			frappe.db.exists("LMS Sidebar Item", {"parenttype": "LMS Settings", "route": "study-in-browser"})
		)

	# --- подпись пункта: правит админ, переименование — патчем (learning-services#479) ---

	def _пункт_ассистента(self) -> str:
		from lms_frappe_app.install import обеспечить_пункты_сайдбара

		frappe.set_user("Administrator")
		обеспечить_пункты_сайдбара()
		заглушка = frappe.db.get_value("Web Page", {"route": "agent-sidebar"})
		# Тесты класса делят базу: подпись, сменённая тестом, вернётся.
		прежняя = frappe.db.get_value("Web Page", заглушка, "title")
		self.addCleanup(self._подписать, заглушка, прежняя)
		return заглушка

	def _подписи(self, заглушка: str) -> tuple[str, str]:
		return (
			frappe.db.get_value("Web Page", заглушка, "title"),
			frappe.db.get_value(
				"LMS Sidebar Item", {"parenttype": "LMS Settings", "web_page": заглушка}, "title"
			),
		)

	def _подписать(self, заглушка: str, подпись: str) -> None:
		frappe.db.set_value("Web Page", заглушка, "title", подпись)
		frappe.db.set_value(
			"LMS Sidebar Item", {"parenttype": "LMS Settings", "web_page": заглушка}, "title", подпись
		)

	def test_патч_переименовывает_прежнюю_подпись(self):
		from lms_frappe_app.patches.v0_1.assistant_sidebar_title import НОВАЯ, ПРЕЖНЯЯ, execute

		заглушка = self._пункт_ассистента()
		self._подписать(заглушка, ПРЕЖНЯЯ)

		execute()
		execute()  # повторный запуск ничего не ломает

		self.assertEqual(self._подписи(заглушка), (НОВАЯ, НОВАЯ))

	def test_подпись_админа_переживает_патч_и_миграцию(self):
		from lms_frappe_app.install import обеспечить_пункты_сайдбара
		from lms_frappe_app.patches.v0_1.assistant_sidebar_title import execute

		заглушка = self._пункт_ассистента()
		self._подписать(заглушка, "Свой ИИ")

		execute()
		обеспечить_пункты_сайдбара()

		self.assertEqual(self._подписи(заглушка), ("Свой ИИ", "Свой ИИ"))
