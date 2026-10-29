# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Агенты и сессии пользователя (learning-services#669): список, отзыв,
завершение сессий и повторный ввод пароля."""

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.auth import get_login_attempt_tracker
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime
from frappe.utils.password import update_password

from lms_frappe_app import connections
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.oauth_server import ПОЛЕ_НАЧАЛА, ПОЛЕ_СЕМЬИ, СРОК_СЕМЬИ
from lms_frappe_app.tests.sample_data import создать_ученика

СЕЙЧАС = get_datetime("2026-10-22 12:00:00")


# --- данные: их заводят и тесты методов страницы ---


def завести_клиента(
	имя: str | None = None, app_name: str = "Агент", адрес: str = "https://claude.ai/cb"
) -> str:
	документ = frappe.get_doc(
		{
			"doctype": "OAuth Client",
			"app_name": app_name,
			"redirect_uris": адрес,
			"default_redirect_uri": адрес,
			"grant_type": "Authorization Code",
			"response_type": "Code",
			"scopes": "all",
		}
	)
	return документ.insert(ignore_permissions=True, set_name=имя).name


def завести_токен(клиент: str, пользователь: str, семья: str | None, статус: str = "Active") -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "OAuth Bearer Token",
				"client": клиент,
				"user": пользователь,
				"scopes": "all",
				"access_token": frappe.generate_hash(length=30),
				"refresh_token": frappe.generate_hash(length=30),
				"expires_in": 3600,
				"status": статус,
				ПОЛЕ_СЕМЬИ: семья,
				ПОЛЕ_НАЧАЛА: now_datetime() if семья else None,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def завести_код(клиент: str, пользователь: str) -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "OAuth Authorization Code",
				"client": клиент,
				"user": пользователь,
				"scopes": "all",
				"authorization_code": frappe.generate_hash(length=30),
				"validity": "Valid",
				"redirect_uri_bound_to_authorization_code": "https://claude.ai/cb",
				"expiration_time": add_to_date(now_datetime(), minutes=10),
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def завести_сессию(пользователь: str, **данные) -> str:
	sid = frappe.generate_hash()
	lastupdate = данные.pop("lastupdate", now_datetime())
	таблица = frappe.qb.DocType("Sessions")
	frappe.qb.into(таблица).columns(
		таблица.sid, таблица.user, таблица.sessiondata, таблица.lastupdate, таблица.status
	).insert(
		sid,
		пользователь,
		frappe.as_json({"creation": str(now_datetime()), "session_ip": "203.0.113.7", **данные}),
		lastupdate,
		"Active",
	).run()
	return sid


def есть_сессия(sid: str) -> bool:
	# `Sessions` — таблица без DocType: `frappe.db.exists` её не читает.
	таблица = frappe.qb.DocType("Sessions")
	return bool(frappe.qb.from_(таблица).select(таблица.sid).where(таблица.sid == sid).run())


class UnitTestConnections(UnitTestCase):
	def test_устройство_по_user_agent(self):
		случаи = {
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
			"Chrome/141.0.0.0 Safari/537.36": "Chrome, Windows",
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
			"Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0": "Edge, Windows",
			"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
			"Version/18.0 Safari/605.1.15": "Safari, macOS",
			"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
			"CriOS/141.0 Mobile/15E148 Safari/604.1": "Chrome, iOS",
			"Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) "
			"Chrome/141.0 YaBrowser/25.8 Mobile Safari/537.36": "Яндекс Браузер, Android",
			"Mozilla/5.0 (X11; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0": "Firefox, Linux",
			"curl/8.5.0": "Неизвестное устройство",
			None: "Неизвестное устройство",
		}
		# Названия — по языку страницы (learning-services#767): русские — по-русски.
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.local.lang = "ru"
		for ua, ожидается in случаи.items():
			with self.subTest(ua=ua):
				self.assertEqual(connections.устройство(ua), ожидается)
		frappe.local.lang = "en"
		with self.subTest("по-английски"):
			self.assertEqual(connections.устройство("Chrome/141.0 YaBrowser/25.8"), "Yandex Browser")
			self.assertEqual(connections.устройство(None), "Unknown device")

	def test_подключение_это_клиент_и_семья(self):
		def строка(имя, клиент, создан, семья=None, начало=None, срок=None):
			return frappe._dict(
				name=имя,
				client=клиент,
				creation=get_datetime(создан),
				expiration_time=срок,
				**{ПОЛЕ_СЕМЬИ: семья, ПОЛЕ_НАЧАЛА: начало},
			)

		давно = СЕЙЧАС - СРОК_СЕМЬИ - timedelta(days=1)
		итог = connections.свести_подключения(
			[
				строка("t1", "claude", "2026-10-20 10:00:00", "код-1", "2026-10-01 09:00:00"),
				строка("t2", "claude", "2026-10-22 11:00:00", "код-1", "2026-10-01 09:00:00"),
				# Второй вход того же агента — отдельное подключение.
				строка("t3", "claude", "2026-10-21 08:00:00", "код-2", "2026-10-21 08:00:00"),
				# До learning-services#665: без семьи — сам её корень.
				строка("старый", "gpt", "2026-09-01 08:00:00"),
				# Семья, которой вышел срок, больше не обновится…
				строка("t4", "claude", "2026-10-22 10:00:00", "код-3", давно, СЕЙЧАС - timedelta(hours=1)),
				# …но пока жив её токен доступа, его есть что отзывать.
				строка("t5", "claude", "2026-10-22 11:30:00", "код-4", давно, СЕЙЧАС + timedelta(minutes=30)),
			],
			СЕЙЧАС,
		)

		self.assertEqual(
			set(итог),
			{
				connections.отпечаток("claude", "код-1"),
				connections.отпечаток("claude", "код-2"),
				connections.отпечаток("gpt", "старый"),
				connections.отпечаток("claude", "код-4"),
			},
		)
		первое = итог[connections.отпечаток("claude", "код-1")]
		self.assertEqual(первое["connected"], get_datetime("2026-10-01 09:00:00"))
		self.assertEqual(первое["last_active"], get_datetime("2026-10-22 11:00:00"))
		self.assertEqual(
			итог[connections.отпечаток("gpt", "старый")]["connected"], get_datetime("2026-09-01 08:00:00")
		)

	def test_корень_без_семьи_и_его_преемник_одно_подключение(self):
		"""Преемник токена до learning-services#665 несёт семью — имя корня."""
		итог = connections.свести_подключения(
			[
				frappe._dict(name="корень", client="gpt", creation=get_datetime("2026-10-01 08:00:00")),
				frappe._dict(
					name="преемник",
					client="gpt",
					creation=get_datetime("2026-10-02 08:00:00"),
					**{ПОЛЕ_СЕМЬИ: "корень", ПОЛЕ_НАЧАЛА: get_datetime("2026-10-01 08:00:00")},
				),
			],
			СЕЙЧАС,
		)

		self.assertEqual(list(итог), [connections.отпечаток("gpt", "корень")])
		self.assertEqual(
			итог[connections.отпечаток("gpt", "корень")]["last_active"], get_datetime("2026-10-02 08:00:00")
		)

	def test_сайт_клиента_только_у_клиента_по_адресу(self):
		self.assertEqual(
			connections.сайт_клиента("https://claude.ai/oauth/claude-code-client-metadata"), "claude.ai"
		)
		self.assertIsNone(connections.сайт_клиента("a1b2c3d4e5"))


class IntegrationTestConnections(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.суффикс = frappe.generate_hash(length=8)

	def статус(self, токен: str) -> str:
		return frappe.db.get_value("OAuth Bearer Token", токен, "status")

	@contextmanager
	def отказ(self, код: str):
		with self.assertRaises(Отказ) as перехват:
			yield
		self.assertEqual(перехват.exception.код, код)

	# --- агенты ---

	def test_подключения_и_отключение_только_своего(self):
		ученик = создать_ученика(f"conn-{self.суффикс}@example.com")
		сосед = создать_ученика(f"conn-other-{self.суффикс}@example.com")
		claude = завести_клиента(f"https://claude.ai/oauth/test-{self.суффикс}", "Claude Code")
		другой = завести_клиента(app_name="Свой агент", адрес="https://agent.example/cb")
		семья = f"семья-{self.суффикс}"
		прежний = завести_токен(claude, ученик, семья, статус="Revoked")
		действующий = завести_токен(claude, ученик, семья)
		второй_вход = завести_токен(claude, ученик, f"второй-{self.суффикс}")
		у_другого = завести_токен(другой, ученик, f"другой-{self.суффикс}")
		соседа = завести_токен(claude, сосед, f"соседа-{self.суффикс}")
		код_ученика = завести_код(claude, ученик)
		код_соседа = завести_код(claude, сосед)

		строки = {с["id"]: с for с in connections.подключения(ученик)}

		with self.subTest("по строке на вход агента, только свои"):
			self.assertEqual(
				set(строки),
				{
					connections.отпечаток(claude, семья),
					connections.отпечаток(claude, f"второй-{self.суффикс}"),
					connections.отпечаток(другой, f"другой-{self.суффикс}"),
				},
			)
			строка = строки[connections.отпечаток(claude, семья)]
			self.assertEqual(
				(строка["agent"], строка["app_name"], строка["site"]), ("Claude", "Claude Code", "claude.ai")
			)
			self.assertEqual(строки[connections.отпечаток(другой, f"другой-{self.суффикс}")]["site"], None)
		with self.subTest("ни токенов, ни семей на странице"):
			текст = repr(list(строки.values()))
			for секрет in (действующий, прежний, семья):
				self.assertNotIn(секрет, текст)

		with (
			self.subTest("чужое подключение не находится и не отзывается"),
			self.отказ(connections.ПОДКЛЮЧЕНИЕ_НЕ_НАЙДЕНО),
		):
			connections.отключить(ученик, connections.отпечаток(claude, f"соседа-{self.суффикс}"))
		self.assertEqual(self.статус(соседа), "Active")

		connections.отключить(ученик, connections.отпечаток(claude, семья))

		with self.subTest("отозвана семья и коды этого агента"):
			self.assertEqual(self.статус(действующий), "Revoked")
			self.assertEqual(
				frappe.db.get_value("OAuth Authorization Code", код_ученика, "validity"), "Invalid"
			)
		with self.subTest("прочее не тронуто"):
			self.assertEqual(self.статус(второй_вход), "Active")
			self.assertEqual(self.статус(у_другого), "Active")
			self.assertEqual(self.статус(соседа), "Active")
			self.assertEqual(frappe.db.get_value("OAuth Authorization Code", код_соседа, "validity"), "Valid")
		with self.subTest("повтор — уже отключено"), self.отказ(connections.ПОДКЛЮЧЕНИЕ_НЕ_НАЙДЕНО):
			connections.отключить(ученик, connections.отпечаток(claude, семья))

	def test_токен_до_семей_отзывается_целиком(self):
		"""Токен до learning-services#665 без семьи — сам её корень."""
		ученик = создать_ученика(f"conn-old-{self.суффикс}@example.com")
		клиент = завести_клиента(app_name="ChatGPT", адрес="https://chatgpt.com/cb")
		старый = завести_токен(клиент, ученик, None)

		[строка] = connections.подключения(ученик)
		connections.отключить(ученик, строка["id"])

		self.assertEqual(self.статус(старый), "Revoked")

	# --- сессии ---

	def test_сессии_и_завершение_только_своих(self):
		ученик = создать_ученика(f"sess-{self.суффикс}@example.com")
		сосед = создать_ученика(f"sess-other-{self.суффикс}@example.com")
		chrome = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/141.0 Safari/537.36"
		текущая = завести_сессию(ученик, user_agent=chrome)
		другая = завести_сессию(ученик, user_agent="Mozilla/5.0 (X11; Linux x86_64) Firefox/142.0")
		третья = завести_сессию(ученик, impersonated_by="Administrator")
		простой = завести_сессию(ученик, lastupdate=add_to_date(now_datetime(), days=-400))
		вышел_срок = завести_сессию(ученик, session_end="2026-01-01T00:00:00+00:00")
		соседа = завести_сессию(сосед)

		список = connections.сессии(ученик, текущая)

		with self.subTest("действующие свои, текущая первой"):
			self.assertEqual(список[0]["id"], connections.отпечаток(текущая))
			self.assertEqual(
				{с["id"] for с in список[1:]}, {connections.отпечаток(sid) for sid in (другая, третья)}
			)
			self.assertEqual(
				(список[0]["current"], список[0]["device"], список[0]["ip"]),
				(True, "Chrome, Windows", "203.0.113.7"),
			)
			self.assertTrue(
				next(с for с in список if с["id"] == connections.отпечаток(третья))["impersonated"]
			)
		with self.subTest("sid на страницу не уходит"):
			текст = repr(список)
			for sid in (текущая, другая, третья):
				self.assertNotIn(sid, текст)

		with self.subTest("текущую — нельзя"), self.отказ(connections.ТЕКУЩАЯ_СЕССИЯ):
			connections.завершить_сессию(ученик, connections.отпечаток(текущая), текущая)
		with self.subTest("чужую — не найти"), self.отказ(connections.СЕССИЯ_НЕ_НАЙДЕНА):
			connections.завершить_сессию(ученик, connections.отпечаток(соседа), текущая)
		self.assertTrue(есть_сессия(соседа))

		connections.завершить_сессию(ученик, connections.отпечаток(другая), текущая)
		self.assertFalse(есть_сессия(другая))

		завершено = connections.завершить_другие(ученик, текущая)

		# Устаревшие удаляются, но в число не входят: на странице их не было.
		self.assertEqual(завершено, 1, "только третья")
		self.assertEqual(
			[есть_сессия(sid) for sid in (текущая, третья, простой, вышел_срок, соседа)],
			[True, False, False, False, True],
		)

	def test_подтверждение_паролем(self):
		ученик = создать_ученика(f"reauth-{self.суффикс}@example.com")
		пароль = f"Верный пароль {self.суффикс}"
		update_password(ученик, пароль)
		счётчик = get_login_attempt_tracker(ученик, raise_locked_exception=False)
		self.addCleanup(счётчик.add_success_attempt)

		for что, (введён, код) in {
			"пусто": ("", connections.НУЖЕН_ПАРОЛЬ),
			"неверный": ("не тот", connections.НЕВЕРНЫЙ_ПАРОЛЬ),
			"длиннее предела Frappe": ("x" * 513, connections.НЕВЕРНЫЙ_ПАРОЛЬ),
		}.items():
			with self.subTest(что), self.отказ(код):
				connections.подтвердить_паролем(ученик, введён)

		connections.подтвердить_паролем(ученик, пароль)
		self.assertFalse(счётчик.login_failed_count, "верный пароль сбрасывает счётчик")

		for _ in range(счётчик.max_failed_logins + 1):
			with self.отказ(connections.НЕВЕРНЫЙ_ПАРОЛЬ):
				connections.подтвердить_паролем(ученик, "не тот")
		with (
			self.subTest("счётчик закрыт — и верный пароль не проверяется"),
			self.отказ(connections.ПОПЫТКИ_КОНЧИЛИСЬ),
		):
			connections.подтвердить_паролем(ученик, пароль)

	def test_без_пароля_свой_отказ_и_не_в_счётчик(self):
		ученик = создать_ученика(f"reauth-none-{self.суффикс}@example.com")
		frappe.db.delete("__Auth", {"doctype": "User", "name": ученик})
		счётчик = get_login_attempt_tracker(ученик, raise_locked_exception=False)
		self.addCleanup(счётчик.add_success_attempt)

		with self.отказ(connections.ПАРОЛЯ_НЕТ):
			connections.подтвердить_паролем(ученик, "что угодно")

		self.assertFalse(счётчик.login_failed_count)

	def test_пароля_нет_в_трассировке_с_переменными(self):
		"""Регрессия learning-services#664: Frappe пишет в Error Log кадры с
		переменными и прячет только латинские имена с `password`."""
		ученик = создать_ученика(f"reauth-tb-{self.суффикс}@example.com")
		# В кадре самого теста — тоже: трассировка начинается с него.
		password = f"Секрет-трассировки-{self.суффикс}"
		update_password(ученик, password)

		# Функцией, а не `side_effect` MagicMock: кадры самого mock держат
		# аргументы в `args`, и трассировка показала бы их, а не наш код.
		def сбой(user, pwd):
			raise RuntimeError("сбой проверки")

		with patch.object(connections, "check_password", сбой):
			try:
				connections.подтвердить_паролем(ученик, password)
			except RuntimeError:
				трассировка = frappe.get_traceback(with_context=True)

		self.assertIn("подтвердить_паролем", трассировка)
		self.assertNotIn(password, трассировка)
