# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Анонс курса: виден в каталоге, записи нет, есть «сообщить о выходе» (#389).

Запрет записи проверяется там же, где его обходят: методом контракта,
который ученик может позвать своим токеном мимо агента, и сохранением
назначения, которое создаёт зачисления само.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import notices
from lms_frappe_app.api import authoring, public, student, team
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_куратора,
	создать_курс,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
)

ЦЕЛИ = "Описать процесс в пять колонок\nНайти стыки между отделами"


class IntegrationTestAnnouncements(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.письма = patch("frappe.sendmail").start()
		patch.object(notices, "почта_есть", return_value=True).start()
		self.addCleanup(patch.stopall)
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"an-c-{суффикс}@example.com")
		self.ученик = создать_ученика(f"an-s-{суффикс}@example.com")
		self.компания = создать_организацию(f"Анонс {суффикс}")
		добавить_в_организацию(self.ученик, self.компания)
		self.руководитель = создать_менеджера(f"an-m-{суффикс}@example.com", self.компания)
		self.курс = создать_курс(f"Анонс {суффикс}")

	def от_имени(self, кто: str, метод, **аргументы) -> dict:
		frappe.set_user(кто)
		try:
			return метод(**аргументы)
		finally:
			frappe.set_user("Administrator")

	def анонсировать(self) -> dict:
		self.от_имени(
			self.куратор,
			authoring.set_course_directive,
			course=self.курс,
			teaching_directive="Вести на примерах ученика",
			objectives=ЦЕЛИ,
		)
		ответ = self.от_имени(self.куратор, authoring.announce_course, course=self.курс)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def код_отказа(self, ответ: dict) -> str | None:
		return None if ответ["ok"] else ответ["error"]["code"]

	# --- куратор ---

	def test_анонс_без_целей_курса_отклоняется(self):
		ответ = self.от_имени(self.куратор, authoring.announce_course, course=self.курс)
		self.assertEqual(self.код_отказа(ответ), authoring.НЕТ_ЦЕЛЕЙ_КУРСА)
		self.assertFalse(frappe.db.get_value("LMS Course", self.курс, "published"))

	def test_анонс_не_требует_уроков_и_отдаёт_цели(self):
		данные = self.анонсировать()
		self.assertEqual(
			данные["objectives"], ["Описать процесс в пять колонок", "Найти стыки между отделами"]
		)
		сведения = frappe.db.get_value("LMS Course", self.курс, ["published", "upcoming"], as_dict=True)
		self.assertEqual((сведения.published, сведения.upcoming), (1, 1))

	def test_открытый_курс_анонсом_не_становится(self):
		frappe.db.set_value("LMS Course", self.курс, "published", 1)
		ответ = self.от_имени(self.куратор, authoring.announce_course, course=self.курс)
		self.assertEqual(self.код_отказа(ответ), authoring.КУРС_УЖЕ_ОТКРЫТ)

	def test_снятие_с_публикации_снимает_и_анонс(self):
		self.анонсировать()
		self.от_имени(self.куратор, authoring.unpublish_course, course=self.курс)
		сведения = frappe.db.get_value("LMS Course", self.курс, ["published", "upcoming"], as_dict=True)
		self.assertEqual((сведения.published, сведения.upcoming), (0, 0))

	def test_список_курсов_отмечает_анонс(self):
		self.анонсировать()
		курсы = self.от_имени(self.куратор, authoring.list_courses)["data"]["courses"]
		строка = next(к for к in курсы if к["id"] == self.курс)
		self.assertTrue(строка["upcoming"])

	# --- ученик ---

	def test_каталог_показывает_анонс_с_целями(self):
		self.анонсировать()
		курсы = self.от_имени(self.ученик, student.list_catalog)["data"]["courses"]
		строка = next(к for к in курсы if к["id"] == self.курс)
		self.assertTrue(строка["upcoming"])
		self.assertEqual(len(строка["objectives"]), 2)
		self.assertFalse(строка["notify"])

	def test_на_анонс_не_записаться_ни_лично_ни_в_организации(self):
		self.анонсировать()
		for space in (None, self.компания):
			ответ = self.от_имени(self.ученик, student.enroll, course=self.курс, space=space)
			self.assertEqual(self.код_отказа(ответ), "course_upcoming")
		self.assertFalse(frappe.db.exists("LMS Enrollment", {"member": self.ученик, "course": self.курс}))

	def test_подписка_одна_и_видна_в_каталоге(self):
		self.анонсировать()
		for _ in range(2):
			ответ = self.от_имени(self.ученик, student.notify_when_released, course=self.курс)
			self.assertTrue(ответ["data"]["notify"])
		self.assertEqual(
			frappe.db.count("LMS Course Interest", {"user": self.ученик, "course": self.курс}), 1
		)
		курсы = self.от_имени(self.ученик, student.list_catalog)["data"]["courses"]
		self.assertTrue(next(к for к in курсы if к["id"] == self.курс)["notify"])

	def test_отписка_снимает_подписку_и_повторяется_без_ошибки(self):
		self.анонсировать()
		self.от_имени(self.ученик, student.notify_when_released, course=self.курс)
		for _ in range(2):
			ответ = self.от_имени(
				self.ученик, student.notify_when_released, course=self.курс, notify="false"
			)
			self.assertFalse(ответ["data"]["notify"])
		self.assertFalse(
			frappe.db.exists("LMS Course Interest", {"user": self.ученик, "course": self.курс})
		)
		курсы = self.от_имени(self.ученик, student.list_catalog)["data"]["courses"]
		self.assertFalse(next(к for к in курсы if к["id"] == self.курс)["notify"])

	def test_подписка_только_на_анонс(self):
		frappe.db.set_value("LMS Course", self.курс, "published", 1)
		ответ = self.от_имени(self.ученик, student.notify_when_released, course=self.курс)
		self.assertEqual(self.код_отказа(ответ), student.КУРС_НЕ_АНОНС)

	# --- страница курса ---

	def test_карта_анонса_без_программы(self):
		self.анонсировать()
		данные = self.от_имени("Guest", public.course_map, course=self.курс)["data"]
		self.assertTrue(данные["upcoming"])
		self.assertEqual(данные["chapters"], [])
		self.assertEqual(данные["documents"], [])
		self.assertEqual(len(данные["objectives"]), 2)
		self.assertNotIn("notify", данные)

		self.от_имени(self.ученик, student.notify_when_released, course=self.курс)
		данные = self.от_имени(self.ученик, public.course_map, course=self.курс)["data"]
		self.assertTrue(данные["notify"])

	def test_карта_открытого_курса_без_признака_анонса(self):
		frappe.db.set_value("LMS Course", self.курс, "published", 1)
		данные = self.от_имени("Guest", public.course_map, course=self.курс)["data"]
		self.assertNotIn("upcoming", данные)

	# --- руководитель ---

	def test_анонс_не_назначить_команде(self):
		self.анонсировать()
		данные = self.от_имени(self.руководитель, team.allocations, organization=self.компания)["data"]
		self.assertNotIn(self.курс, [к["id"] for к in данные["courses"]])
		ответ = self.от_имени(
			self.руководитель, team.assign_course, organization=self.компания, course=self.курс
		)
		self.assertEqual(self.код_отказа(ответ), "course_upcoming")
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": "Course Allocation", "organization": self.компания, "course": self.курс}
			).insert(ignore_permissions=True)

	# --- выход ---

	def test_выход_анонса_пишет_подписавшимся_один_раз(self):
		self.анонсировать()
		self.от_имени(self.ученик, student.notify_when_released, course=self.курс)
		with patch.object(authoring.course_builder, "проверить_готовность") as готовность:
			готовность.return_value = {"blocking": [], "warnings": []}
			первый = self.от_имени(self.куратор, authoring.publish_course, course=self.курс)
			self.assertEqual(первый["data"]["notified"], 1)
			self.assertFalse(frappe.db.get_value("LMS Course", self.курс, "upcoming"))
			self.assertEqual(self.письма.call_args.kwargs["recipients"], [self.ученик])

			self.от_имени(self.куратор, authoring.unpublish_course, course=self.курс)
			второй = self.от_имени(self.куратор, authoring.publish_course, course=self.курс)
			self.assertEqual(второй["data"]["notified"], 0)
		self.assertEqual(self.письма.call_count, 1)

		ответ = self.от_имени(self.ученик, student.enroll, course=self.курс)
		self.assertTrue(ответ["ok"], ответ)
