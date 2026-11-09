# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Уход из команды: копия документов — письмом (learning-services#585).

Видимость работы ушедшего проверяют тесты прав (`api/test_team.py`,
`api/test_spaces.py`); здесь — только письмо.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import leave
from lms_frappe_app.api import student
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	раздел,
	создать_организацию,
	создать_урок,
	создать_ученика,
	схема_документа,
)


class IntegrationTestLeave(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.команда = создать_организацию(f"Команда {суффикс}")
		self.участник = создать_ученика(f"leave-{суффикс}@example.com")
		добавить_в_организацию(self.участник, self.команда)
		self.курс = зачислить(self.участник, создать_урок(f"Урок {суффикс}"))
		frappe.db.set_value("LMS Course", self.курс, "title", f"Курс: «старт»/{суффикс}")
		схема_документа(self.курс, "summary", "Резюме проекта", [раздел("goal", "Цель")])
		frappe.get_doc(
			{"doctype": "Course Allocation", "organization": self.команда, "course": self.курс}
		).insert(ignore_permissions=True)
		frappe.set_user(self.участник)
		for текст, пространство in (("Для команды", self.команда), ("Своё", "personal")):
			ответ = student.update_artifact(
				self.курс, "summary", "goal", fields={"goal": текст}, space=пространство
			)
			self.assertTrue(ответ["ok"], ответ)
		frappe.set_user("Administrator")

	def уйти(self, почта: bool = True):
		with patch.object(leave, "почта_есть", return_value=почта), patch("frappe.sendmail") as письма:
			файлов = leave.уйти(self.участник, self.команда)
		return файлов, письма

	def test_копия_документов_команды_письмом(self):
		файлов, письма = self.уйти()

		self.assertEqual(файлов, 1)
		письма.assert_called_once()
		аргументы = письма.call_args.kwargs
		self.assertEqual(аргументы["recipients"], [self.участник])
		(файл,) = аргументы["attachments"]
		# Только документ команды, личный не уходит.
		self.assertIn("Для команды", файл["fcontent"])
		self.assertNotIn("Своё", файл["fcontent"])
		# Знаки из названия курса, которых не бывает в именах файлов, заменены.
		self.assertTrue(файл["fname"].endswith("-summary.md"), файл["fname"])
		self.assertFalse(set("/:«»") & set(файл["fname"]), файл["fname"])

	def test_без_почты_уход_проходит_молча(self):
		frappe.clear_messages()

		файлов, письма = self.уйти(почта=False)

		self.assertEqual(файлов, 0)
		письма.assert_not_called()
		self.assertEqual(frappe.message_log, [])

	def test_документ_без_схемы_не_роняет_письмо(self):
		"""Схему сняли с курса — файла нет, а в письме сказано, что его нет."""
		frappe.db.set_value("Agent Course Artifact", {"course": self.курс, "slug": "summary"}, "is_active", 0)

		frappe.db.set_value("User", self.участник, "language", "ru")

		файлов, письма = self.уйти()

		self.assertEqual(файлов, 0)
		аргументы = письма.call_args.kwargs
		self.assertEqual(аргументы["attachments"], [])
		self.assertIn("собрать не удалось", аргументы["message"])

	def test_письмо_и_вложение_на_языке_ушедшего(self):
		"""Уход зовёт и руководитель со своим языком, а письмо и подписи
		документа — на языке ушедшего (learning-services#768)."""
		frappe.db.set_value("User", self.участник, "language", "en")
		# Пустой раздел — чтобы во вложении была подпись выгрузки.
		схема_документа(
			self.курс, "summary", "Резюме проекта", [раздел("goal", "Цель"), раздел("step", "Шаг")]
		)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.local.lang = "ru"

		_файлов, письма = self.уйти()

		аргументы = письма.call_args.kwargs
		название = frappe.db.get_value("Learning Organization", self.команда, "organization_name")
		self.assertEqual(аргументы["subject"], f"Your documents from the team “{название}”")
		self.assertIn("You're no longer on the team", аргументы["message"])
		self.assertIn("A copy of the documents you built in its space is attached.", аргументы["message"])
		(файл,) = аргументы["attachments"]
		self.assertIn("## Шаг\n\n_Not filled in._", файл["fcontent"])
