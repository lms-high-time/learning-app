# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.reports import НУЖЕН_ОТВЕТ_УЧЕНИКУ
from lms_frappe_app.tests.sample_data import (
	зачислить,
	создать_занятие,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
	создать_урок,
)


class IntegrationTestAgentCourseReport(IntegrationTestCase):
	"""Репорт агента о курсе: хранение и очередь разбора."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"rep-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(self.ученик, self.урок)
		self.занятие = создать_занятие(self.ученик, self.урок)

	def test_новый_репорт_ждёт_разбора(self):
		"""Why: статус — очередь методолога. Репорт, заведённый без статуса,
		выпал бы из фильтра «новые» и не был бы разобран никогда."""
		репорт = frappe.get_doc(
			{
				"doctype": "Agent Course Report",
				"session": self.занятие,
				"course": self.курс,
				"lesson": self.урок,
				"kind": "Material Issue",
				"text": "В примере перепутаны роли",
			}
		).insert()

		self.assertEqual(репорт.status, "New")

	def test_дата_итога_ставится_при_закрытии_и_снимается_при_переоткрытии(self):
		"""Why: дата итога — то, по чему ученик узнаёт «когда ответили», а
		отметка «ученик узнал» — по чему итог приходит ему один раз. Новый итог
		после переоткрытия — другая новость, и отметка снимается, чтобы она
		дошла; статус, сменённый в desk, обязан вести себя так же, поэтому
		проверяется запись, а не метод. Ответ закрытого репорта в desk правится,
		но пустым не становится — те же правила, что у `resolve_report`."""
		репорт = frappe.get_doc(
			{
				"doctype": "Agent Course Report",
				"session": self.занятие,
				"course": self.курс,
				"lesson": self.урок,
				"kind": "Material Issue",
				"text": "В примере перепутаны роли",
			}
		).insert()
		self.assertIsNone(репорт.resolved_at)

		репорт.status = "Fixed"
		репорт.resolution = "Поправили пример"
		репорт.save()
		self.assertTrue(репорт.resolved_at)
		репорт.db_set("student_notified_at", frappe.utils.now_datetime())

		репорт.resolution = "Поправили пример и вопрос"
		репорт.save()
		self.assertTrue(репорт.student_notified_at, "правка ответа — не новый итог")
		репорт.resolution = ""
		with self.assertRaises(Отказ) as пойман:
			репорт.save()
		self.assertEqual(пойман.exception.код, НУЖЕН_ОТВЕТ_УЧЕНИКУ, "ответ правят, но не стирают")
		репорт.reload()

		репорт.status = "In Progress"
		репорт.save()
		self.assertIsNone(репорт.resolved_at)
		self.assertIsNone(репорт.resolution, "переоткрытый репорт не держит прежний итог")

		репорт.status = "Rejected"
		репорт.resolution = "Так задумано"
		репорт.save()
		self.assertTrue(репорт.resolved_at)
		self.assertIsNone(репорт.student_notified_at)

	def test_репорты_не_читают_ни_ученик_ни_руководитель(self):
		"""Why: прав на доктайп у этих ролей нет вовсе. Ученик читает свои
		репорты только методом `my_reports` — по своим занятиям и без полей,
		которые ему ни к чему; право на доктайп открыло бы через REST все
		репорты курса разом, чужие тоже. Руководителю репорты не показываются:
		репорт о курсе, а не отчётность по людям. Дописанный в схему блок прав
		иначе не заметит никто."""
		суффикс = frappe.generate_hash(length=6)
		организация = создать_организацию(f"Компания {суффикс}")
		руководитель = создать_менеджера(f"rep-m-{суффикс}@example.com", организация)

		frappe.set_user(self.ученик)
		self.assertFalse(frappe.has_permission("Agent Course Report", "read"))

		frappe.set_user(руководитель)
		self.assertFalse(frappe.has_permission("Agent Course Report", "read"))
