# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Вход по почте с паролем рядом со входом через Google (learning-services#460)."""

from contextlib import contextmanager, nullcontext
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from lms_frappe_app.access import (
	КЛЮЧ_ДЕЙСТВУЕТ,
	КЛЮЧ_НЕ_НАЙДЕН,
	КЛЮЧ_УСТАРЕЛ,
	ЛИМИТ_РЕГИСТРАЦИИ,
	ПРОВЕРЬТЕ_ПОЧТУ,
	sign_up,
	sign_up_learning,
	update_password,
	состояние_ключа,
)
from lms_frappe_app.tests.sample_data import создать_ученика

СРОК = "reset_password_link_expiry_duration"


def ученик() -> str:
	return создать_ученика(f"access-{frappe.generate_hash(length=8)}@example.com")


def со_сроком(секунды: int):
	"""Срок ссылки сброса только на время теста.

	`Why:` `get_system_settings` кэширует документ настроек в двух местах, и
	записанный в базу срок тест не видел — он получал срок предыдущего теста.
	"""
	настоящий = frappe.get_system_settings
	return patch(
		"frappe.get_system_settings", side_effect=lambda поле: секунды if поле == СРОК else настоящий(поле)
	)


def ключ(пользователь: str) -> str:
	"""Ключ из ссылки сброса — так же, как он приходит в письме."""
	return frappe.get_doc("User", пользователь)._reset_password().split("key=", 1)[1]


class IntegrationTestResetKey(IntegrationTestCase):
	"""Состояние ключа сброса: страница пароля решает по нему, что показать."""

	def test_состояние_ключа(self):
		"""Срок ключа и его следы в записи пользователя. Ключ без даты выдачи
		устарел: без даты срок не проверить, а Frappe на таком ключе падает с
		500. Frappe очищает ключ после смены пароля: использованный неотличим от
		опечатки. Каждый случай — у своего пользователя."""
		выдан = "last_reset_password_key_generated_on"
		случаи = {
			"просроченный ключ устарел": (выдан, add_to_date(now_datetime(), days=-1), 3600, КЛЮЧ_УСТАРЕЛ),
			"без срока ключ не стареет": (выдан, add_to_date(now_datetime(), days=-30), 0, КЛЮЧ_ДЕЙСТВУЕТ),
			"ключ без даты выдачи устарел": (выдан, None, 3600, КЛЮЧ_УСТАРЕЛ),
			"использованный ключ не найден": ("reset_password_key", "", None, КЛЮЧ_НЕ_НАЙДЕН),
		}
		for что, (поле, значение, срок, состояние) in случаи.items():
			with self.subTest(что):
				пользователь = ученик()
				свой = ключ(пользователь)
				frappe.db.set_value("User", пользователь, поле, значение)

				with со_сроком(срок) if срок is not None else nullcontext():
					self.assertEqual(состояние_ключа(свой), состояние)

		with self.subTest("выдуманный и пустой ключ не найдены"):
			self.assertEqual(состояние_ключа("выдуманный"), КЛЮЧ_НЕ_НАЙДЕН)
			self.assertEqual(состояние_ключа(""), КЛЮЧ_НЕ_НАЙДЕН)


НОВЫЙ_ПАРОЛЬ = "Новый-пароль-460!"


def менявший_пароль() -> tuple[str, str]:
	"""(ключ, пользователь): пароль уже менялся — дата смены есть."""
	пользователь = ученик()
	frappe.db.set_value("User", пользователь, "last_password_reset_date", "2026-01-01")
	return ключ(пользователь), пользователь


def без_пароля() -> tuple[str, str]:
	"""(ключ, пользователь): после регистрации или входа через Google человек
	пароля не менял."""
	пользователь = ученик()
	return ключ(пользователь), пользователь


def с_просроченным_ключом() -> tuple[str, str]:
	"""(ключ, пользователь): пароль менялся, ключ выдан сутки назад."""
	свой, пользователь = менявший_пароль()
	frappe.db.set_value(
		"User", пользователь, "last_reset_password_key_generated_on", add_to_date(now_datetime(), days=-1)
	)
	return свой, пользователь


def с_выдуманным_ключом() -> tuple[str, None]:
	return "выдуманный", None


class IntegrationTestPasswordChanged(IntegrationTestCase):
	"""Письмо «Пароль изменён» после смены пароля по ссылке."""

	@contextmanager
	def запрос(self):
		"""Запрос, вход и почта — заново на каждый случай.

		`update_password` Frappe входит под пользователем — нужен запрос.
		"""
		from frappe.auth import CookieManager, LoginManager
		from frappe.utils import set_request

		прежнее = {
			имя: getattr(frappe.local, имя, None) for имя in ("request", "cookie_manager", "login_manager")
		}
		set_request(method="POST", path="/")
		frappe.local.cookie_manager = CookieManager()
		frappe.local.login_manager = LoginManager()
		try:
			with (
				patch("frappe.sendmail") as письма,
				patch("lms_frappe_app.access.почта_есть", return_value=True),
			):
				yield письма
		finally:
			frappe.local.response.pop("http_status_code", None)
			frappe.set_user("Administrator")
			for имя, значение in прежнее.items():
				setattr(frappe.local, имя, значение)

	def test_письмо_только_после_смены_пароля(self):
		"""Письмо — только когда пароль менялся раньше и сменился сейчас.
		Первое задание пароля — без письма. Просроченный ключ найден, но Frappe
		отказал (410): пароль не менялся. Недействительный ключ — тоже 410."""
		случаи = {
			"смена пароля по ссылке присылает письмо": (менявший_пароль, None, "Пароль изменён", None),
			"первое задание пароля без письма": (без_пароля, None, None, None),
			"просроченный ключ без письма": (с_просроченным_ключом, 3600, None, 410),
			"недействительный ключ без письма": (с_выдуманным_ключом, None, None, 410),
		}
		for что, (подготовка, срок, тема, код) in случаи.items():
			with self.subTest(что), self.запрос() as письма:
				свой, пользователь = подготовка()

				with со_сроком(срок) if срок is not None else nullcontext():
					update_password(НОВЫЙ_ПАРОЛЬ, key=свой)

				self.assertEqual(frappe.local.response.get("http_status_code"), код)
				self.assertEqual(
					[(в.kwargs["recipients"], в.kwargs["subject"]) for в in письма.call_args_list],
					[([пользователь], тема)] if тема else [],
				)

	def test_перекрытия_подключены(self):
		"""Смена пароля и обе формы регистрации идут через приложение."""
		перекрытия = frappe.get_hooks("override_whitelisted_methods")
		for метод, наш in (
			("frappe.core.doctype.user.user.update_password", "lms_frappe_app.access.update_password"),
			("frappe.core.doctype.user.user.sign_up", "lms_frappe_app.access.sign_up"),
			("lms.lms.user.sign_up", "lms_frappe_app.access.sign_up_learning"),
		):
			with self.subTest(метод):
				self.assertEqual(перекрытия[метод][-1], наш)


class IntegrationTestSignUp(IntegrationTestCase):
	"""Регистрация: новый, занятый и отключённый адрес получают один ответ."""

	def setUp(self):
		from frappe.auth import CookieManager, LoginManager
		from frappe.utils import set_request

		self.прежнее = {
			имя: getattr(frappe.local, имя, None)
			for имя in ("request", "request_ip", "cookie_manager", "login_manager")
		}
		# `sanitize_redirect` и лимит частоты читают запрос, `update_password`
		# входит под пользователем.
		set_request(method="POST", path="/")
		frappe.local.request_ip = "127.0.0.1"
		frappe.local.cookie_manager = CookieManager()
		frappe.local.login_manager = LoginManager()
		self.письма = patch("frappe.sendmail").start()
		self.почта = patch("lms_frappe_app.access.почта_есть", return_value=True)
		self.почта.start()
		# Регистрация открыта, как на стенде; локальный сайт может быть закрыт.
		for модуль in ("lms_frappe_app.access", "frappe.core.doctype.user.user", "lms.lms.user"):
			patch(f"{модуль}.is_signup_disabled", return_value=False).start()
		self.сбросить_лимит()
		# Форму на `/login` отправляет гость.
		frappe.set_user("Guest")

	def tearDown(self):
		patch.stopall()
		self.сбросить_лимит()
		frappe.form_dict.clear()
		frappe.local.response.pop("http_status_code", None)
		frappe.set_user("Administrator")
		for имя, значение in self.прежнее.items():
			setattr(frappe.local, имя, значение)

	def сбросить_лимит(self):
		# Только счётчик регистрации: сайт общий с соседними прогонами.
		frappe.cache.delete_keys(f"rl:{ЛИМИТ_РЕГИСТРАЦИИ}:")

	def новый_адрес(self) -> str:
		return f"signup-{frappe.generate_hash(length=8)}@example.com"

	def test_после_пароля_возврат_туда_откуда_пришёл_и_без_кэша(self):
		"""Кэш Redis может вытесниться, пока человек идёт к письму."""
		адрес = self.новый_адрес()
		self.assertEqual(sign_up(адрес, "Новый Ученик", "/lms/join/abc"), (1, ПРОВЕРЬТЕ_ПОЧТУ))
		frappe.cache.hdel("redirect_after_login", адрес)

		переход = update_password(НОВЫЙ_ПАРОЛЬ, key=ключ(адрес))

		self.assertTrue(переход.endswith("/lms/join/abc"), переход)

	def test_занятый_адрес_получает_ссылку_на_пароль(self):
		"""«Already Registered» — тупик для вошедшего через Google (learning-services#459)."""
		пользователь = ученик()

		self.assertEqual(sign_up(пользователь, "Кто-то", ""), (1, ПРОВЕРЬТЕ_ПОЧТУ))
		письмо = self.письма.call_args.kwargs
		self.assertEqual(письмо["recipients"], [пользователь])
		self.assertIn("/update-password?key=", письмо["message"])

	def test_новый_и_занятый_адрес_неразличимы(self):
		"""По ответу формы не узнать, зарегистрирован ли чужой адрес."""
		ответы = []
		for адрес in (self.новый_адрес(), ученик()):
			frappe.local.message_log = []
			ответы.append((sign_up(адрес, "Кто-то", ""), list(frappe.local.message_log)))

		self.assertEqual(ответы[0], ответы[1])

	def test_адрес_в_другом_написании_тоже_занят(self):
		"""Пробел или регистр не должны вести мимо занятого адреса в ошибку Frappe."""
		пользователь = ученик()

		for написание in (" " + пользователь, пользователь.upper() + "\n"):
			self.assertEqual(sign_up(написание, "Кто-то", ""), (1, ПРОВЕРЬТЕ_ПОЧТУ), написание)
			self.assertEqual(sign_up_learning(написание, "Кто-то", 1, ""), (1, ПРОВЕРЬТЕ_ПОЧТУ), написание)
		self.assertEqual(
			{tuple(вызов.kwargs["recipients"]) for вызов in self.письма.call_args_list}, {(пользователь,)}
		)

	def test_лимит_общий_для_любого_написания_адреса(self):
		"""Против рассылки писем на чужой ящик через форму."""
		пользователь = ученик()
		for попытка in range(5):
			sign_up(пользователь if попытка % 2 else пользователь.upper(), "Кто-то", "")

		# Лимит — по адресу, а не по IP (обоснование в `access.py`).
		self.assertEqual(sign_up(ученик(), "Кто-то", ""), (1, ПРОВЕРЬТЕ_ПОЧТУ))
		with self.assertRaises(frappe.RateLimitExceededError):
			sign_up(" " + пользователь, "Кто-то", "")

	def test_лимит_общий_для_обеих_форм(self):
		"""Счётчик Frappe ключуется вызванным методом: без общего ключа каждая
		форма и `/api/v2` давали бы свои пять писем в час на один ящик."""
		пользователь = ученик()
		for попытка in range(5):
			if попытка % 2:
				frappe.form_dict.cmd = "lms.lms.user.sign_up"
				sign_up_learning(пользователь, "Кто-то", 1, "")
			else:
				frappe.form_dict.cmd = "frappe.core.doctype.user.user.sign_up"
				sign_up(пользователь, "Кто-то", "")

		frappe.form_dict.cmd = None
		with self.assertRaises(frappe.RateLimitExceededError):
			sign_up(пользователь, "Кто-то", "")

	def test_пустой_адрес_отклоняется_понятно(self):
		with self.assertRaises(frappe.ValidationError) as отказ:
			sign_up("  ", "Кто-то", "")
		self.assertNotIn("Either key or IP", str(отказ.exception))

	def test_отключённый_адрес_отвечает_так_же_и_без_письма(self):
		пользователь = ученик()
		frappe.db.set_value("User", пользователь, "enabled", 0)

		self.assertEqual(sign_up(пользователь, "Кто-то", ""), (1, ПРОВЕРЬТЕ_ПОЧТУ))
		self.письма.assert_not_called()

	def test_служебным_пользователям_ссылки_нет(self):
		"""`reset_password` Frappe их пропускает — и регистрация не должна выдавать."""
		for служебный in frappe.STANDARD_USERS:
			адрес = frappe.db.get_value("User", служебный, "email")
			self.assertEqual(sign_up(адрес, "Кто-то", ""), (1, ПРОВЕРЬТЕ_ПОЧТУ), служебный)
		self.письма.assert_not_called()

	def test_без_почты_занятый_адрес_отвечает_как_новый(self):
		"""Новому адресу без почты Frappe отвечает «попросите администратора»."""
		self.почта.stop()
		with patch("lms_frappe_app.access.почта_есть", return_value=False):
			код, текст = sign_up(ученик(), "Кто-то", "")

		self.assertEqual(код, 2)
		self.assertNotEqual(текст, ПРОВЕРЬТЕ_ПОЧТУ)
		self.почта.start()

	def test_закрытая_регистрация_закрыта_и_для_занятого_адреса(self):
		with patch("lms_frappe_app.access.is_signup_disabled", return_value=True):
			with self.assertRaises(frappe.ValidationError):
				sign_up(ученик(), "Кто-то", "")
		self.письма.assert_not_called()

	def test_форма_learning_создаёт_аккаунт(self):
		адрес = self.новый_адрес()

		self.assertEqual(sign_up_learning(адрес, "Новый Ученик", 1, ""), (1, ПРОВЕРЬТЕ_ПОЧТУ))
		self.assertTrue(frappe.db.exists("User", адрес))
