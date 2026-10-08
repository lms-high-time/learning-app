# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Настройка при установке и миграции."""

import frappe
from frappe.utils import get_url

from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	НАСТРОЙКИ,
)

#: Пункты сайдбара Frappe Learning — в том порядке, в каком их видит ученик:
#: сначала способы заниматься, документы последними (lms-platform#311).
#: Web Page — обязательное поле пункта, а `route` и `title` пункт берёт из неё
#: (`fetch_from`). Заглушки не публикуются: сами страницы живут в Learning и в
#: MCP-сервисе, а с маршрута заглушки на них ведёт `website_redirects` в hooks.
#:
#: Иконка — имя компонента `lucide-vue-next` (PascalCase), как её сохраняет
#: выбор иконки в самом Learning: по нему рисует сайдбар на десктопе, а
#: телефон выводит из него класс `lucide-…`. `Why:` имена в kebab-case
#: телефон понимал, а десктоп — нет, и пункты платформы стояли без иконок.
ПУНКТЫ_САЙДБАРА = (
	# Веб-чат живёт в MCP-сервисе на том же домене, что и сайт. Маршрут заглушки
	# не начинается с путей этого сервиса: Traefik сопоставляет их по префиксу, и
	# `/chat-…` ушёл бы в MCP-сервис с «Not Found» вместо переадресации.
	{"title": "Заниматься в браузере", "route": "study-in-browser", "icon": "MessageCircle", "page": "/chat"},
	{"title": "Подключить ассистента", "route": "agent-sidebar", "icon": "Bot", "page": "/lms/agent"},
	{"title": "Мои документы", "route": "artifacts-sidebar", "icon": "FileText", "page": "/lms/documents"},
)

#: Иконки, которые прежде ставила сама установка, — их правка админа не трогает.
ПРЕЖНИЕ_ИКОНКИ = {"message-circle", "bot", "file-text", ""}


def обеспечить_пункты_сайдбара() -> None:
	"""Пункты приложения в сайдбаре Frappe Learning — есть, с иконкой, по порядку.

	Сайдбар читает дочернюю таблицу `LMS Settings.sidebar_items` в её порядке
	и ведёт по `route` обычной ссылкой. Пункты платформы идут первыми и в
	порядке `ПУНКТЫ_САЙДБАРА`, пункты, добавленные админом, — следом, в своём.
	Иконку установка меняет, только если та осталась от неё самой: выбранную
	админом не трогает. Подпись задаётся при создании заглушки, дальше её
	правит админ, и деплой её не трогает: переименование пункта платформой —
	разовым патчем. Идемпотентно: вызывается при установке и при каждой
	миграции, и без расхождений ничего не сохраняет.
	"""
	заглушки = [_заглушка(пункт) for пункт in ПУНКТЫ_САЙДБАРА]
	настройки = frappe.get_single("LMS Settings")
	строки = {строка.web_page: строка for строка in настройки.sidebar_items}

	наши = []
	for пункт, заглушка in zip(ПУНКТЫ_САЙДБАРА, заглушки):
		строка = строки.get(заглушка)
		if строка is None:
			строка = frappe._dict(web_page=заглушка, icon=пункт["icon"])
		elif (строка.icon or "") in ПРЕЖНИЕ_ИКОНКИ:
			строка.icon = пункт["icon"]
		наши.append(строка)
	чужие = [строка for строка in настройки.sidebar_items if строка.web_page not in заглушки]

	было = [(строка.web_page, строка.icon) for строка in настройки.sidebar_items]
	стало = [(строка.web_page, строка.icon) for строка in наши + чужие]
	if было == стало:
		return
	настройки.set(
		"sidebar_items",
		[{"web_page": строка.web_page, "icon": строка.icon} for строка in наши + чужие],
	)
	настройки.save(ignore_permissions=True)


def _заглушка(пункт: dict) -> str:
	имя = frappe.db.get_value("Web Page", {"route": пункт["route"]})
	if имя:
		return имя
	return (
		frappe.get_doc(
			{
				"doctype": "Web Page",
				"published": 0,
				"content_type": "Rich Text",
				"main_section": f"<p>Страница живёт в приложении: {пункт['page']}.</p>",
				"title": пункт["title"],
				"route": пункт["route"],
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def обеспечить_индекс_заметок() -> None:
	"""Уникальность ключа заметки на уровне базы.

	`Why:` замещение по ключу — договор всей затеи. Проверка в Python держит
	его, пока запись идёт из одного места; индекс держит всегда, включая
	правку из админки и гонку двух вызовов агента. Пустой курс у фактов
	хранится строкой, а не NULL: NULL-ы MariaDB считает различными, и дубли
	прошли бы мимо индекса.
	"""
	frappe.db.sql_ddl(
		"""
		CREATE UNIQUE INDEX IF NOT EXISTS `agent_student_note_key`
		ON `tabAgent Student Note` (`student`, `course`, `note_key`)
		"""
	)


def обеспечить_индекс_сценариев() -> None:
	"""Разговор сценария один на «ученик + ключ сценария».

	`Why:` та же причина, что у индекса заметок: метод ищет запись перед
	созданием, но гонку двух ходов и правку из админки держит только база.
	Две записи одного сценария — два разговора и два счётчика ходов, из
	которых метод видел бы случайный, а лимит ходов обходился бы вторым.
	"""
	frappe.db.sql_ddl(
		"""
		CREATE UNIQUE INDEX IF NOT EXISTS `agent_scenario_state_key`
		ON `tabAgent Scenario State` (`student`, `scenario_key`)
		"""
	)


def обеспечить_индекс_артефактов() -> None:
	"""Экземпляр артефакта один на «ученик + курс + артефакт + пространство».

	`Why:` та же причина, что у индекса заметок: метод записи ищет экземпляр
	перед созданием, но гонку двух вызовов агента и правку из админки держит
	только база. Два экземпляра одного документа — это два разных «резюме
	проекта», из которых страница покажет случайный.

	Пространство входит в ключ (learning-services#346): один курс проходится
	лично и в компании, и документов у него два. Входит через вычисляемую
	колонку `space_key`: личное пространство — пустое `organization`, а NULL в
	уникальном индексе MariaDB не равен NULL, и личные документы дублировались
	бы беспрепятственно.
	"""
	frappe.db.sql_ddl(
		"""
		ALTER TABLE `tabAgent Student Artifact`
		ADD COLUMN IF NOT EXISTS `space_key` varchar(140)
		AS (ifnull(`organization`, '')) PERSISTENT
		"""
	)
	frappe.db.sql_ddl(
		"DROP INDEX IF EXISTS `agent_student_artifact_key` ON `tabAgent Student Artifact`"
	)
	frappe.db.sql_ddl(
		"""
		CREATE UNIQUE INDEX IF NOT EXISTS `agent_student_artifact_space_key`
		ON `tabAgent Student Artifact` (`student`, `course`, `artifact`, `space_key`)
		"""
	)


def обеспечить_индекс_релизов() -> None:
	"""Версия релиза одна на курс (learning-services#500).

	`Why:` версию считает сервис публикации под блокировкой строки курса, но
	правку мимо кода и гонку держит только база: дубль версии сделал бы
	«действующий релиз» неоднозначным, а историю курса — нелинейной.
	"""
	frappe.db.sql_ddl(
		"""
		CREATE UNIQUE INDEX IF NOT EXISTS `agent_course_release_version`
		ON `tabAgent Course Release` (`course`, `version`)
		"""
	)


def обеспечить_индекс_прохождений() -> None:
	"""Прохождение урока одно на «ученик + курс + ключ урока» (learning-services#504).

	`Why:` та же причина, что у индекса заметок: `runs.прохождение` ищет
	запись перед созданием, а параллельные вызовы агента и правку из админки
	держит только база. Два прохождения одного урока — две половины отметок, и
	статус цели зависел бы от того, какое прочитано. Архивные прохождения
	(`student` пуст) индекс не ловит: NULL-ы MariaDB различны, и после сброса
	урок проходится заново.
	"""
	frappe.db.sql_ddl(
		"""
		CREATE UNIQUE INDEX IF NOT EXISTS `agent_lesson_run_key`
		ON `tabAgent Lesson Run` (`student`, `course`, `lesson_key`)
		"""
	)


def обеспечить_индекс_ответов_квиза() -> None:
	"""Ответ на вопрос урока из релиза один на попытку (learning-services#504).

	`Why:` `release_quiz.ответить` проверяет «уже отвечено» перед записью, но
	проверка читает без блокировки, и второй из двух параллельных ответов
	прошёл бы её: второй вердикт по тому же вопросу назвал бы верный вариант.
	Держит только база.
	"""
	frappe.db.sql_ddl(
		"""
		CREATE UNIQUE INDEX IF NOT EXISTS `agent_quiz_answer_question_key`
		ON `tabAgent Quiz Answer` (`attempt`, `question_key`)
		"""
	)


def обеспечить_индекс_домашек() -> None:
	"""Индексы сдач домашки: живая сдача одна на «задание + ученик + пространство»
	(learning-services#439), отбор по статусу и сроку (learning-services#452).

	`Why:` та же гонка, что у документов: выдача и сохранение ищут сдачу перед
	созданием, а два вызова подряд держит только база. Пространство — через
	вычисляемую `space_key`: NULL в уникальном индексе MariaDB не равен NULL.
	Архивные сдачи (`member` пуст) индекс не ловит по той же причине —
	повторное прохождение после сброса начинается с новой сдачи.
	"""
	frappe.db.sql_ddl(
		"""
		ALTER TABLE `tabAgent Homework Submission`
		ADD COLUMN IF NOT EXISTS `space_key` varchar(140)
		AS (ifnull(`organization`, '')) PERSISTENT
		"""
	)
	frappe.db.sql_ddl(
		"""
		CREATE UNIQUE INDEX IF NOT EXISTS `agent_homework_submission_space_key`
		ON `tabAgent Homework Submission` (`homework`, `member`, `space_key`)
		"""
	)
	# Why: очередь куратора, счётчик меню и ежечасные письма отбирают сдачи по
	# статусу, напоминания — ещё и по сроку (learning-services#452).
	frappe.db.sql_ddl(
		"""
		CREATE INDEX IF NOT EXISTS `agent_homework_submission_status_due`
		ON `tabAgent Homework Submission` (`status`, `due_at`)
		"""
	)


#: Ключи Google живут в конфигурации сайта, а не в коде: значения секретны, а
#: запись провайдера — данные, которые обязаны пережить пересоздание сайта.
КЛЮЧ_ID = "google_login_client_id"
КЛЮЧ_СЕКРЕТ = "google_login_client_secret"


def обеспечить_вход_через_google() -> None:
	"""Ключ входа через Google из конфигурации сайта.

	`Why:` запись `Social Login Key`, заведённая кликами в админке, живёт до
	пересоздания сайта. Стенд поднимается `create-site`, и такая настройка
	нигде не записана — после переезда или восстановления из бэкапа вход
	исчезает молча, а замечает это первый ученик, который не смог войти.

	Ключей в конфиге нет — не делаем ничего: локальная разработка не обязана
	держать секреты Google, а пустая запись сломала бы кнопку на `/login`.
	"""
	client_id = frappe.conf.get(КЛЮЧ_ID)
	client_secret = frappe.conf.get(КЛЮЧ_СЕКРЕТ)
	if not client_id or not client_secret:
		return

	существует = frappe.db.exists("Social Login Key", "google")
	ключ = (
		frappe.get_doc("Social Login Key", "google")
		if существует
		else frappe.new_doc("Social Login Key")
	)
	if not существует:
		# Адреса, скоупы и иконку задаёт сам Frappe: свои копии разъехались бы
		# с ним при первом же обновлении. Метод объявлен пригодным именно для
		# создания ключа из контроллера.
		ключ.get_social_login_provider("Google", initialize=True)

	if not _расходится(ключ, client_id, client_secret):
		return

	ключ.update(
		{
			"social_login_provider": "Google",
			"client_id": client_id,
			"client_secret": client_secret,
			"enable_social_login": 1,
			# Пускаем всех: платформа открыта, ограничение по доменам
			# организаций сознательно не вводится.
			"sign_ups": "Allow",
		}
	)
	ключ.save(ignore_permissions=True)


def _расходится(ключ, client_id: str, client_secret: str) -> bool:
	"""Отличается ли запись от конфигурации.

	`Why:` `after_migrate` зовётся на каждый старт контейнера, и безусловное
	сохранение писало бы новую версию документа на ровном месте.
	"""
	from frappe.utils.password import get_decrypted_password

	if ключ.is_new():
		return True
	if ключ.client_id != client_id or not ключ.enable_social_login:
		return True
	if ключ.sign_ups != "Allow":
		return True
	прежний = get_decrypted_password(
		"Social Login Key", "google", "client_secret", raise_exception=False
	)
	return прежний != client_secret


#: OAuth-клиент веб-чата тоже приезжает из конфигурации сайта: те же id и
#: секрет знает MCP-сервис, а оболочки контейнера на стенде нет.
КЛЮЧ_ЧАТА_ID = "web_chat_oauth_client_id"
КЛЮЧ_ЧАТА_СЕКРЕТ = "web_chat_oauth_client_secret"
КЛЮЧ_ЧАТА_ВОЗВРАТ = "web_chat_redirect_uri"
ИМЯ_КЛИЕНТА_ЧАТА = "MCP-LMS Web Chat"


def обеспечить_клиента_веб_чата() -> None:
	"""OAuth-клиент веб-чата MCP-сервиса из конфигурации сайта.

	`Why:` та же причина, что у ключа Google: клиент, заведённый в desk, не
	переживает пересоздания сайта, а завести его скриптом на стенде нечем.
	Флаги — часть договора с учеником: без Skip Authorization он видит экран
	согласия, без Client Secret Post сервис не обменяет код на токен (заголовок
	Basic Frappe принимает за вход пользователя). Ручная правка флагов
	возвращается на следующей миграции.

	Ключи заданы не все — не делаем ничего: клиент без секрета или адреса
	возврата — вход, который молча не работает.
	"""
	client_id = frappe.conf.get(КЛЮЧ_ЧАТА_ID)
	client_secret = frappe.conf.get(КЛЮЧ_ЧАТА_СЕКРЕТ)
	возврат = frappe.conf.get(КЛЮЧ_ЧАТА_ВОЗВРАТ)
	if not (client_id and client_secret and возврат):
		return

	нужные = {
		"app_name": ИМЯ_КЛИЕНТА_ЧАТА,
		"client_secret": client_secret,
		"redirect_uris": возврат,
		"default_redirect_uri": возврат,
		"scopes": "all",
		"grant_type": "Authorization Code",
		"response_type": "Code",
		"skip_authorization": 1,
		"token_endpoint_auth_method": "Client Secret Post",
	}
	if frappe.db.exists("OAuth Client", client_id):
		клиент = frappe.get_doc("OAuth Client", client_id)
		# Безусловное сохранение писало бы версию на каждый старт контейнера.
		if all(клиент.get(поле) == значение for поле, значение in нужные.items()):
			return
		клиент.update(нужные)
		клиент.save(ignore_permissions=True)
		return
	# Имя записи = id из конфигурации: `client_id` Frappe выводит из имени.
	frappe.get_doc({"doctype": "OAuth Client", **нужные}).insert(
		ignore_permissions=True, set_name=client_id
	)


def обеспечить_значения_настроек() -> None:
	"""Незаполненным полям `Agent Learning Settings` — значения по умолчанию.

	`Why:` поле Single-доктайпа, появившееся миграцией, на существующем стенде
	остаётся без строки в `tabSingles`: код возьмёт запасное значение из
	константы, а админ увидит пустое поле и не узнает, на чём платформа
	работала до сих пор. Значения берутся из схемы доктайпа, чтобы не заводить
	им второй дом в коде установки.

	Заполненное не переписывается — включая пустую строку: `after_migrate`
	зовётся на каждый старт контейнера, и иначе правка из админки откатывалась
	бы на ближайшем деплое. Строка в `tabSingles` есть — поле уже видели.
	"""
	заполненные = frappe.db.get_singles_dict(НАСТРОЙКИ)
	умолчания = {
		поле.fieldname: поле.default
		for поле in frappe.get_meta(НАСТРОЙКИ).fields
		if поле.default
	}
	# Адрес сервиса агента в схеме пустой: он зависит от сайта, а не от кода.
	умолчания.setdefault("agent_service_url", get_url().rstrip("/"))

	новые = {
		поле: значение
		for поле, значение in умолчания.items()
		if поле not in заполненные
	}
	if not новые:
		return
	for поле, значение in новые.items():
		frappe.db.set_single_value(НАСТРОЙКИ, поле, значение)
	frappe.clear_document_cache(НАСТРОЙКИ, НАСТРОЙКИ)


def after_install() -> None:
	обеспечить_пункты_сайдбара()
	обеспечить_индекс_заметок()
	обеспечить_индекс_сценариев()
	обеспечить_индекс_артефактов()
	обеспечить_индекс_домашек()
	обеспечить_индекс_релизов()
	обеспечить_индекс_прохождений()
	обеспечить_индекс_ответов_квиза()
	обеспечить_вход_через_google()
	обеспечить_клиента_веб_чата()
	обеспечить_значения_настроек()


def after_migrate() -> None:
	обеспечить_пункты_сайдбара()
	обеспечить_индекс_заметок()
	обеспечить_индекс_сценариев()
	обеспечить_индекс_артефактов()
	обеспечить_индекс_домашек()
	обеспечить_индекс_релизов()
	обеспечить_индекс_прохождений()
	обеспечить_индекс_ответов_квиза()
	обеспечить_вход_через_google()
	обеспечить_клиента_веб_чата()
	обеспечить_значения_настроек()
