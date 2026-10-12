# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import создать_курс, создать_ученика

DOCTYPE = "Agent Course Artifact"


def блок(ключ: str, название: str) -> dict:
	return {"block_key": ключ, "title": название, "rows": "one", "columns": "[]"}


class IntegrationTestAgentCourseArtifact(IntegrationTestCase):
	"""Схема артефакта: версии по паре «курс + ключ», ключи блоков, доступ."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.курс = создать_курс(f"Курс {frappe.generate_hash(length=6)}")

	def схема(self, slug: str = "project_summary", **поля):
		return frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"course": self.курс,
				"slug": slug,
				"title": "Резюме проекта",
				"blocks": [
					блок("goal", "Цель"),
					блок("sponsor", "Спонсор"),
				],
				**поля,
			}
		).insert(ignore_permissions=True)

	def test_версии_по_курсу_и_ключу(self):
		"""Ради этого миксин и обобщали: в курсе несколько документов, и
		версии у каждого свои; новая версия того же документа вытесняет прежнюю."""
		резюме = self.схема("project_summary")
		карта = self.схема("deliverables_map")
		резюме.reload()
		with self.subTest("версия считается по курсу и ключу"):
			self.assertEqual(резюме.version, 1)
			self.assertEqual(карта.version, 1)
			self.assertTrue(резюме.is_active, "схема другого документа не снимает эту с действия")
			self.assertTrue(карта.is_active)

		вторая = self.схема("project_summary")
		резюме.reload()
		карта.reload()
		with self.subTest("новая версия того же документа вытесняет прежнюю"):
			self.assertEqual(вторая.version, 2)
			self.assertTrue(вторая.is_active)
			self.assertFalse(резюме.is_active)
			self.assertTrue(карта.is_active, "новая версия другого документа не снимает карту с действия")

	def test_ключи_блоков(self):
		"""Повторяющийся ключ блока отклоняется — до первой схемы курса; ключи
		схемы и блоков нормализуются."""
		with self.subTest("повторяющийся ключ блока отклоняется"):
			with self.assertRaises(frappe.ValidationError):
				self.схема(
					blocks=[
						блок("goal", "Цель"),
						блок("GOAL", "Цель ещё раз"),
					]
				)

		with self.subTest("ключи нормализуются"):
			схема = self.схема(" Project_Summary ", blocks=[блок(" Goal ", "Цель")])

			self.assertEqual(схема.slug, "project_summary")
			self.assertEqual(схема.blocks[0].block_key, "goal")

	def test_ученик_не_имеет_доступа_к_схеме(self):
		"""Схема адресована автору; ученику блоки приходят через метод."""
		ученик = создать_ученика(f"art-{frappe.generate_hash(length=6)}@example.com")
		self.схема()
		frappe.set_user(ученик)

		self.assertFalse(frappe.has_permission(DOCTYPE, "read"))
		self.assertFalse(frappe.has_permission(DOCTYPE, "write"))
