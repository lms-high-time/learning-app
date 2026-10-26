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
# Вход, регистрация и смена пароля — в стиле публичного сайта; правила файла
# заперты на адресах этих страниц, остальные веб-страницы он не трогает.
web_include_css = "/assets/lms_frappe_app/css/high-time-web.css"
# Подпись о согласии с Условиями и Политикой в карточках входа и регистрации
# (learning-services#577); на других страницах скрипт ничего не делает.
# Согласие на аналитику — общий скрипт публичного сайта на том же домене
# (learning-services#582): баннер и выбор одни на весь cenda.app.
web_include_js = [
	"/assets/lms_frappe_app/js/consent-notice.js",
	"/site/analytics.js",
]

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
# (learning-services#331, #470), веб-чат — в MCP-сервисе на том же домене,
# «Агенты и сессии» — страница приложения `/connections` (learning-services
# #669). Редирект отрабатывает до выбора страницы, поэтому заглушки
# публиковать не нужно. `/agent` — адрес, который уже разошёлся по ссылкам и закладкам.
website_redirects = [
	{"source": "/agent-sidebar", "target": "/lms/agent"},
	{"source": "/agent", "target": "/lms/agent", "forward_query_parameters": True},
	{"source": "/study-in-browser", "target": "/chat"},
	{"source": "/artifacts-sidebar", "target": "/lms/documents"},
	{"source": "/connections-sidebar", "target": "/connections"},
]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# Логотип сайта — в шаблоны, а не путь к файлу: экран подтверждения OAuth
# (templates/includes/oauth_confirmation.html) берёт тот же логотип, что вход,
# и на сайте без загруженного логотипа не показывает битую картинку
# (learning-services#622).
jinja = {
	"methods": [
		"frappe.core.doctype.navbar_settings.navbar_settings.get_app_logo",
		# Хост адреса возврата на экране подтверждения OAuth (learning-services#661).
		"lms_frappe_app.agent_learning.oauth_server.возврат_подключения",
	],
}

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
	"Course Chapter": f"{_права}.условие_главы",
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
	"Course Chapter": f"{_права}.доступна_глава",
}

# Роли ставятся вместе с приложением: без них права на DocType ссылались бы
# на несуществующие роли, и Frappe молча отдал бы доступ никому. Release
# Publisher — служебная учётка конвейера курсов (learning-services#685).
fixtures = [
	{
		"dt": "Role",
		"filters": [
			[
				"name",
				"in",
				["Organization Manager", "Organization Admin", "Agent Service", "Release Publisher"],
			]
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
	# Зачин, обещание и описание главы хранятся как есть, без очистки Frappe
	# (`ignore_xss_filter`, learning-services#521), и скрыты из отчётов и печати
	# Desk: там Frappe выводит их без экранирования.
	# Семья токена OAuth — на самом токене: Frappe удаляет отозванные токены
	# каждый час, и ссылка на первый токен семьи пропала бы (learning-services#665).
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
					"OAuth Bearer Token-lms_token_family",
					"OAuth Bearer Token-lms_family_started",
					"OAuth Bearer Token-lms_previous_refresh",
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
		# Новые аккаунты без согласия за 30 дней — удаляются (learning-services#624).
		"lms_frappe_app.agent_learning.consent.удалить_без_согласия",
		# Записи согласия удалённых аккаунтов — через 3 года (learning-services#689).
		"lms_frappe_app.agent_learning.consent.удалить_старые_согласия",
	],
}

# Installation
# ------------

# before_install = "lms_frappe_app.install.before_install"
# Пункты приложения в сайдбаре Frappe Learning — и при установке, и при
# каждой миграции: на уже развёрнутом стенде after_install не сработает.
after_install = "lms_frappe_app.install.after_install"
# Индексы по полям фикстур: при установке фикстуры синхронизируются после
# `after_install`, а `after_sync` — следом за ними.
after_sync = "lms_frappe_app.install.after_sync"
# Сайт с флагом разработки не мигрирует — до схемы и патчей (learning-services#691).
before_migrate = ["lms_frappe_app.install.проверить_флаги_разработки"]
after_migrate = ["lms_frappe_app.install.after_migrate"]

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

doc_events = {
	# Текст с тегом, который Frappe не чистит от HTML — JSON или с незакрытым
	# тегом, — хук чистит в доктайпах, куда пишут методы приложения
	# (learning-services#527).
	"*": {
		"before_save": "lms_frappe_app.agent_learning.html_text.очистить",
	},
	# OAuth-клиентов агенты регистрируют сами, а Frappe разрешает их только
	# Desk User — ученик без desk-доступа не мог авторизовать агента. Хук идёт
	# после validate Frappe и добавляет роли платформы (#26).
	"OAuth Client": {
		"validate": "lms_frappe_app.agent_learning.oauth_client.разрешить_роли_платформы",
	},
	# Самостоятельная регистрация — формой или первым входом через Google —
	# записывает согласие с документами (learning-services#577).
	# Смена пароля в desk, отключение и удаление аккаунта отзывают его токены
	# OAuth (learning-services#631, #665); смену по ссылке — `access.update_password`.
	"User": {
		"after_insert": "lms_frappe_app.agent_learning.consent.записать_согласие",
		# Длина, контекстные слова и утечки пароля из desk и REST (learning-services#666);
		# смена пароля замечается для отзыва токенов (learning-services#665).
		"before_validate": [
			"lms_frappe_app.passwords.проверить_пароль_пользователя",
			"lms_frappe_app.agent_learning.oauth_client.заметить_смену_пароля",
		],
		"on_update": "lms_frappe_app.agent_learning.oauth_client.отозвать_при_смене_доступа",
		"on_trash": "lms_frappe_app.agent_learning.oauth_client.удалить_токены",
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
	# Строки оглавления вставляются и удаляются и сами по себе — `/api/resource`,
	# Desk и `delete_documents` Learning.
	"Chapter Reference": {
		"validate": "lms_frappe_app.agent_learning.releases.course_guard.проверить_ссылку",
		"on_trash": "lms_frappe_app.agent_learning.releases.course_guard.проверить_ссылку",
	},
	"Lesson Reference": {
		"validate": "lms_frappe_app.agent_learning.releases.course_guard.проверить_ссылку",
		"on_trash": "lms_frappe_app.agent_learning.releases.course_guard.проверить_ссылку",
	},
	# Поля зачисления пишет код платформы (learning-services#674). Прогресс
	# курса из релиза — по урокам программы: пройденный урок, снятый из релиза,
	# Learning засчитывал бы (learning-services#522).
	"LMS Enrollment": {
		"validate": "lms_frappe_app.agent_learning.enrollment_guard.проверить_зачисление",
		"on_update": "lms_frappe_app.agent_learning.course_progress.сверить",
	},
	# Отметка урока, один ключ на две ишью:
	# - learning-services#525: урок курса из релиза закрывает только занятие —
	#   Learning пишет отметку и прямыми вызовами Python, мимо подмены методов.
	#   `проверить_отметку` — первым: отказ раньше, чем #522 пометит снятие.
	# - learning-services#522: снятый пройденный урок опускает и долю, достигшую
	#   100: сброс прогресса даёт 0, а не 100.
	"LMS Course Progress": {
		"validate": [
			"lms_frappe_app.agent_learning.browser_progress.проверить_отметку",
			"lms_frappe_app.agent_learning.course_progress.отметить_снятие",
		],
		"on_trash": "lms_frappe_app.agent_learning.course_progress.отметить_снятие",
		"on_update": "lms_frappe_app.agent_learning.course_progress.снять_отметку",
		"after_delete": "lms_frappe_app.agent_learning.course_progress.снять_отметку",
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
		"validate": "lms_frappe_app.agent_learning.releases.course_guard.проверить_блок",
		"on_trash": "lms_frappe_app.agent_learning.releases.course_guard.проверить_блок",
	},
	# Сроки журналов — из политики конфиденциальности, правкой в Desk не
	# меняются; ежедневная чистка Frappe сохраняет настройки перед удалением
	# старых записей и тоже проходит через этот хук (learning-services#689).
	"Log Settings": {
		"validate": "lms_frappe_app.personal_data.закрепить_сроки_журналов",
	},
	# Копия данных и удаление аккаунта — по письму, процедурой приложения;
	# штатные запросы Frappe закрыты (learning-services#689).
	"Personal Data Deletion Request": {
		"before_insert": "lms_frappe_app.personal_data.отказать_штатному_запросу",
	},
	"Personal Data Download Request": {
		"before_insert": "lms_frappe_app.personal_data.отказать_штатному_запросу",
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
# Запись в группу Learning заводит зачисления на курсы группы под меткой,
# только на шаге после проверок группы (learning-services#674). Адрес файла и
# файлы сдачи домашки меняет только System Manager (learning-services#683).
# Примесь, а не `doc_events`: проверка обязана идти до метода контроллера
# `File`, который сразу трогает диск, — см. модуль.
extend_doctype_class = {
	"LMS Batch Enrollment": "lms_frappe_app.agent_learning.enrollment_guard.ЗаписьВГруппу",
	"File": "lms_frappe_app.agent_learning.file_guard.ФайлПлатформы",
}

# Overriding Methods
# ------------------------------

_редактор = "lms_frappe_app.agent_learning.releases.learning_editor"

_прогресс = "lms_frappe_app.agent_learning.browser_progress.save_progress"
_oauth = "lms_frappe_app.agent_learning.oauth_server"
_чтения = "lms_frappe_app.agent_learning.learning_reads"
_методы_чтения = (
	"get_course_outline",
	"get_lesson",
	"get_courses",
	"get_course_count",
	"get_reviews",
	"get_batches",
	"get_batch_count",
	"get_chart_data",
	"get_course_completion_data",
)
_методы_oauth = ("authorize", "approve", "get_token", "revoke_token", "introspect_token", "register_client")

# Урок закрывает занятие с агентом, а не время на странице урока —
# обоснование в модуле (lms-platform#305). Подмена сверяет строку вызова, а
# Learning импортирует `save_progress` ещё в два модуля: подменено каждое имя
# (learning-services#525).
override_whitelisted_methods = {
	"lms.lms.doctype.course_lesson.course_lesson.save_progress": _прогресс,
	"lms.lms.api.save_progress": _прогресс,
	"lms.lms.doctype.lms_quiz.lms_quiz.save_progress": _прогресс,
	# Вход по почте (learning-services#460): занятый адрес не тупик, смена
	# пароля подтверждается письмом — обоснование в `access.py`.
	"frappe.core.doctype.user.user.sign_up": "lms_frappe_app.access.sign_up",
	"lms.lms.user.sign_up": "lms_frappe_app.access.sign_up_learning",
	"frappe.core.doctype.user.user.update_password": "lms_frappe_app.access.update_password",
	# Приглашение Frappe принимается без входа под приглашённым (learning-services#691).
	"frappe.core.api.user_invitation.accept_invitation": "lms_frappe_app.access.accept_invitation",
	# Редактор Learning не правит главы и уроки курса из релиза: порядок он пишет
	# мимо `validate` — обоснование в модуле (learning-services#512).
	"lms.lms.api.delete_chapter": f"{_редактор}.delete_chapter",
	"lms.lms.api.update_lesson_index": f"{_редактор}.update_lesson_index",
	"lms.lms.api.update_chapter_index": f"{_редактор}.update_chapter_index",
	"lms.lms.api.delete_lesson": f"{_редактор}.delete_lesson",
	"lms.lms.api.create_lesson": f"{_редактор}.create_lesson",
	"lms.lms.api.upsert_chapter": f"{_редактор}.upsert_chapter",
	# Эндпоинты OAuth — с валидатором платформы: клиенты по адресу документа,
	# локальный адрес возврата с любым портом, `invalid_grant` на мёртвый токен
	# обновления (learning-services#661); секрет клиента, PKCE S256, срок кода,
	# семьи токенов, интроспекция только клиенту с секретом, область и предел
	# регистрации (learning-services#665) — обоснование в модуле.
	**{f"frappe.integrations.oauth2.{метод}": f"{_oauth}.{метод}" for метод in _методы_oauth},
	# Вход через Google — только с подтверждённым адресом и тем же аккаунтом
	# Google, что при первом входе: все три пути к входу Frappe через Google —
	# обоснование в модуле (learning-services#666).
	"frappe.integrations.oauth2_logins.login_via_google": "lms_frappe_app.google_login.login_via_google",
	"frappe.www.login.login_via_google": "lms_frappe_app.google_login.login_via_google",
	"frappe.integrations.oauth2_logins.custom": "lms_frappe_app.google_login.custom",
	# Прочие провайдеры входа — отказ: вход через внешний провайдер только
	# Google (learning-services#691) — обоснование в модуле. Список — копия
	# `google_login.ПРОВАЙДЕРЫ_FRAPPE`: импорт модулей приложения в hooks
	# тянул бы Frappe при чтении хуков; расхождение ловит тест.
	**{
		f"{модуль}.login_via_{провайдер}": "lms_frappe_app.google_login.отклонить_провайдера"
		for модуль in ("frappe.integrations.oauth2_logins", "frappe.www.login")
		for провайдер in ("github", "facebook", "frappe", "office365", "salesforce", "fairlogin", "keycloak")
	},
	# Версии приложений — только пользователю Desk (learning-services#692).
	"frappe.utils.change_log.get_versions": "lms_frappe_app.versions.get_versions",
	# Оглавление, урок, отзывы, каталог и группы Learning — по правилу витрины,
	# статистика — персоналу: обоснование в модуле (learning-services#692).
	**{f"lms.lms.utils.{метод}": f"{_чтения}.{метод}" for метод in _методы_чтения},
	"lms.lms.api.get_chart_details": f"{_чтения}.get_chart_details",
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

# Запись согласия — доказательство на случай спора и переживает удаление
# пользователя; без этой строки ссылка из неё запрещала бы удалить любого, кто
# зарегистрировался сам (learning-services#577). Удаление по запросу на
# стирание данных Frappe делает переименованием, и ссылка переезжает на
# анонимное имя.
ignore_links_on_delete = ["Agent Consent"]

# Страница SPA Learning: мета-теги курса и группы вне витрины и версия Frappe —
# по правилу витрины (learning-services#692).
update_website_context = ["lms_frappe_app.agent_learning.learning_reads.страница_learning"]

# Request Events
# ----------------
# Без явного согласия с документами страницы платформы ведут на экран
# согласия (learning-services#624): вошедшие через Google и прежние
# пользователи отмечают «мне есть 18» и «принимаю документы» один раз.
#
# `sid` в параметрах запроса — отказ, и первым: переадресация следующих хуков
# поставила бы cookie сессии из параметра (learning-services#666).
#
# Абсолютный срок сессии, поднятой из базы, — следом: Frappe сверяет его
# только у сессии из кэша (learning-services#691).
#
# Оригиналы подменённых методов снимаются из реестра whitelisted на каждом
# запросе и задаче: иначе до них доходят под другим путём модуля
# (learning-services#692).
before_request = [
	"lms_frappe_app.session_guard.отклонить_sid_из_запроса",
	"lms_frappe_app.session_guard.завершить_истёкшую",
	"lms_frappe_app.whitelist_guard.снять_подменённые",
	"lms_frappe_app.agent_learning.consent.потребовать_согласие",
]
# Выход очищает данные сайта в браузере, приватные файлы не кэшируются
# (learning-services#688).
after_request = [
	"lms_frappe_app.session_guard.не_ставить_cookie",
	"lms_frappe_app.browser_data.заголовки_ответа",
]

# Session Events
# --------------
# Выход из Cenda отзывает токены веб-чата: у чата свой вход, и без этого он
# переживал «Выйти» (learning-services#656). Второй хук запоминает, кто
# вышел, — для очистки данных сайта в браузере (learning-services#688).
on_logout = [
	"lms_frappe_app.agent_learning.oauth_client.отозвать_чат_при_выходе",
	"lms_frappe_app.browser_data.запомнить_выход",
]
# Абсолютный срок сессии от входа (learning-services#666).
on_session_creation = "lms_frappe_app.session_guard.ограничить_срок_сессии"

# Job Events
# ----------
before_job = ["lms_frappe_app.whitelist_guard.снять_подменённые"]
# after_job = ["lms_frappe_app.utils.after_job"]

# User Data Protection
# --------------------
# Реестр данных пользователя (learning-services#689): поля доктайпов
# приложения, Learning и Frappe, в которых стоит пользователь. Его читают выгрузка
# Frappe (`Personal Data Download Request`) и процедура приложения
# `lms_frappe_app.personal_data` — выгрузка и удаление данных ученика.
# Ключ `mode` — её: `delete` — запись удаляется (строка дочерней таблицы —
# из своей таблицы), `anonymize` — поле очищается, запись остаётся,
# `keep` — запись хранится своим сроком. Записи, которые держатся за запись
# ученика ссылкой, а не полем пользователя, — `personal_data.ЗАВИСИМЫЕ`.
# Классификация данных и сроки — `docs/process/data-classification.md` в
# learning-services.
#
# Новое поле со ссылкой на пользователя — строка здесь или в
# `personal_data.НЕ_ДАННЫЕ_УЧЕНИКА`: иначе ворота
# `tests/test_personal_data.py` красные. `Why:` поле вне реестра процедура не
# видит — запись осталась бы после удаления, а удаление аккаунта отказало бы
# по ссылке.

_удалить = "delete"
_обезличить = "anonymize"

user_data_fields = [
	# Учебные данные ученика
	{"doctype": "Agent Chat State", "filter_by": "student", "mode": _удалить},
	{"doctype": "Agent Scenario State", "filter_by": "student", "mode": _удалить},
	{"doctype": "Agent Student Note", "filter_by": "student", "mode": _удалить},
	{"doctype": "Agent Student Note", "filter_by": "archived_student", "mode": _удалить},
	{"doctype": "Agent Student Artifact", "filter_by": "student", "mode": _удалить},
	{"doctype": "Agent Student Artifact", "filter_by": "archived_student", "mode": _удалить},
	{"doctype": "Agent Learning Session", "filter_by": "student", "mode": _удалить},
	{"doctype": "Agent Learning Session", "filter_by": "archived_student", "mode": _удалить},
	{"doctype": "Agent Lesson Run", "filter_by": "student", "mode": _удалить},
	{"doctype": "Agent Lesson Run", "filter_by": "archived_student", "mode": _удалить},
	{"doctype": "Agent Quiz Attempt", "filter_by": "student", "mode": _удалить},
	{"doctype": "Agent Quiz Attempt", "filter_by": "archived_student", "mode": _удалить},
	{"doctype": "Agent Quiz Event", "filter_by": "student", "mode": _удалить},
	{"doctype": "Agent Quiz Event", "filter_by": "archived_student", "mode": _удалить},
	{"doctype": "Agent Homework Submission", "filter_by": "member", "mode": _удалить},
	{"doctype": "Agent Homework Submission", "filter_by": "archived_student", "mode": _удалить},
	{"doctype": "Homework Notice", "filter_by": "recipient", "mode": _удалить},
	# Команда: членство и назначения — данные человека, сама команда остаётся
	{"doctype": "Organization Membership", "filter_by": "user", "mode": _удалить},
	{"doctype": "Allocation Notice", "filter_by": "user", "mode": _удалить},
	{"doctype": "Course Allocation Member", "filter_by": "user", "mode": _удалить},
	{"doctype": "Learning Organization", "filter_by": "created_by", "mode": _обезличить},
	# Кто вернул или принял чужую домашку — куратор; след проверки остаётся без имени
	{"doctype": "Agent Homework Event", "filter_by": "by_user", "mode": _обезличить},
	# Доказательство согласия — 3 года после удаления аккаунта (`consent.удалить_старые_согласия`)
	{"doctype": "Agent Consent", "filter_by": "user", "mode": "keep"},
	# Learning: запись на курс, прогресс, программы и то, что пишут его страницы урока
	{"doctype": "LMS Enrollment", "filter_by": "member", "mode": _удалить},
	{"doctype": "LMS Course Progress", "filter_by": "member", "mode": _удалить},
	{"doctype": "LMS Course Interest", "filter_by": "user", "mode": _удалить},
	{"doctype": "LMS Program Member", "filter_by": "member", "mode": _удалить},
	{"doctype": "LMS Quiz Submission", "filter_by": "member", "mode": _удалить},
	{"doctype": "LMS Lesson Note", "filter_by": "member", "mode": _удалить},
	{"doctype": "LMS Video Watch Duration", "filter_by": "member", "mode": _удалить},
	# Frappe: обсуждения уроков, уведомления, запросы на копию данных
	{"doctype": "Discussion Topic", "filter_by": "owner", "mode": _удалить},
	{"doctype": "Discussion Reply", "filter_by": "owner", "mode": _удалить},
	{"doctype": "Notification Log", "filter_by": "for_user", "mode": _удалить},
	{"doctype": "Notification Log", "filter_by": "from_user", "mode": _обезличить},
	{"doctype": "Personal Data Download Request", "filter_by": "user", "mode": _удалить},
]

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

