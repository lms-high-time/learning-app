# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Патч `homework_practice`: признак обязательной у заведённых сдач, срок
практики снят (learning-services#627)."""

import io
from contextlib import redirect_stdout

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import homework as домашка
from lms_frappe_app.patches.v0_1 import homework_practice
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	создать_домашку,
	создать_занятие,
	создать_организацию,
	создать_урок,
	создать_ученика,
)

СРОК = "2030-01-01 10:00:00"


class IntegrationTestПатчПрактики(IntegrationTestCase):
	"""Сдачи до патча: у всех срок автора в сдаче и журнале выдачи, признака нет.
	Обязательная — в организации, назначившей курс; практика — в организации,
	где курс взят из каталога самим, и в личном пространстве."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		с = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"hwp-{с}@example.com")
		урок = создать_урок(f"Урок {с}")
		курс = зачислить(self.ученик, урок)
		задание = создать_домашку(урок, due_mode="relative", due_days=5)
		назначила = создать_организацию(f"Назначила {с}")
		сам = создать_организацию(f"Сам {с}")
		self.сдачи = {}
		for вид, организация, сам_выбрал in (("назначен", назначила, 0), ("из каталога", сам, 1), ("личное", None, 0)):
			if организация:
				добавить_в_организацию(self.ученик, организация)
				frappe.get_doc(
					{
						"doctype": "Course Allocation",
						"organization": организация,
						"course": курс,
						"audience": "Selected Members",
						"members": [{"user": self.ученик}],
						"chosen_by_member": сам_выбрал,
					}
				).insert(ignore_permissions=True)
			занятие = создать_занятие(self.ученик, урок)
			frappe.db.set_value("Agent Learning Session", занятие, "organization", организация)
			домашка.выдать(frappe.get_doc("Agent Learning Session", занятие))
			имя = домашка.найти_сдачу(задание.name, self.ученик, организация)
			frappe.db.set_value(домашка.СДАЧА, имя, {"required": 0, "due_at": СРОК}, update_modified=False)
			frappe.db.set_value(
				"Agent Homework Event", {"parent": имя}, {"due_at": СРОК, "due_source": "author"}
			)
			self.сдачи[вид] = имя

	def состояние(self) -> dict:
		итог = {}
		for вид, имя in self.сдачи.items():
			сдача = frappe.get_doc(домашка.СДАЧА, имя)
			[выдача] = сдача.history
			итог[вид] = (
				сдача.required,
				str(сдача.due_at) if сдача.due_at else None,
				str(выдача.due_at) if выдача.due_at else None,
				выдача.due_source or None,
			)
		return итог

	def test_признак_и_срок_практики(self):
		ожидаемо = {
			"назначен": (1, СРОК, СРОК, "author"),
			"из каталога": (0, None, None, None),
			"личное": (0, None, None, None),
		}
		for запуск in ("первый", "повторный ничего не меняет"):
			with self.subTest(запуск):
				with redirect_stdout(io.StringIO()):
					homework_practice.execute()
				self.assertEqual(self.состояние(), ожидаемо)
