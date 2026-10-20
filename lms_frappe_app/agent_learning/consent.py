# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Согласие с документами: при регистрации и на экране согласия.

Явное согласие — две отметки: «мне есть 18 лет» и «принимаю Условия
использования и Политику конфиденциальности» (learning-services#624). В форме
регистрации они обязательны (`public/js/consent-notice.js`), и скрипт ставит
cookie `ЯВНОЕ_СОГЛАСИЕ` — по нему запись регистрации становится явной. Вход
через Google создаёт аккаунт при возврате от провайдера, до него отметок не
спросить: такая запись не явная, и пользователь до явного согласия видит
экран `/consent` (`потребовать_согласие`). Тот же экран видят все, у кого
явного согласия с действующей редакцией нет: прежние пользователи и все после
смены редакции. Обычный вход ничего не записывает: аккаунт уже есть.

Аккаунт, который завели вручную (Desk, импорт), при создании записи не
получает: человек этих страниц не видел. Такие аккаунты заводим только мы и
только по просьбе самого человека (политика конфиденциальности, раздел 3); их
согласие — с экрана, при первом входе, как у всех.

Гейт касается всех вошедших, сотрудников в desk тоже. `Why:` документы
принимает каждый, кто пользуется платформой; исключён только Administrator —
технический аккаунт, за которым нет человека. Не касается и сессии «Войти
как» (`impersonated_by`): отметки за ученика ставил бы не он.

Отметки формы регистрации приходят cookie с клиента. Подделать её может
только сам регистрирующийся — по силе это то же поле формы.
"""

from urllib.parse import quote, urlsplit

import frappe
from frappe.utils import add_days, now_datetime
from werkzeug.exceptions import HTTPException
from werkzeug.utils import redirect

from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	НАСТРОЙКИ,
)

РЕГИСТРАЦИЯ = "Sign Up"
ВНЕШНИЙ_ВХОД = "Social Login"
ЭКРАН_СОГЛАСИЯ = "Consent Screen"
#: Cookie формы регистрации: обе отметки поставлены (`consent-notice.js`).
#: Своё имя: `cenda_consent` — выбор по аналитике (`site/static/analytics.js`),
#: и с одним именем браузер прислал бы обе (learning-services#624).
ЯВНОЕ_СОГЛАСИЕ = "cenda_signup_marks"
#: Экран согласия.
СТРАНИЦА = "/consent"
#: Пути, которые работают и без явного согласия: сам экран, вход и выход,
#: статика и API. `Why:` API зовут скрипты уже открытых страниц и агенты по
#: токену — им экран не показать, а переадресация вместо JSON сломала бы их.
СВОБОДНЫЕ = (
	СТРАНИЦА,
	"/login",
	"/logout",
	"/update-password",
	# Сообщение Frappe живёт в кэше минуту: увести с него на экран — потерять его.
	"/message",
	"/api/",
	"/assets/",
	"/files/",
	"/private/",
	"/socket.io",
	"/.well-known/",
	"/website_script.js",
	"/favicon",
	"/robots.txt",
	"/sitemap",
)
#: Исключение из `/api/`: экран согласия OAuth открывается в браузере, когда
#: ученик подключает агента, — до согласия его ведём сначала на `/consent`.
АВТОРИЗАЦИЯ_АГЕНТА = (
	"/api/method/frappe.integrations.oauth2.authorize",
	"/api/v2/method/frappe.integrations.oauth2.authorize",
)
#: Служебная строка входа, которую Frappe в `User.validate` добавляет каждому
#: пользователю, — не внешний провайдер. `Why:` без исключения любая
#: регистрация формой записывалась как вход через провайдера `frappe`.
СЛУЖЕБНЫЙ_ВХОД = "frappe"


def редакция_документов() -> str:
	"""Редакция из настроек, а пустая — умолчание схемы.

	В настройках поле обязательное, и пустым оно бывает только до первой
	миграции. `Why:` запись согласия вставляется в той же транзакции, что и
	пользователь, и пустая редакция уронила бы саму регистрацию.
	"""
	поле = frappe.get_meta(НАСТРОЙКИ).get_field("legal_version")
	return frappe.db.get_single_value(НАСТРОЙКИ, "legal_version") or (поле.default if поле else "")


def путь_под_согласием(путь: str, метод: str) -> bool:
	"""Ведёт ли страница на экран согласия, пока явного согласия нет.

	Только страницы, которые открывают в браузере, — `GET`, кроме свободных
	путей; авторизация агента — тоже страница, хоть и под `/api/`.
	"""
	if метод not in ("GET", "HEAD"):
		return False
	if путь.startswith(АВТОРИЗАЦИЯ_АГЕНТА):
		return True
	return not путь.startswith(СВОБОДНЫЕ)


def готово() -> bool:
	"""Схема согласия уже с явными отметками. `Why:` в окно выкладки код новый,
	а `bench migrate` ещё не добавил колонку: запрос по ней дал бы 500 на каждой
	странице и уронил бы регистрацию."""
	return frappe.db.table_exists("Agent Consent") and frappe.db.has_column("Agent Consent", "explicit")


def куда_вернуть(адрес: str | None, домой: str) -> str:
	"""Свой относительный адрес или `домой`.

	`Why:` чужой адрес превратил бы экран в переадресацию на любой сайт. Браузер
	выбрасывает из адреса табуляцию и переводы строк, и `/\t/evil` стал бы
	`//evil` — поэтому управляющие символы отвергаются целиком, а адрес
	разбирается так же, как его разберёт браузер.
	"""
	адрес = (адрес or "").strip()
	if any(ord(знак) < 0x20 or знак == "\x7f" for знак in адрес) or "\\" in адрес:
		return домой
	части = urlsplit(адрес)
	if части.scheme or части.netloc or not адрес.startswith("/") or адрес.startswith("//"):
		return домой
	if адрес.startswith(СТРАНИЦА):
		return домой
	return адрес


def от_имени() -> bool:
	"""Сессия «Войти как» — за пользователя действует другой человек."""
	return bool((getattr(frappe.local, "session", None) or {}).get("data", {}).get("impersonated_by"))


def есть_явное(пользователь: str) -> bool:
	"""Явное согласие с действующей редакцией документов уже дано."""
	return bool(
		frappe.db.exists(
			"Agent Consent",
			{"user": пользователь, "documents_version": редакция_документов(), "explicit": 1},
		)
	)


def потребовать_согласие():
	"""`before_request`: без явного согласия страницы платформы ведут на `/consent`.

	Сессия из cookie к этому моменту уже поднята (`frappe.auth.HTTPRequest`),
	а вход по токену — нет: агенты по API сюда и не попадают. Переадресация —
	ответом werkzeug: `frappe.Redirect`, брошенный здесь, Frappe отрисовал бы
	страницей ошибки.
	"""
	пользователь = frappe.session.user
	if пользователь in ("Guest", "Administrator", None) or not frappe.request:
		return
	if not путь_под_согласием(frappe.request.path, frappe.request.method):
		return
	if от_имени() or not готово() or есть_явное(пользователь):
		return
	куда = frappe.request.full_path.rstrip("?")
	raise HTTPException(response=redirect(f"{СТРАНИЦА}?redirect-to={quote(куда, safe='')}", 302))


def принять(пользователь: str) -> None:
	"""Явное согласие с экрана согласия: обе отметки поставлены."""
	if есть_явное(пользователь):
		return
	frappe.get_doc(
		{
			"doctype": "Agent Consent",
			"user": пользователь,
			"documents_version": редакция_документов(),
			"accepted_at": now_datetime(),
			"method": ЭКРАН_СОГЛАСИЯ,
			"explicit": 1,
			"age_confirmed": 1,
		}
	).insert(ignore_permissions=True)


def записать_согласие(user, method=None):
	"""`after_insert` пользователя: самостоятельная регистрация — запись согласия.

	Самостоятельная — значит, аккаунт создаётся в гостевом запросе: и
	`sign_up`, и вход через провайдера вставляют пользователя до входа под
	ним. Под администратором или в фоне (импорт, `bench`) сессия не гостевая.
	"""
	if frappe.session.user != "Guest" or user.name in ("Guest", "Administrator"):
		return
	# Окно выкладки: код уже новый, а `bench migrate` ещё не создал таблицу.
	# Регистрация в это окно проходит без записи, а не падает.
	if not frappe.db.table_exists("Agent Consent"):
		return
	явное = готово() and _отметки_формы()
	провайдеры = [
		строка.provider
		for строка in user.get("social_logins") or []
		if строка.provider and строка.provider != СЛУЖЕБНЫЙ_ВХОД
	]
	frappe.get_doc(
		{
			"doctype": "Agent Consent",
			"user": user.name,
			"documents_version": редакция_документов(),
			"accepted_at": now_datetime(),
			"method": ВНЕШНИЙ_ВХОД if провайдеры else РЕГИСТРАЦИЯ,
			"provider": провайдеры[0] if провайдеры else "",
			# Отметки есть только в форме регистрации: у входа через провайдера
			# их не спросить, и он придёт на экран согласия.
			**({"explicit": 1, "age_confirmed": 1} if not провайдеры and явное else {}),
		}
	).insert(ignore_permissions=True)
	# Отметки нужны одному запросу регистрации — дальше cookie не живёт.
	# Менеджер cookie есть только у HTTP-запроса, не у вызова из кода.
	if явное and (cookie := getattr(frappe.local, "cookie_manager", None)):
		cookie.delete_cookie(ЯВНОЕ_СОГЛАСИЕ)


def _отметки_формы() -> bool:
	"""Форма регистрации прислала обе отметки: cookie ставит её скрипт."""
	return bool(frappe.request and frappe.request.cookies.get(ЯВНОЕ_СОГЛАСИЕ) == "1")


# --- младше 18 лет ---

#: Учебные данные ученика: есть они — аккаунт не удаляется сразу, а
#: закрывается, и данные удаляет администратор (политика, раздел 13).
УЧЕБНЫЕ_ДАННЫЕ = (
	("LMS Enrollment", "member"),
	("Agent Learning Session", "student"),
	("Agent Student Artifact", "student"),
	("Agent Student Note", "student"),
	("Agent Homework Submission", "member"),
)
#: Сколько дней ждём согласия у нового аккаунта, прежде чем удалить его.
ДНЕЙ_БЕЗ_СОГЛАСИЯ = 30
#: Чистка касается аккаунтов, созданных с этого дня, — когда появилось явное
#: согласие. `Why:` у прежних пользователей явного согласия нет ни у кого, и
#: чистка удалила бы всех, кто не зашёл на сайт за месяц.
ЧИСТКА_С = "2026-10-20"
#: Куда писать о закрытом аккаунте с данными.
ПОЧТА_ДАННЫХ = "privacy@cenda.app"


def есть_учебные_данные(пользователь: str) -> bool:
	return any(frappe.db.exists(тип, {поле: пользователь}) for тип, поле in УЧЕБНЫЕ_ДАННЫЕ)


def убрать_несовершеннолетнего(пользователь: str) -> str:
	"""Пользователь сообщил, что ему нет 18 лет: аккаунт удаляется или закрывается.

	Без учебных данных — удаляется сразу вместе с контактом: Frappe при удалении
	пользователя контакт только отвязывает, и в нём остались бы почта и имя.
	С данными — закрывается, а данные удаляет администратор по письму на
	`ПОЧТА_ДАННЫХ` в срок политики конфиденциальности. `Why:` данные ребёнка без
	согласия родителей держать нельзя (ЗРУ-547, ст. 21), а удалять учебные
	данные молча, мимо проверки человеком, — риск стереть чужое. Запись о
	согласии остаётся: она доказывает, что согласия не было.
	"""
	if есть_учебные_данные(пользователь):
		frappe.db.set_value("User", пользователь, "enabled", 0)
		if getattr(frappe.local, "login_manager", None):
			frappe.local.login_manager.logout(user=пользователь)
		_сообщить_о_закрытом(пользователь)
		return "closed"
	_удалить(пользователь)
	return "deleted"


def _удалить(пользователь: str) -> None:
	for контакт in frappe.get_all("Contact", filters={"user": пользователь}, pluck="name"):
		frappe.delete_doc("Contact", контакт, ignore_permissions=True, delete_permanently=True)
	frappe.delete_doc("User", пользователь, ignore_permissions=True, delete_permanently=True)


def _сообщить_о_закрытом(пользователь: str) -> None:
	from lms_frappe_app.agent_learning.notices import почта_есть

	if not почта_есть():
		return
	frappe.sendmail(
		recipients=[ПОЧТА_ДАННЫХ],
		subject="Закрыт аккаунт: пользователю нет 18 лет",
		message=(
			f"Пользователь {пользователь} сообщил на экране согласия, что ему нет 18 лет. "
			"Аккаунт закрыт. Удалите его учебные данные и аккаунт в срок политики конфиденциальности."
		),
		now=True,
	)


def удалить_без_согласия() -> int:
	"""Ежедневно: новые аккаунты, не давшие согласия за `ДНЕЙ_БЕЗ_СОГЛАСИЯ`, удаляются.

	Это человек, который вошёл через Google и закрыл экран согласия: он мог быть
	младше 18, а его почта и имя у нас уже есть. Только аккаунты сайта
	(`Website User`), созданные не раньше `ЧИСТКА_С` и без учебных данных.
	"""
	if not готово():
		return 0
	срок = add_days(now_datetime(), -ДНЕЙ_БЕЗ_СОГЛАСИЯ)
	кандидаты = frappe.get_all(
		"User",
		filters={
			"user_type": "Website User",
			"creation": ["between", [ЧИСТКА_С, срок]],
			"name": ["not in", ("Guest", "Administrator")],
		},
		pluck="name",
	)
	удалено = 0
	for пользователь in кандидаты:
		if frappe.db.exists("Agent Consent", {"user": пользователь, "explicit": 1}):
			continue
		if есть_учебные_данные(пользователь):
			continue
		_удалить(пользователь)
		удалено += 1
	return удалено
