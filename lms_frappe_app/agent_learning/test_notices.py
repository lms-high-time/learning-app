# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Письма о курсах: ссылки (learning-services#693) и язык получателя
(learning-services#768)."""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.agent_learning import announcements, notices
from lms_frappe_app.agent_learning.notices import ссылка
from lms_frappe_app.tests.sample_data import создать_курс, создать_ученика


class UnitTestEmailLink(UnitTestCase):
	def test_адрес_ссылки_экранирован(self):
		"""Адрес собирается из имён документов и выводится в атрибут экранированным."""
		self.assertEqual(
			ссылка('https://cenda.app/lms/courses/a"b<c>&d', "Открыть курс"),
			'<a href="https://cenda.app/lms/courses/a&quot;b&lt;c&gt;&amp;d">Открыть курс</a>',
		)


class IntegrationTestПисьмаНаЯзыкеПолучателя(IntegrationTestCase):
	"""Письмо о выходе курса уходит всем подписавшимся сразу, приглашение
	тестеру — по вызову автора: язык и название курса — каждого получателя,
	а не того, кто отправку вызвал."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.письма = patch("frappe.sendmail").start()
		patch.object(notices, "почта_есть", return_value=True).start()
		self.addCleanup(patch.stopall)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.local.lang = "ru"
		суффикс = frappe.generate_hash(length=6)
		self.курс = создать_курс(f"Основы {суффикс}")
		frappe.db.set_value(
			"LMS Course", self.курс, "course_translations", json.dumps({"en": {"title": f"Basics {суффикс}"}})
		)
		self.русское = frappe.db.get_value("LMS Course", self.курс, "title")
		self.английское = f"Basics {суффикс}"
		self.люди = {}
		for язык in ("ru", "en"):
			self.люди[язык] = создать_ученика(f"nl-{язык}-{суффикс}@example.com")
			frappe.db.set_value("User", self.люди[язык], "language", язык)

	def письмо_к(self, кому: str) -> dict:
		[письмо] = [в.kwargs for в in self.письма.call_args_list if в.kwargs["recipients"] == [кому]]
		return письмо

	def test_выход_курса(self):
		for кто in self.люди.values():
			announcements.подписать(кто, self.курс)

		self.assertEqual(notices.уведомить_о_выходе(self.курс), 2)

		русское, английское = self.письмо_к(self.люди["ru"]), self.письмо_к(self.люди["en"])
		self.assertEqual(русское["subject"], f"Курс «{self.русское}» вышел")
		self.assertIn(">Открыть курс</a> — занятие идёт с наставником", русское["message"])
		self.assertEqual(английское["subject"], f"The course “{self.английское}” is out")
		self.assertIn("is now open for enrollment.", английское["message"])
		self.assertIn(">Open the course</a> — lessons run with a mentor", английское["message"])

	def test_приглашение_тестера(self):
		for язык, тема in (
			("ru", f"Вас пригласили проверить курс «{self.русское}»"),
			("en", f"You've been invited to test the course “{self.английское}”"),
		):
			with self.subTest(язык):
				self.assertTrue(notices.пригласить_тестера(self.курс, self.люди[язык]))
				self.assertEqual(self.письмо_к(self.люди[язык])["subject"], тема)
