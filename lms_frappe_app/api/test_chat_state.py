# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Хранение состояния разговора веб-чата (lms-platform#138).

Здесь только хранение: состояние пишет и читает MCP-сервис, формат его
внутренний. Приложение отвечает за то, чтобы запись была одна на занятие и
доставалась только владельцу занятия.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	создать_занятие,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
	создать_урок,
)
from lms_frappe_app.api import student

СОСТОЯНИЕ = json.dumps({"id": "conv-1", "messages": [{"role": "user", "content": "Привет"}]})


class IntegrationTestChatState(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"chat-{суффикс}@example.com")
		self.урок = создать_урок(f"Чат {суффикс}")
		зачислить(self.ученик, self.урок)
		self.организация = создать_организацию(f"Чат-компания {суффикс}")
		добавить_в_организацию(self.ученик, self.организация)
		self.руководитель = создать_менеджера(f"chatboss-{суффикс}@example.com", self.организация)
		self.чужой = создать_ученика(f"chat-other-{суффикс}@example.com")
		self.чужое_занятие = создать_занятие(self.чужой, self.урок)

		frappe.set_user(self.ученик)
		self.занятие = создать_занятие(self.ученик, self.урок)

	def test_без_сохранения_состояния_нет(self):
		данные = student.chat_state(self.занятие)["data"]

		self.assertEqual(данные, {"session": self.занятие, "state": None, "version": None})

	def test_сохранённое_состояние_возвращается_как_есть(self):
		сохранено = student.save_chat_state(self.занятие, СОСТОЯНИЕ, "1")
		self.assertTrue(сохранено["ok"], сохранено.get("error"))

		данные = student.chat_state(self.занятие)["data"]

		self.assertEqual(данные["state"], СОСТОЯНИЕ)
		self.assertEqual(данные["version"], "1")

	def test_повторное_сохранение_замещает_запись(self):
		student.save_chat_state(self.занятие, СОСТОЯНИЕ, "1")
		новое = json.dumps({"id": "conv-1", "messages": []})
		student.save_chat_state(self.занятие, новое, "2")

		self.assertEqual(frappe.db.count("Agent Chat State", {"session": self.занятие}), 1)
		данные = student.chat_state(self.занятие)["data"]
		self.assertEqual((данные["state"], данные["version"]), (новое, "2"))

	def test_ученик_записи_берётся_из_занятия(self):
		student.save_chat_state(self.занятие, СОСТОЯНИЕ, "1")

		self.assertEqual(
			frappe.db.get_value("Agent Chat State", {"session": self.занятие}, "student"),
			self.ученик,
		)

	def test_чужое_занятие_не_читается(self):
		ответ = student.chat_state(self.чужое_занятие)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ЧУЖОЕ_ЗАНЯТИЕ)

	def test_в_чужое_занятие_не_пишется(self):
		ответ = student.save_chat_state(self.чужое_занятие, СОСТОЯНИЕ, "1")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ЧУЖОЕ_ЗАНЯТИЕ)
		self.assertFalse(frappe.db.exists("Agent Chat State", {"session": self.чужое_занятие}))

	def test_не_json_отклоняется(self):
		ответ = student.save_chat_state(self.занятие, "{обрыв", "1")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.НЕВЕРНОЕ_СОСТОЯНИЕ)

	def test_закрытое_занятие_принимает_состояние(self):
		# Квиз закрывает занятие посреди хода, а состояние пишется после хода:
		# отказ здесь терял бы последний ответ агента.
		frappe.db.set_value("Agent Learning Session", self.занятие, "status", "Completed")

		ответ = student.save_chat_state(self.занятие, СОСТОЯНИЕ, "1")

		self.assertTrue(ответ["ok"], ответ.get("error"))

	def test_записи_не_читают_ни_ученик_ни_руководитель(self):
		# Формат внутренний, а переписка — не отчётность: руководителю её не
		# отдаёт ни один метод, и прямого права на записи нет ни у кого из них.
		student.save_chat_state(self.занятие, СОСТОЯНИЕ, "1")
		for кто in (self.ученик, self.руководитель):
			frappe.set_user(кто)
			self.assertFalse(
				frappe.has_permission("Agent Chat State", "read"),
				f"{кто} получил доступ к состоянию разговора",
			)
