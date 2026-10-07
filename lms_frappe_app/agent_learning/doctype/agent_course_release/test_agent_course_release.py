# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from lms_frappe_app.tests.sample_data import создать_курс

DOCTYPE = "Agent Course Release"


def вставить_релиз(курс: str, версия: int = 1, снимок: str = "{}") -> str:
	"""Релиз без индекса — запись, на которую можно сослаться. Без проверки
	прав, как у сервиса публикации: права `create` у релиза нет ни у кого."""
	return (
		frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"course": курс,
				"course_key": f"test-{frappe.generate_hash(length=6)}",
				"version": версия,
				"release_format": "lms-release/1",
				"digest": frappe.generate_hash(length=64),
				"published_by": "Administrator",
				"published_at": now_datetime(),
				"snapshot": снимок,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


class IntegrationTestAgentCourseRelease(IntegrationTestCase):
	"""Релиз курса неизменяем: исправление — новый релиз (learning-services#500)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		self.курс = создать_курс(f"Курс {frappe.generate_hash(length=6)}")

	def test_релиз_не_правится_даже_администратором(self):
		имя = вставить_релиз(self.курс)
		релиз = frappe.get_doc(DOCTYPE, имя)
		релиз.snapshot = '{"format": "подмена"}'
		with self.assertRaises(frappe.ValidationError):
			релиз.save()
		self.assertEqual(frappe.db.get_value(DOCTYPE, имя, "snapshot"), "{}")

	def test_релиз_не_удаляется(self):
		имя = вставить_релиз(self.курс)
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(DOCTYPE, имя)
		self.assertTrue(frappe.db.exists(DOCTYPE, имя))

	def test_версия_одна_на_курс(self):
		вставить_релиз(self.курс)
		with self.assertRaises(frappe.UniqueValidationError):
			вставить_релиз(self.курс)
