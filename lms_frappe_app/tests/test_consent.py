# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Согласие с документами: при регистрации и на экране согласия
(learning-services#577, #624).

Проверяется, кто получает запись и с чем: регистрация формой и первым входом
через провайдера — да, с редакцией документов из настроек; аккаунт от
администратора — нет. Пустая настройка не роняет регистрацию. Явное
согласие — отметки формы или экрана согласия; без него страницы платформы
ведут на экран согласия.
"""

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_days, now_datetime
from werkzeug.exceptions import HTTPException
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from lms_frappe_app.agent_learning import consent
from lms_frappe_app.agent_learning.consent import ВНЕШНИЙ_ВХОД, РЕГИСТРАЦИЯ
from lms_frappe_app.api import consent as consent_api
from lms_frappe_app.www.consent import ДОМОЙ
from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	НАСТРОЙКИ,
)


def почта() -> str:
	return f"consent-{frappe.generate_hash(length=8)}@example.com"


def зарегистрировать(почта: str, *, провайдер: str | None = None) -> str:
	"""Пользователь создаётся так, как это делают `sign_up` и вход через
	провайдера: в гостевом запросе, с флагом обхода прав."""
	пользователь = frappe.new_doc("User")
	пользователь.update({"email": почта, "first_name": "Ученик", "send_welcome_email": 0})
	if провайдер:
		пользователь.append("social_logins", {"provider": провайдер, "userid": "id-" + почта})
	пользователь.flags.ignore_permissions = True
	пользователь.insert()
	return почта


def согласия(почта: str) -> list[frappe._dict]:
	return frappe.get_all(
		"Agent Consent",
		filters={"user": почта},
		fields=["documents_version", "method", "provider", "accepted_at", "explicit", "age_confirmed"],
	)


@contextmanager
def запрос(путь: str = "/lms", метод: str = "GET", cookie: str | None = None, **заголовки):
	"""Запрос браузера на время проверки: путь, метод, cookie и заголовки."""
	был = hasattr(frappe.local, "request")
	прежний = getattr(frappe.local, "request", None)
	if cookie:
		заголовки["Cookie"] = cookie
	frappe.local.request = Request(EnvironBuilder(path=путь, method=метод, headers=заголовки).get_environ())
	try:
		yield
	finally:
		if был:
			frappe.local.request = прежний
		else:
			del frappe.local.request


class IntegrationTestConsent(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")

	def test_регистрация_формой_записывает_согласие_с_редакцией(self):
		frappe.db.set_single_value(НАСТРОЙКИ, "legal_version", "2026-10-14")

		[запись] = согласия(зарегистрировать(почта()))

		self.assertEqual(запись.documents_version, "2026-10-14")
		# Служебную строку входа `frappe` Frappe добавляет каждому пользователю:
		# она не делает регистрацию формой входом через провайдера.
		self.assertEqual(запись.method, РЕГИСТРАЦИЯ)
		self.assertFalse(запись.provider)
		self.assertTrue(запись.accepted_at)

	def test_первый_вход_через_google_записывает_провайдера(self):
		"""Кнопка «Войти через Google» у нового человека и есть регистрация."""
		[запись] = согласия(зарегистрировать(почта(), провайдер="google"))

		self.assertEqual(запись.method, ВНЕШНИЙ_ВХОД)
		self.assertEqual(запись.provider, "google")

	def test_аккаунт_от_администратора_записи_не_получает(self):
		"""Человек страниц входа не видел — согласие собирает тот, кто его заводит."""
		frappe.set_user("Administrator")

		self.assertEqual(согласия(зарегистрировать(почта())), [])

	def test_удаление_пользователя_не_мешает_и_запись_остаётся(self):
		"""Ссылка из записи согласия не запрещает удалить пользователя, а сама
		запись — доказательство — остаётся."""
		адрес = зарегистрировать(почта())
		frappe.set_user("Administrator")

		frappe.delete_doc("User", адрес, ignore_permissions=True)

		self.assertFalse(frappe.db.exists("User", адрес))
		self.assertEqual(len(согласия(адрес)), 1)

	def test_пустая_редакция_в_настройках_не_роняет_регистрацию(self):
		frappe.db.set_single_value(НАСТРОЙКИ, "legal_version", "")
		умолчание = frappe.get_meta(НАСТРОЙКИ).get_field("legal_version").default

		[запись] = согласия(зарегистрировать(почта()))

		self.assertTrue(умолчание)
		self.assertEqual(запись.documents_version, умолчание)

	def test_вход_существующего_ничего_не_записывает(self):
		"""Привязка Google к уже существующему аккаунту — сохранение, а не вставка."""
		адрес = зарегистрировать(почта())
		frappe.set_user("Guest")
		пользователь = frappe.get_doc("User", адрес)
		пользователь.append("social_logins", {"provider": "google", "userid": "id-existing"})
		пользователь.flags.ignore_permissions = True
		пользователь.save()

		self.assertEqual(len(согласия(адрес)), 1)


class IntegrationTestExplicitConsent(IntegrationTestCase):
	"""Явное согласие и экран согласия (learning-services#624)."""

	def setUp(self):
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")

	def test_отметки_формы_делают_регистрацию_явной(self):
		# Рядом — cookie выбора по аналитике `cenda_consent` (analytics.js): имя
		# отметок своё, и браузер присылает обе.
		with запрос(cookie=f"cenda_consent=granted; {consent.ЯВНОЕ_СОГЛАСИЕ}=1"):
			[с_отметками] = согласия(зарегистрировать(почта()))
			[через_google] = согласия(зарегистрировать(почта(), провайдер="google"))
		with запрос():
			[без_отметок] = согласия(зарегистрировать(почта()))

		self.assertEqual((с_отметками.explicit, с_отметками.age_confirmed), (1, 1))
		self.assertEqual((через_google.explicit, без_отметок.explicit), (0, 0), "отметок здесь не было")

	def test_без_явного_согласия_страница_ведёт_на_экран_согласия(self):
		адрес = зарегистрировать(почта(), провайдер="google")
		frappe.set_user(адрес)

		with запрос("/lms/courses?x=1"), self.assertRaises(HTTPException) as переход:
			consent.потребовать_согласие()
		ответ = переход.exception.get_response()
		self.assertEqual(ответ.status_code, 302)
		self.assertEqual(ответ.headers["Location"], "/consent?redirect-to=%2Flms%2Fcourses%3Fx%3D1")

		with self.subTest("вход «от имени» не ведёт на экран"), запрос("/lms"):
			frappe.session.data.impersonated_by = "Administrator"
			try:
				consent.потребовать_согласие()
			finally:
				frappe.session.data.pop("impersonated_by", None)

		with self.subTest("после согласия — пропускает"), запрос("/consent", "POST"):
			ответ = consent_api.accept(age_confirmed=1, documents_accepted="true")
			self.assertTrue(ответ["ok"], ответ)
			with запрос("/lms"):
				consent.потребовать_согласие()
			[_, явное] = sorted(согласия(адрес), key=lambda с: с.explicit)
			self.assertEqual(
				(явное.method, явное.explicit, явное.age_confirmed), (consent.ЭКРАН_СОГЛАСИЯ, 1, 1)
			)

	def test_согласие_даёт_только_сам_пользователь_и_обеими_отметками(self):
		адрес = зарегистрировать(почта(), провайдер="google")
		frappe.set_user(адрес)
		for что, (заголовки, отметки, код) in {
			"одна отметка": ({}, {"age_confirmed": 1, "documents_accepted": 0}, consent_api.НУЖНЫ_ОТМЕТКИ),
			"токен агента": (
				{"Authorization": "Bearer x"},
				{"age_confirmed": 1, "documents_accepted": 1},
				consent_api.ТОЛЬКО_САМ,
			),
		}.items():
			with self.subTest(что), запрос("/api/method/x", "POST", **заголовки):
				self.assertEqual(consent_api.accept(**отметки)["error"]["code"], код)
		self.assertFalse(consent.есть_явное(адрес))

		with запрос("/consent", "POST"):
			consent_api.accept(age_confirmed=1, documents_accepted=1)
			consent_api.accept(age_confirmed=1, documents_accepted=1)
		self.assertEqual(sum(с.explicit for с in согласия(адрес)), 1, "повтор ничего не пишет")

	def test_гость_и_administrator_на_экран_не_идут(self):
		with запрос("/lms"):
			consent.потребовать_согласие()
			frappe.set_user("Administrator")
			consent.потребовать_согласие()

	def test_новая_редакция_снова_просит_согласия(self):
		frappe.db.set_single_value("Agent Learning Settings", "legal_version", "2026-10-20")
		адрес = зарегистрировать(почта(), провайдер="google")
		frappe.set_user(адрес)
		with запрос("/consent", "POST"):
			consent_api.accept(age_confirmed=1, documents_accepted=1)
		self.assertTrue(consent.есть_явное(адрес))

		frappe.db.set_single_value("Agent Learning Settings", "legal_version", "2026-11-01")

		self.assertFalse(consent.есть_явное(адрес))


class IntegrationTestUnderage(IntegrationTestCase):
	"""Аккаунты без согласия: «Мне нет 18 лет» и чистка (learning-services#624)."""

	def setUp(self):
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")

	def заметка(self, адрес: str, **поля) -> None:
		frappe.get_doc(
			{
				"doctype": "Agent Student Note",
				"student": адрес,
				"kind": "Fact",
				"note_key": "role",
				"text": "x",
				**поля,
			}
		).insert(ignore_permissions=True)

	def способы(self, адрес: str) -> list[str]:
		return sorted(
			с.method for с in frappe.get_all("Agent Consent", filters={"user": адрес}, fields=["method"])
		)

	def отказаться(self, адрес: str) -> dict:
		frappe.set_user(адрес)
		with запрос("/consent", "POST"):
			ответ = consent_api.decline_underage()
		frappe.set_user("Administrator")
		return ответ

	def test_мне_нет_18_без_данных_удаляет_аккаунт_и_контакт(self):
		адрес = зарегистрировать(почта(), провайдер="google")

		ответ = self.отказаться(адрес)

		self.assertEqual(ответ["data"]["result"], "deleted")
		self.assertFalse(frappe.db.exists("User", адрес))
		self.assertFalse(frappe.db.exists("Contact Email", {"email_id": адрес}))
		self.assertEqual(self.способы(адрес), sorted([ВНЕШНИЙ_ВХОД, consent.НЕТ_18]), "заявление остаётся")

	def test_со_связанными_данными_аккаунт_закрывается(self):
		"""Связанные данные — любые ссылки на пользователя по проверке самого
		Frappe: и токен подключённого агента, которого ручной список не видел."""
		for что in ("заметка", "токен агента"):
			with self.subTest(что):
				адрес = зарегистрировать(почта(), провайдер="google")
				frappe.set_user("Administrator")
				if что == "заметка":
					self.заметка(адрес)
				else:
					self.токен(адрес)

				ответ = self.отказаться(адрес)

				self.assertEqual(ответ["data"]["result"], "closed")
				self.assertEqual(frappe.db.get_value("User", адрес, "enabled"), 0)
				self.assertIn(consent.НЕТ_18, self.способы(адрес))

	def токен(self, адрес: str) -> None:
		клиент = frappe.get_doc(
			{
				"doctype": "OAuth Client",
				"app_name": "Агент",
				"redirect_uris": "https://agent.example.com/cb",
				"default_redirect_uri": "https://agent.example.com/cb",
				"grant_type": "Authorization Code",
				"response_type": "Code",
				"scopes": "all",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "OAuth Bearer Token",
				"client": клиент.name,
				"user": адрес,
				"scopes": "all",
				"access_token": frappe.generate_hash(length=30),
				"refresh_token": frappe.generate_hash(length=30),
				"expires_in": 3600,
				"expiration_time": add_days(now_datetime(), 1),
			}
		).insert(ignore_permissions=True)

	def test_сообщить_о_возрасте_может_только_сам_и_не_сотрудник(self):
		адрес = зарегистрировать(почта(), провайдер="google")
		frappe.set_user(адрес)
		with запрос("/api/method/x", "POST", Authorization="Bearer x"):
			self.assertEqual(consent_api.decline_underage()["error"]["code"], consent_api.ТОЛЬКО_САМ)
		frappe.set_user("Administrator")
		frappe.db.set_value("User", адрес, "user_type", "System User")
		frappe.set_user(адрес)
		with запрос("/consent", "POST"):
			self.assertEqual(consent_api.decline_underage()["error"]["code"], consent_api.СОТРУДНИК)
		self.assertTrue(frappe.db.exists("User", адрес))

	def test_чистка_убирает_аккаунты_google_без_согласия(self):
		давно = add_days(now_datetime(), -consent.ДНЕЙ_БЕЗ_СОГЛАСИЯ - 5)
		без_согласия = зарегистрировать(почта(), провайдер="google")
		с_согласием = зарегистрировать(почта(), провайдер="google")
		с_данными = зарегистрировать(почта(), провайдер="google")
		свежий = зарегистрировать(почта(), провайдер="google")
		frappe.set_user("Administrator")
		ручной = зарегистрировать(почта())  # под администратором: записи согласия нет
		frappe.set_user(с_согласием)
		with запрос("/consent", "POST"):
			consent_api.accept(age_confirmed=1, documents_accepted=1)
		frappe.set_user("Administrator")
		self.заметка(с_данными)
		for адрес in (без_согласия, с_согласием, с_данными, ручной):
			frappe.db.set_value("Agent Consent", {"user": адрес}, "accepted_at", давно, update_modified=False)
			frappe.db.set_value("User", адрес, "creation", давно, update_modified=False)

		# Отсечка — раньше «давно»; коммит чистки в тесте не нужен.
		with patch.object(consent, "ЧИСТКА_С", add_days(давно, -1)), patch.object(frappe.db, "commit"):
			consent.удалить_без_согласия()
			consent.удалить_без_согласия()

		self.assertFalse(frappe.db.exists("User", без_согласия))
		self.assertEqual(frappe.db.get_value("User", с_данными, "enabled"), 0, "с данными — закрыт")
		self.assertEqual(self.способы(с_данными).count(consent.НЕ_ДАНО), 1, "повтор не убирает второй раз")
		for адрес in (с_согласием, свежий, ручной):
			self.assertEqual(frappe.db.get_value("User", адрес, "enabled"), 1, адрес)

	def test_сбой_на_одном_не_останавливает_чистку(self):
		давно = add_days(now_datetime(), -consent.ДНЕЙ_БЕЗ_СОГЛАСИЯ - 5)
		первый, второй = (зарегистрировать(почта(), провайдер="google") for _ in range(2))
		frappe.set_user("Administrator")
		for адрес in (первый, второй):
			frappe.db.set_value("Agent Consent", {"user": адрес}, "accepted_at", давно, update_modified=False)
		настоящий = consent.убрать_аккаунт

		def с_ошибкой(пользователь, причина):
			if пользователь == первый:
				raise frappe.ValidationError("сбой")
			return настоящий(пользователь, причина)

		with (
			patch.object(consent, "ЧИСТКА_С", add_days(давно, -1)),
			patch.object(frappe.db, "commit"),
			patch.object(consent, "убрать_аккаунт", с_ошибкой),
			patch.object(frappe, "log_error"),
		):
			consent.удалить_без_согласия()

		self.assertTrue(frappe.db.exists("User", первый))
		self.assertFalse(frappe.db.exists("User", второй))

	def test_прежние_аккаунты_чистка_не_трогает(self):
		"""Явного согласия у прежних пользователей нет ни у кого: без отсечки
		чистка удалила бы всех, кто не зашёл на сайт за месяц."""
		давно = add_days(now_datetime(), -consent.ДНЕЙ_БЕЗ_СОГЛАСИЯ - 5)
		прежний = зарегистрировать(почта(), провайдер="google")
		frappe.set_user("Administrator")
		frappe.db.set_value("Agent Consent", {"user": прежний}, "accepted_at", давно, update_modified=False)

		with patch.object(consent, "ЧИСТКА_С", add_days(давно, 1)), patch.object(frappe.db, "commit"):
			consent.удалить_без_согласия()

		self.assertTrue(frappe.db.exists("User", прежний))


class UnitTestПутиСогласия(UnitTestCase):
	def test_какие_страницы_ведут_на_экран_согласия(self):
		for путь, метод, ведёт in (
			("/lms", "GET", True),
			("/app/user", "GET", True),
			("/me", "GET", True),
			("/api/method/frappe.integrations.oauth2.authorize?client_id=x", "GET", True),
			("/api/method/lms_frappe_app.api.student.whoami", "GET", False),
			("/lms", "POST", False),
			("/consent", "GET", False),
			("/login", "GET", False),
			("/logout", "GET", False),
			("/assets/app.js", "GET", False),
			("/update-password", "GET", False),
			("/message?id=x", "GET", False),
			("/api/v2/method/frappe.integrations.oauth2.authorize", "GET", True),
		):
			with self.subTest(путь=путь, метод=метод):
				self.assertEqual(consent.путь_под_согласием(путь, метод), ведёт)

	def test_возврат_только_на_свой_адрес(self):
		for адрес, ожидается in (
			("/lms/courses", "/lms/courses"),
			(
				"/api/method/frappe.integrations.oauth2.authorize?a=1",
				"/api/method/frappe.integrations.oauth2.authorize?a=1",
			),
			("https://evil.example", ДОМОЙ),
			("//evil.example", ДОМОЙ),
			("/\\evil.example", ДОМОЙ),
			# Браузер выбрасывает табуляцию и переводы строк: стало бы //evil.
			("/\t/evil.example", ДОМОЙ),
			("/\n/evil.example", ДОМОЙ),
			("/%09/evil.example", "/%09/evil.example"),
			("/consent?redirect-to=/lms", ДОМОЙ),
			("", ДОМОЙ),
			(None, ДОМОЙ),
		):
			with self.subTest(адрес=адрес):
				self.assertEqual(consent.куда_вернуть(адрес, ДОМОЙ), ожидается)

	def test_гейт_подключён(self):
		self.assertIn(
			"lms_frappe_app.agent_learning.consent.потребовать_согласие", frappe.get_hooks("before_request")
		)


class UnitTestConsentNotice(UnitTestCase):
	def test_подпись_подключена_к_веб_страницам(self):
		"""Без подписи запись согласия ничего не доказывает: человек не видел,
		с чем соглашается."""
		адрес = "/assets/lms_frappe_app/js/consent-notice.js"
		self.assertIn(адрес, frappe.get_hooks("web_include_js"))
		# Аналитика — общий скрипт сайта cenda.app, а не своя копия
		# (learning-services#582).
		self.assertIn("/site/analytics.js", frappe.get_hooks("web_include_js"))
		файл = Path(frappe.get_app_path("lms_frappe_app", "public", "js", "consent-notice.js"))
		текст = файл.read_text(encoding="utf-8")
		for нужное in ('"/terms"', '"/privacy"', ".for-login", ".for-signup", consent.ЯВНОЕ_СОГЛАСИЕ):
			self.assertIn(нужное, текст)
