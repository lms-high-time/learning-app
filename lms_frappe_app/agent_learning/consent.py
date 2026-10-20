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

#: Пользователь сообщил на экране согласия, что ему нет 18 лет.
НЕТ_18 = "Underage"
#: Аккаунт через Google не дал согласия за `ДНЕЙ_БЕЗ_СОГЛАСИЯ`.
НЕ_ДАНО = "Not Given"
#: Сколько дней ждём согласия у аккаунта, созданного через Google.
ДНЕЙ_БЕЗ_СОГЛАСИЯ = 30
#: Чистка касается аккаунтов, созданных с этого дня, — когда появилось явное
#: согласие. `Why:` у прежних пользователей явного согласия нет ни у кого, и
#: чистка удалила бы всех, кто не зашёл на сайт за месяц.
ЧИСТКА_С = "2026-10-20"
#: Аккаунтов за один прогон чистки. `Why:` удаление сверяет ссылки по всем
#: полям на User — десятки запросов на пользователя.
ЧИСТКА_ЗА_РАЗ = 200
#: Куда писать о закрытом аккаунте с данными.
ПОЧТА_ДАННЫХ = "privacy@cenda.app"
#: Ссылки, которые Frappe при удалении пользователя убирает сам.
УБИРАЕТ_FRAPPE = frozenset({"Contact", "Notification Settings"})


def есть_связанные_данные(пользователь: str) -> bool:
	"""На пользователя ссылается что-то, кроме того, что Frappe убирает сам.

	Проверка — той же функцией, что у самого удаления. `Why:` ручной список
	«учебных данных» отстаёт от доктайпов (сброс прогресса, команды, токены
	агента, доктайпы Frappe Learning), а Frappe проверяет ссылки уже после того,
	как разлогинил пользователя и закоммитил: аккаунт оставался живым, а
	заявление пропадало.
	"""
	from frappe.model.delete_doc import get_dynamic_linked_docs, get_linked_docs

	документ = frappe.get_doc("User", пользователь)
	ссылки = get_linked_docs(документ) + get_dynamic_linked_docs(документ)
	return any(с["reference_doctype"] not in УБИРАЕТ_FRAPPE for с in ссылки)


def убрать_аккаунт(пользователь: str, причина: str) -> str:
	"""Аккаунт без согласия: удаляется или закрывается.

	`причина` — `НЕТ_18` (заявление на экране) или `НЕ_ДАНО` (чистка). Сначала —
	запись в «Согласие с документами»: она переживает удаление и показывает,
	когда и почему аккаунт убран. Без связанных данных аккаунт удаляется вместе
	с контактом: Frappe контакт только отвязывает, и в нём остались бы почта и
	имя. Со связанными данными — закрывается, все его сессии завершаются, а
	данные удаляет администратор по письму на `ПОЧТА_ДАННЫХ`. `Why:` данные
	несовершеннолетнего без согласия родителей держать нельзя (ЗРУ-547, ст.
	21), а стирать учебные данные мимо человека — риск удалить их у взрослого,
	нажавшего по ошибке.
	"""
	данные = есть_связанные_данные(пользователь)
	frappe.get_doc(
		{
			"doctype": "Agent Consent",
			"user": пользователь,
			"documents_version": редакция_документов(),
			"accepted_at": now_datetime(),
			"method": причина,
		}
	).insert(ignore_permissions=True)
	# Сессии на других устройствах: `Session.resume` не смотрит на `enabled`.
	# Импорт явный: `frappe.sessions` подгружает только веб-запрос, и в задаче
	# планировщика обращение через атрибут падало бы на каждом аккаунте.
	from frappe.sessions import clear_sessions

	clear_sessions(пользователь, keep_current=True, force=True)
	if данные:
		документ = frappe.get_doc("User", пользователь)
		документ.enabled = 0
		документ.save(ignore_permissions=True)
		_сообщить_о_закрытом(пользователь, причина)
		# Свою текущую сессию — выходом: просто удалённую Frappe в конце запроса
		# записал бы обратно.
		if frappe.session.user == пользователь and getattr(frappe.local, "login_manager", None):
			frappe.local.login_manager.logout()
		return "closed"
	# Текущую сессию при удалении закрывает сам Frappe (`User.on_trash`).
	_удалить(пользователь)
	return "deleted"


def _удалить(пользователь: str) -> None:
	контакты = set(frappe.get_all("Contact", filters={"user": пользователь}, pluck="name"))
	контакты.update(frappe.get_all("Contact Email", filters={"email_id": пользователь}, pluck="parent"))
	for контакт in контакты:
		frappe.delete_doc("Contact", контакт, ignore_permissions=True, delete_permanently=True)
	frappe.delete_doc("User", пользователь, ignore_permissions=True, delete_permanently=True)


def _сообщить_о_закрытом(пользователь: str, причина: str) -> None:
	from lms_frappe_app.agent_learning.notices import почта_есть

	if not почта_есть():
		# След остаётся и без почты: запись `причина` в «Согласие с документами».
		return
	почему = (
		"сообщил на экране согласия, что ему нет 18 лет"
		if причина == НЕТ_18
		else f"вошёл через Google и за {ДНЕЙ_БЕЗ_СОГЛАСИЯ} дней не подтвердил возраст и согласие"
	)
	frappe.sendmail(
		recipients=[ПОЧТА_ДАННЫХ],
		subject="Закрыт аккаунт без согласия с документами",
		message=(
			f"Пользователь {пользователь} {почему}. У аккаунта есть данные, поэтому он закрыт, "
			"а не удалён. Прежде чем удалять данные, свяжитесь с пользователем: кнопку могли нажать "
			"по ошибке. Срок удаления — 30 дней по политике конфиденциальности."
		),
	)


def удалить_без_согласия() -> int:
	"""Ежедневно: аккаунты через Google без согласия за `ДНЕЙ_БЕЗ_СОГЛАСИЯ` убираются.

	Это человек, который вошёл через Google и закрыл экран согласия: он мог быть
	младше 18, а его почта и имя у нас уже есть. Только аккаунты с записью входа
	через провайдера с `ЧИСТКА_С` — заведённые вручную не трогаются, — без
	явного согласия и ещё не убранные. Каждый — своей транзакцией: сбой на
	одном не откатывает остальных.
	"""
	if not готово():
		return 0
	срок = add_days(now_datetime(), -ДНЕЙ_БЕЗ_СОГЛАСИЯ)
	кандидаты = frappe.get_all(
		"Agent Consent",
		filters={"method": ВНЕШНИЙ_ВХОД, "accepted_at": ["between", [ЧИСТКА_С, срок]]},
		pluck="user",
		distinct=True,
	)
	убрано = 0
	for пользователь in кандидаты:
		if убрано >= ЧИСТКА_ЗА_РАЗ:
			break
		тип = frappe.db.get_value("User", {"name": пользователь, "enabled": 1}, "user_type")
		if тип != "Website User":
			continue
		уже = {"user": пользователь, "method": ["in", (НЕТ_18, НЕ_ДАНО)]}
		if frappe.db.exists("Agent Consent", {"user": пользователь, "explicit": 1}) or frappe.db.exists(
			"Agent Consent", уже
		):
			continue
		frappe.db.savepoint("consent_cleanup")
		try:
			убрать_аккаунт(пользователь, НЕ_ДАНО)
			frappe.db.commit()
			убрано += 1
		except Exception:
			frappe.db.rollback(save_point="consent_cleanup")
			frappe.log_error(title=f"Чистка аккаунтов без согласия: {пользователь}")
	return убрано
