# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Хранение разговоров сценариев вне урока (learning-services#463).

Как и у урочного чата, здесь только хранение: состояние пишет и читает
MCP-сервис, формат его внутренний. Приложение отвечает за то, чтобы запись
была одна на ученика и ключ сценария, доставалась только владельцу, а
счётчик ходов не обходился сбросом разговора.
"""

import json
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from lms_frappe_app.api import student
from lms_frappe_app.tests.sample_data import создать_ученика

СОСТОЯНИЕ = json.dumps({"id": "conv-1", "messages": [{"role": "user", "content": "Привет"}]})
КЛЮЧ = "profile"


class IntegrationTestScenarioState(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"scenario-{суффикс}@example.com")
		frappe.set_user(self.ученик)

	def запись(self, ученик: str | None = None):
		return frappe.db.get_value(
			"Agent Scenario State",
			{"student": ученик or self.ученик, "scenario_key": КЛЮЧ},
			["state", "state_version", "turns", "turns_since"],
			as_dict=True,
		)

	def test_сохранённое_состояние_возвращается_и_замещается(self):
		"""Без сохранения состояния нет; сохранённое возвращается как есть;
		повторное сохранение замещает запись, а не заводит вторую."""
		with self.subTest("без сохранения"):
			данные = student.scenario_state(КЛЮЧ)["data"]
			self.assertEqual(данные, {"key": КЛЮЧ, "state": None, "version": None})

		сохранено = student.save_scenario_state(КЛЮЧ, СОСТОЯНИЕ, "1")
		with self.subTest("сохранённое возвращается как есть"):
			self.assertTrue(сохранено["ok"], сохранено.get("error"))
			self.assertEqual(сохранено["data"], {"key": КЛЮЧ, "version": "1"})
			данные = student.scenario_state(КЛЮЧ)["data"]
			self.assertEqual(данные, {"key": КЛЮЧ, "state": СОСТОЯНИЕ, "version": "1"})
			self.assertEqual(self.запись().turns, 0)

		новое = json.dumps({"id": "conv-1", "messages": []})
		student.save_scenario_state(КЛЮЧ, новое, "2")
		with self.subTest("повторное сохранение замещает запись"):
			self.assertEqual(
				frappe.db.count("Agent Scenario State", {"student": self.ученик, "scenario_key": КЛЮЧ}), 1
			)
			данные = student.scenario_state(КЛЮЧ)["data"]
			self.assertEqual((данные["state"], данные["version"]), (новое, "2"))

	def test_вторая_запись_того_же_ключа_не_заводится(self):
		"""Уникальность держит база: метод ищет запись перед созданием, но
		гонку двух вызовов и правку из админки держит только индекс."""
		student.save_scenario_state(КЛЮЧ, СОСТОЯНИЕ, "1")
		frappe.set_user("Administrator")

		with self.assertRaises(frappe.UniqueValidationError):
			frappe.get_doc(
				{"doctype": "Agent Scenario State", "student": self.ученик, "scenario_key": КЛЮЧ}
			).insert(ignore_permissions=True)

	def test_сброс_чистит_разговор_но_не_счётчик(self):
		"""Иначе лимит ходов обходился бы кнопкой «начать заново». Сброс без
		разговора проходит — первым: записи он не заводит."""
		with self.subTest("сброс без разговора"):
			ответ = student.reset_scenario_state(КЛЮЧ)
			self.assertTrue(ответ["ok"], ответ.get("error"))

		with self.subTest("сброс разговора"):
			self.assertEqual(student.count_scenario_turn(КЛЮЧ)["data"], {"key": КЛЮЧ, "turns": 1})
			student.count_scenario_turn(КЛЮЧ)
			self.assertIsNone(student.scenario_state(КЛЮЧ)["data"]["state"])
			student.save_scenario_state(КЛЮЧ, СОСТОЯНИЕ, "1")

			ответ = student.reset_scenario_state(КЛЮЧ)

			self.assertTrue(ответ["ok"], ответ.get("error"))
			self.assertEqual(
				student.scenario_state(КЛЮЧ)["data"], {"key": КЛЮЧ, "state": None, "version": None}
			)
			self.assertEqual(self.запись().turns, 2)
			self.assertEqual(student.count_scenario_turn(КЛЮЧ)["data"]["turns"], 3)

	def test_счёт_ходов_идёт_за_сутки(self):
		"""Лимит навсегда закрыл бы профиль ученику, вернувшемуся через полгода."""
		student.count_scenario_turn(КЛЮЧ)
		student.count_scenario_turn(КЛЮЧ)
		имя = frappe.db.get_value(
			"Agent Scenario State", {"student": self.ученик, "scenario_key": КЛЮЧ}
		)
		frappe.db.set_value(
			"Agent Scenario State", имя, "turns_since", now_datetime() - timedelta(days=1, minutes=1)
		)

		self.assertEqual(student.count_scenario_turn(КЛЮЧ)["data"]["turns"], 1)
		self.assertGreater(self.запись().turns_since, now_datetime() - timedelta(minutes=1))
		self.assertEqual(student.count_scenario_turn(КЛЮЧ)["data"]["turns"], 2)

	def гонка(self, функция: str, *исходы):
		"""Запись разговора по очереди: исключение — бросить, `None` — настоящая.

		`Why:` гонку MariaDB стенда в тесте не вызвать — одна транзакция;
		заглушка отдаёт её так, как база отдала бы второму из двух ходов разом.
		Откат подменён: настоящий снял бы данные теста.
		"""
		настоящая = getattr(student, функция)
		очередь = list(исходы)

		def запись(*args, **kwargs):
			исход = очередь.pop(0)
			if callable(исход):
				исход = исход()
			if исход is None:
				return настоящая(*args, **kwargs)
			raise исход

		return patch.object(student, функция, side_effect=запись), patch.object(frappe.db, "rollback")

	def вызовы(self):
		"""Метод и его запись: ход и сохранение разговора гонку переживают одинаково."""
		return (
			("_засчитать_ход", lambda: student.count_scenario_turn(КЛЮЧ)),
			("_сохранить_разговор", lambda: student.save_scenario_state(КЛЮЧ, СОСТОЯНИЕ, "1")),
			("_сбросить_разговор", lambda: student.reset_scenario_state(КЛЮЧ)),
		)

	def test_гонка_дважды_даёт_busy_а_один_раз_повторяется(self):
		"""`busy` — первыми: отказ записи не оставляет, и повторы идут с чистого
		листа."""
		for функция, вызов in self.вызовы():
			with self.subTest("дважды", функция=функция):
				подмена, откат = self.гонка(
					функция, frappe.QueryDeadlockError("1020"), frappe.QueryDeadlockError("1020")
				)
				with подмена, откат:
					ответ = вызов()
				self.assertFalse(ответ["ok"], функция)
				self.assertEqual(
					(ответ["error"]["code"], ответ["error"]["key"]), ("busy", КЛЮЧ), функция
				)
		self.assertIsNone(self.запись(), "busy записи не оставляет")

		for функция, вызов in self.вызовы():
			for ошибка in (
				frappe.QueryDeadlockError("1020"),
				frappe.QueryTimeoutError("1205"),
				frappe.UniqueValidationError("1062"),
			):
				with self.subTest("один раз", функция=функция, ошибка=type(ошибка).__name__):
					подмена, откат = self.гонка(функция, ошибка, None)
					with подмена, откат:
						ответ = вызов()
					self.assertTrue(ответ["ok"], (функция, ошибка, ответ))
		self.assertEqual(self.запись().state, None, "сброс шёл последним")
		self.assertEqual(self.запись().turns, 3)

	def test_сообщение_о_дубле_не_уходит_в_ответ(self):
		"""Frappe кладёт «must be unique» в журнал перед исключением; при
		успешном повторе оно ушло бы в ответ ученику."""

		def дубль():
			frappe.msgprint("Student must be unique")
			return frappe.UniqueValidationError("1062")

		сообщений = len(frappe.local.message_log)
		подмена, откат = self.гонка("_засчитать_ход", дубль, None)
		with подмена, откат:
			ответ = student.count_scenario_turn(КЛЮЧ)

		self.assertEqual(ответ["data"]["turns"], 1)
		self.assertEqual(len(frappe.local.message_log), сообщений)

	def test_неверные_ключ_и_состояние_отклоняются(self):
		"""Отказ записи не заводит."""
		for ключ in ("", "Profile", "1profile", "lesson:1", "a" * 33, "profile\n", None):
			with self.subTest(ключ=ключ):
				for ответ in (
					student.scenario_state(ключ),
					student.save_scenario_state(ключ, СОСТОЯНИЕ, "1"),
					student.reset_scenario_state(ключ),
					student.count_scenario_turn(ключ),
				):
					self.assertFalse(ответ["ok"], ключ)
					self.assertEqual(ответ["error"]["code"], student.НЕВЕРНЫЙ_КЛЮЧ_СЦЕНАРИЯ, ключ)

		with self.subTest("состояние не JSON"):
			ответ = student.save_scenario_state(КЛЮЧ, "{обрыв", "1")

			self.assertFalse(ответ["ok"])
			self.assertEqual(ответ["error"]["code"], student.НЕВЕРНОЕ_СОСТОЯНИЕ)
			self.assertIsNone(self.запись())

		with self.subTest("состояние больше предела"):
			from unittest.mock import patch

			with patch.object(student, "ДЛИНА_СОСТОЯНИЯ", len(СОСТОЯНИЕ.encode()) - 1):
				ответ = student.save_scenario_state(КЛЮЧ, СОСТОЯНИЕ, "1")
			self.assertEqual(ответ["error"]["code"], student.ДЛИННОЕ_СОСТОЯНИЕ)
			self.assertIsNone(self.запись())
		self.assertFalse(frappe.db.exists("Agent Scenario State", {"student": self.ученик}))

	def test_ученики_с_одним_ключом_не_смешиваются(self):
		frappe.set_user("Administrator")
		try:
			другой = создать_ученика(f"scenario-other-{frappe.generate_hash(length=6)}@example.com")
		finally:
			frappe.set_user(self.ученик)
		student.save_scenario_state(КЛЮЧ, СОСТОЯНИЕ, "1")
		student.count_scenario_turn(КЛЮЧ)

		frappe.set_user(другой)
		try:
			self.assertIsNone(student.scenario_state(КЛЮЧ)["data"]["state"])
			self.assertEqual(student.count_scenario_turn(КЛЮЧ)["data"]["turns"], 1)
			student.reset_scenario_state(КЛЮЧ)
		finally:
			frappe.set_user(self.ученик)

		self.assertEqual(student.scenario_state(КЛЮЧ)["data"]["state"], СОСТОЯНИЕ)
		self.assertEqual(self.запись().turns, 1)

	def test_записи_ученик_напрямую_не_читает(self):
		"""Формат внутренний: отдаётся только методами, только владельцу."""
		student.save_scenario_state(КЛЮЧ, СОСТОЯНИЕ, "1")

		self.assertFalse(frappe.has_permission("Agent Scenario State", "read"))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("Agent Scenario State")
