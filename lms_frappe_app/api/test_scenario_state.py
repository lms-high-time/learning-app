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
		self.другой = создать_ученика(f"scenario-other-{суффикс}@example.com")
		frappe.set_user(self.ученик)

	def запись(self, ученик: str | None = None):
		return frappe.db.get_value(
			"Agent Scenario State",
			{"student": ученик or self.ученик, "scenario_key": КЛЮЧ},
			["state", "state_version", "turns", "turns_since"],
			as_dict=True,
		)

	def test_без_сохранения_состояния_нет(self):
		данные = student.scenario_state(КЛЮЧ)["data"]

		self.assertEqual(данные, {"key": КЛЮЧ, "state": None, "version": None})

	def test_сохранённое_состояние_возвращается_как_есть(self):
		сохранено = student.save_scenario_state(КЛЮЧ, СОСТОЯНИЕ, "1")
		self.assertTrue(сохранено["ok"], сохранено.get("error"))
		self.assertEqual(сохранено["data"], {"key": КЛЮЧ, "version": "1"})

		данные = student.scenario_state(КЛЮЧ)["data"]

		self.assertEqual(данные, {"key": КЛЮЧ, "state": СОСТОЯНИЕ, "version": "1"})

	def test_повторное_сохранение_замещает_запись(self):
		student.save_scenario_state(КЛЮЧ, СОСТОЯНИЕ, "1")
		новое = json.dumps({"id": "conv-1", "messages": []})
		student.save_scenario_state(КЛЮЧ, новое, "2")

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

	def test_не_json_отклоняется(self):
		ответ = student.save_scenario_state(КЛЮЧ, "{обрыв", "1")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.НЕВЕРНОЕ_СОСТОЯНИЕ)
		self.assertIsNone(self.запись())

	def test_сброс_чистит_разговор_но_не_счётчик(self):
		"""Иначе лимит ходов обходился бы кнопкой «начать заново»."""
		student.count_scenario_turn(КЛЮЧ)
		student.count_scenario_turn(КЛЮЧ)
		student.save_scenario_state(КЛЮЧ, СОСТОЯНИЕ, "1")

		ответ = student.reset_scenario_state(КЛЮЧ)

		self.assertTrue(ответ["ok"], ответ.get("error"))
		self.assertEqual(
			student.scenario_state(КЛЮЧ)["data"], {"key": КЛЮЧ, "state": None, "version": None}
		)
		self.assertEqual(self.запись().turns, 2)
		self.assertEqual(student.count_scenario_turn(КЛЮЧ)["data"]["turns"], 3)

	def test_сброс_без_разговора_проходит(self):
		ответ = student.reset_scenario_state(КЛЮЧ)

		self.assertTrue(ответ["ok"], ответ.get("error"))

	def test_ходы_считаются_и_заводят_запись(self):
		первый = student.count_scenario_turn(КЛЮЧ)
		self.assertTrue(первый["ok"], первый.get("error"))
		self.assertEqual(первый["data"], {"key": КЛЮЧ, "turns": 1})

		self.assertEqual(student.count_scenario_turn(КЛЮЧ)["data"]["turns"], 2)
		self.assertIsNone(student.scenario_state(КЛЮЧ)["data"]["state"])

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

	def test_неверный_ключ_отклоняется(self):
		for ключ in ("", "Profile", "1profile", "lesson:1", "a" * 33, "profile\n", None):
			for ответ in (
				student.scenario_state(ключ),
				student.save_scenario_state(ключ, СОСТОЯНИЕ, "1"),
				student.reset_scenario_state(ключ),
				student.count_scenario_turn(ключ),
			):
				self.assertFalse(ответ["ok"], ключ)
				self.assertEqual(ответ["error"]["code"], student.НЕВЕРНЫЙ_КЛЮЧ_СЦЕНАРИЯ, ключ)
		self.assertFalse(frappe.db.exists("Agent Scenario State", {"student": self.ученик}))

	def test_ученики_с_одним_ключом_не_смешиваются(self):
		student.save_scenario_state(КЛЮЧ, СОСТОЯНИЕ, "1")
		student.count_scenario_turn(КЛЮЧ)

		frappe.set_user(self.другой)
		self.assertIsNone(student.scenario_state(КЛЮЧ)["data"]["state"])
		self.assertEqual(student.count_scenario_turn(КЛЮЧ)["data"]["turns"], 1)
		student.reset_scenario_state(КЛЮЧ)

		frappe.set_user(self.ученик)
		self.assertEqual(student.scenario_state(КЛЮЧ)["data"]["state"], СОСТОЯНИЕ)
		self.assertEqual(self.запись().turns, 1)

	def test_записи_ученик_напрямую_не_читает(self):
		"""Формат внутренний: отдаётся только методами, только владельцу."""
		student.save_scenario_state(КЛЮЧ, СОСТОЯНИЕ, "1")

		self.assertFalse(frappe.has_permission("Agent Scenario State", "read"))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("Agent Scenario State")
