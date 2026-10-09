app_name = "lms_frappe_app"
app_title = "Agent Learning"
app_publisher = "NikoMusaev"
app_description = "Директивы, сессии и серверный квиз для обучения через MCP-агента"
app_email = "hightimeconsult@gmail.com"
app_license = "agpl-3.0"

# Apps
# ------------------

# Приложение опирается на доменную модель Frappe Learning: релиз строит главы
# и уроки курса записями Learning, пройденные уроки пишутся в LMS Course Progress.
required_apps = ["frappe/lms"]

# Плитка приложения на стартовом экране desk. `Why:` Frappe 16 строит
# навигацию из постоянного сайдбара и стартового экрана; без этого хука
# разделы приложения открывались только по прямой ссылке (#51). Маршрут —
# workspace модуля: сайдбар подхватывается от него.
#
# Логотип отдаёт whitelisted-метод, а не /assets: файлы public/ до стенда не
# доезжают — см. agent_learning/branding.py.
add_to_apps_screen = [
	{
		"name": "lms_frappe_app",
		"logo": "/api/method/lms_frappe_app.agent_learning.branding.logo",
		"title": "Agent Learning",
		"route": "/desk/agent-learning",
		"has_permission": "lms_frappe_app.agent_learning.permissions.доступен_desk",
	}
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/lms_frappe_app/css/lms_frappe_app.css"
# app_include_js = "/assets/lms_frappe_app/js/lms_frappe_app.js"

# include js, css files in header of web template
# web_include_css = "/assets/lms_frappe_app/css/lms_frappe_app.css"
# web_include_js = "/assets/lms_frappe_app/js/lms_frappe_app.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "lms_frappe_app/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# Кнопка «Сбросить прогресс» на записи на курс (learning-services#313).
doctype_js = {"LMS Enrollment": "public/js/lms_enrollment.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "lms_frappe_app/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Куда попадает вошедший человек. `Why:` без этого Frappe доходит до
# последнего фолбэка и показывает страницу настроек `me` — «Edit Profile,
# Reset Password, Manage 3rd party apps», из которой новому ученику некуда
# идти. Роль `LMS Student` заводится с пустым `home_page`, а `frappe/lms`
# домашнюю страницу не задаёт вовсе.
#
# Правкой Website Settings это не чинится: настройка в базе не переживает
# пересоздания сайта — та же причина, по которой ключ входа через Google
# приезжает кодом, а не кликами.
#
# Функцией, а не строкой: `get_home_page_via_hooks` не смотрит на тип
# пользователя, и строковый вариант уводил в `/lms` администратора — тот
# переставал попадать в desk после входа.
get_website_user_home_page = "lms_frappe_app.www.home.домашняя_страница"

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Пункт сайдбара Frappe Learning ведёт по маршруту своей Web Page (fetch_from),
# а страницы «Подключить ассистента» и «Мои документы» живут в SPA Learning
# (learning-services#331, #470), веб-чат — в MCP-сервисе на том же домене.
# Редирект отрабатывает до выбора страницы, поэтому заглушки публиковать не
# нужно. `/agent` — адрес, который уже разошёлся по ссылкам и закладкам.
website_redirects = [
	{"source": "/agent-sidebar", "target": "/lms/agent"},
	{"source": "/agent", "target": "/lms/agent", "forward_query_parameters": True},
	{"source": "/study-in-browser", "target": "/chat"},
	{"source": "/artifacts-sidebar", "target": "/lms/documents"},
]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "lms_frappe_app.utils.jinja_methods",
# 	"filters": "lms_frappe_app.utils.jinja_filters"
# }

# Права
# ------------------
# Изоляция занятий держится на правах Frappe, а не на проверках в вызывающем
# коде: и браузер, и MCP-сервис ходят от имени ученика, поэтому чужое занятие
# отклоняется одинаково в обоих каналах, без дублирования логики.

_сессия = "lms_frappe_app.agent_learning.doctype.agent_learning_session.agent_learning_session"
_права = "lms_frappe_app.agent_learning.permissions"

permission_query_conditions = {
	"Agent Learning Session": f"{_права}.условие_занятия",
	"Agent Session Event": f"{_права}.условие_события",
	"Agent Quiz Attempt": f"{_права}.условие_попытки",
	"Agent Quiz Answer": f"{_права}.условие_ответа",
	"Organization Membership": f"{_права}.условие_членства",
	"Course Allocation": f"{_права}.условие_назначения",
	"Agent Student Note": f"{_права}.условие_заметки",
	"Agent Student Artifact": f"{_права}.условие_артефакта",
	"Agent Homework Submission": f"{_права}.условие_сдачи",
}

has_permission = {
	"Agent Learning Session": f"{_права}.доступно_занятие",
	"Agent Session Event": f"{_права}.доступно_событие",
	"Agent Quiz Attempt": f"{_права}.доступна_попытка",
	"Agent Quiz Answer": f"{_права}.доступен_ответ",
	"Organization Membership": f"{_права}.доступно_членство",
	"Course Allocation": f"{_права}.доступно_назначение",
	"Agent Student Note": f"{_права}.доступна_заметка",
	"Agent Student Artifact": f"{_права}.доступен_артефакт",
	"Agent Homework Submission": f"{_права}.доступна_сдача",
}

# Роли ставятся вместе с приложением: без них права на DocType ссылались бы
# на несуществующие роли, и Frappe молча отдал бы доступ никому.
fixtures = [
	{
		"dt": "Role",
		"filters": [
			["name", "in", ["Organization Manager", "Organization Admin", "Agent Service"]]
		],
	},
	# Зачин урока и обещание курса адресованы ученику, а директивы — агенту,
	# поэтому поля живут на самих DocType Learning, а не в директивах (#238,
	# решение 1Б). Цена: это первые наши поля на чужих DocType, и
	# версионируются они вместе с уроком, а не с редакцией директивы.
	# Тестовая запись — отметка на самой записи на курс: доступ по-прежнему даёт
	# запись, второго основания доступа нет (learning-services#393).
	# Ключ, действующий релиз и атрибуция курса, описание главы — из релиза
	# (learning-services#500). Цели анонса — у курса без релиза (learning-services#512).
	# Ключи глав и уроков из релиза — на самих записях (learning-services#514).
	{
		"dt": "Custom Field",
		"filters": [
			[
				"name",
				"in",
				[
					"Course Lesson-lesson_hook",
					"LMS Course-course_promise",
					"LMS Enrollment-agent_tester",
					"LMS Course-course_key",
					"LMS Course-active_release",
					"LMS Course-course_attribution",
					"LMS Course-announce_objectives",
					"Course Chapter-chapter_description",
					"Course Chapter-chapter_key",
					"Course Lesson-lesson_key",
				],
			]
		],
	},
]

# Фоновые задачи
# ------------------
# Раз в час: занятия, брошенные посреди урока, закрываются сами. Иначе ученик,
# закрывший ноутбук, навсегда остаётся «в процессе» и портит отчётность.

_назначение = "lms_frappe_app.agent_learning.doctype.course_allocation.course_allocation"

scheduler_events = {
	"hourly": [
		f"{_сессия}.закрыть_брошенные_занятия",
		# Письма домашки: напоминание, «вернули», дайджест куратору (learning-services#452).
		"lms_frappe_app.agent_learning.homework_notices.разослать",
	],
	# Страховка к хуку на вступление: членство может появиться в обход него —
	# импортом, миграцией или правкой в базе.
	"daily": [
		f"{_назначение}.сверить_зачисления",
		# Письмо непрошедшим, когда срок назначения близко (#365).
		"lms_frappe_app.agent_learning.notices.напомнить_о_сроках",
	],
}

# Installation
# ------------

# before_install = "lms_frappe_app.install.before_install"
# Пункты приложения в сайдбаре Frappe Learning — и при установке, и при
# каждой миграции: на уже развёрнутом стенде after_install не сработает.
# Проверка каталога документов — последней: сломанный каталог валит
# `bench migrate`, и выкатка останавливается до переключения (#377).
after_install = "lms_frappe_app.install.after_install"
# Индексы по полям фикстур: при установке фикстуры синхронизируются после
# `after_install`, а `after_sync` — следом за ними.
after_sync = "lms_frappe_app.install.after_sync"
after_migrate = [
	"lms_frappe_app.install.after_migrate",
	"lms_frappe_app.agent_learning.artifacts.catalog.после_миграции",
]

# Uninstallation
# ------------

# before_uninstall = "lms_frappe_app.uninstall.before_uninstall"
# after_uninstall = "lms_frappe_app.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "lms_frappe_app.utils.before_app_install"
# after_app_install = "lms_frappe_app.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "lms_frappe_app.utils.before_app_uninstall"
# after_app_uninstall = "lms_frappe_app.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "lms_frappe_app.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "lms_frappe_app.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["lms_frappe_app.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# OAuth-клиентов агенты регистрируют сами, а Frappe разрешает их только
# Desk User — ученик без desk-доступа не мог авторизовать агента. Хук идёт
# после validate Frappe и добавляет роли платформы (#26).
doc_events = {
	"OAuth Client": {
		"validate": "lms_frappe_app.agent_learning.oauth_client.разрешить_роли_платформы",
	},
	# Ключ курса, действующий релиз и порядок глав курса из релиза ставит только
	# публикация релиза (learning-services#500, #512).
	"LMS Course": {
		"validate": "lms_frappe_app.agent_learning.releases.course_guard.проверить_курс",
		"before_rename": "lms_frappe_app.agent_learning.releases.course_guard.проверить_переименование",
	},
	# Главы и уроки курса из релиза правит только новый релиз (learning-services#512);
	# пустой ключ релиза на записи — NULL (learning-services#514).
	"Course Chapter": {
		"validate": [
			"lms_frappe_app.agent_learning.releases.course_guard.проверить_структуру",
			"lms_frappe_app.agent_learning.releases.projection.ключ_только_из_релиза",
		],
		"on_trash": "lms_frappe_app.agent_learning.releases.course_guard.проверить_структуру",
		"before_rename": "lms_frappe_app.agent_learning.releases.course_guard.проверить_переименование",
	},
	"Course Lesson": {
		"validate": [
			"lms_frappe_app.agent_learning.releases.course_guard.проверить_структуру",
			"lms_frappe_app.agent_learning.releases.projection.ключ_только_из_релиза",
		],
		"on_trash": "lms_frappe_app.agent_learning.releases.course_guard.проверить_структуру",
		"before_rename": "lms_frappe_app.agent_learning.releases.course_guard.проверить_переименование",
	},
	# Строки оглавления удаляются и сами по себе — Desk и `delete_documents` Learning.
	"Chapter Reference": {
		"on_trash": "lms_frappe_app.agent_learning.releases.course_guard.проверить_ссылку",
	},
	"Lesson Reference": {
		"on_trash": "lms_frappe_app.agent_learning.releases.course_guard.проверить_ссылку",
	},
	# Домашки уроков и схему документа курса из релиза пишет только публикация
	# (learning-services#526).
	"Agent Lesson Homework": {
		"validate": "lms_frappe_app.agent_learning.releases.course_guard.проверить_домашку",
		"on_trash": "lms_frappe_app.agent_learning.releases.course_guard.проверить_домашку",
	},
	"Agent Course Artifact": {
		"validate": "lms_frappe_app.agent_learning.releases.course_guard.проверить_документ",
		"on_trash": "lms_frappe_app.agent_learning.releases.course_guard.проверить_документ",
		"before_rename": "lms_frappe_app.agent_learning.releases.course_guard.проверить_переименование",
	},
	"Agent Artifact Block": {
		"on_trash": "lms_frappe_app.agent_learning.releases.course_guard.проверить_блок",
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"lms_frappe_app.tasks.all"
# 	],
# 	"daily": [
# 		"lms_frappe_app.tasks.daily"
# 	],
# 	"hourly": [
# 		"lms_frappe_app.tasks.hourly"
# 	],
# 	"weekly": [
# 		"lms_frappe_app.tasks.weekly"
# 	],
# 	"monthly": [
# 		"lms_frappe_app.tasks.monthly"
# 	],
# }

# Testing
# -------

# Индексация поиска Learning на время тестов выключается — см. testing.py.
before_tests = "lms_frappe_app.testing.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "lms_frappe_app.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------

_редактор = "lms_frappe_app.agent_learning.releases.learning_editor"

# Урок закрывает занятие с агентом, а не время на странице урока —
# обоснование в модуле (lms-platform#305).
override_whitelisted_methods = {
	"lms.lms.doctype.course_lesson.course_lesson.save_progress": (
		"lms_frappe_app.agent_learning.browser_progress.save_progress"
	),
	# Вход по почте (learning-services#460): занятый адрес не тупик, смена
	# пароля подтверждается письмом — обоснование в `access.py`.
	"frappe.core.doctype.user.user.sign_up": "lms_frappe_app.access.sign_up",
	"lms.lms.user.sign_up": "lms_frappe_app.access.sign_up_learning",
	"frappe.core.doctype.user.user.update_password": "lms_frappe_app.access.update_password",
	# Редактор Learning не правит главы и уроки курса из релиза: порядок он пишет
	# мимо `validate` — обоснование в модуле (learning-services#512).
	"lms.lms.api.delete_chapter": f"{_редактор}.delete_chapter",
	"lms.lms.api.update_lesson_index": f"{_редактор}.update_lesson_index",
	"lms.lms.api.update_chapter_index": f"{_редактор}.update_chapter_index",
	"lms.lms.api.delete_lesson": f"{_редактор}.delete_lesson",
	"lms.lms.api.create_lesson": f"{_редактор}.create_lesson",
	"lms.lms.api.upsert_chapter": f"{_редактор}.upsert_chapter",
}
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "lms_frappe_app.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["lms_frappe_app.utils.before_request"]
# after_request = ["lms_frappe_app.utils.after_request"]

# Job Events
# ----------
# before_job = ["lms_frappe_app.utils.before_job"]
# after_job = ["lms_frappe_app.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"lms_frappe_app.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

