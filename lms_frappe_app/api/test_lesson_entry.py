# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Вход в урок со страницы Learning: зачин и дорога на занятие.

`Why:` страница урока в браузере показывает не материал, а куда идти
заниматься (lms-platform#309). Позови она в веб-чат, когда пробные уроки
кончились, — чат откажет; позови подключать ассистента, когда пробный урок есть, —
ученик без своего агента застрянет на самом тяжёлом шаге.
"""

from urllib.parse import quote

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	НАСТРОЙКИ,
)
from lms_frappe_app.agent_learning.errors import УРОК_НЕ_НАЙДЕН
from lms_frappe_app.api import public, student
from lms_frappe_app.tests.sample_data import (
	привязать_урок,
	зачислить,
	политика_по_умолчанию,
	создать_урок,
	создать_ученика,
)

СЕРВИС = "https://agent.example.com"


class IntegrationTestLessonEntry(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(политика_по_умолчанию)
		политика_по_умолчанию()
		прежний = frappe.db.get_single_value(НАСТРОЙКИ, "agent_service_url")
		self.addCleanup(self.задать, "agent_service_url", прежний)
		self.задать("agent_service_url", СЕРВИС + "/")
		self.задать("web_demo_lessons", 2)

		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"entry-{суффикс}@example.com")
		self.уроки = [создать_урок(f"Вход {н} {суффикс}") for н in range(3)]
		for урок in self.уроки:
			зачислить(self.ученик, урок)
		frappe.set_user(self.ученик)

	def задать(self, поле: str, значение) -> None:
		frappe.db.set_single_value(НАСТРОЙКИ, поле, значение)
		frappe.clear_document_cache(НАСТРОЙКИ, НАСТРОЙКИ)

	def войти(self, урок: str) -> dict:
		ответ = public.lesson_entry(lesson=урок)
		self.assertTrue(ответ["ok"], ответ.get("error"))
		return ответ["data"]

	def test_пока_есть_пробные_ведёт_в_чат_на_этот_урок(self):
		вход = self.войти(self.уроки[0])

		self.assertEqual(вход["study"]["channel"], "web")
		self.assertEqual(вход["study"]["url"], f"{СЕРВИС}/chat?lesson={quote(self.уроки[0], safe='')}")
		self.assertEqual(вход["study"]["demo_left"], 2)

	def test_пробные_кончились_ведёт_к_своему_агенту(self):
		student.start_lesson(lesson=self.уроки[0], channel="web")
		student.start_lesson(lesson=self.уроки[1], channel="web")

		вход = self.войти(self.уроки[2])

		self.assertEqual(вход["study"], {"channel": "agent", "url": "/lms/agent", "demo_left": 0})

	def test_начатый_в_чате_урок_ведёт_в_чат_и_без_пробных(self):
		"""Тем же правилом, что `start_lesson`: возврат в урок пробного не тратит."""
		student.start_lesson(lesson=self.уроки[0], channel="web")
		student.start_lesson(lesson=self.уроки[1], channel="web")

		вход = self.войти(self.уроки[0])

		self.assertEqual(вход["study"]["channel"], "web")
		self.assertEqual(вход["study"]["demo_left"], 0)

	def test_без_сервиса_агента_ведёт_на_страницу_агента(self):
		frappe.set_user("Administrator")
		self.задать("agent_service_url", "")
		frappe.set_user(self.ученик)

		вход = self.войти(self.уроки[0])

		self.assertEqual(вход["study"]["channel"], "agent")
		self.assertEqual(вход["study"]["url"], "/lms/agent")

	def test_зачин_и_пройденность(self):
		frappe.set_user("Administrator")
		frappe.db.set_value("Course Lesson", self.уроки[0], "lesson_hook", "  Зачем это вам  ")
		frappe.get_doc(
			{
				"doctype": "LMS Course Progress",
				"member": self.ученик,
				"lesson": self.уроки[0],
				"status": "Complete",
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)

		вход = self.войти(self.уроки[0])
		пустой = self.войти(self.уроки[1])

		self.assertEqual(вход["hook"], "Зачем это вам")
		self.assertTrue(вход["completed"])
		self.assertIsNone(пустой["hook"], "пустой зачин — null, а не пустая строка")
		self.assertFalse(пустой["completed"])

	def test_называет_блоки_документа_этого_урока(self):
		"""Что из документа курса собирают на занятии (learning-services#340)."""
		frappe.set_user("Administrator")
		курс = frappe.db.get_value("Course Lesson", self.уроки[0], "course")
		frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"course": курс,
				"slug": "summary",
				"title": "Резюме проекта",
				"blocks": [
					{"block_key": "goal", "title": "Цель", "lesson": self.уроки[0]},
					{"block_key": "sponsor", "title": "Спонсор"},
				],
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)

		self.assertEqual(
			self.войти(self.уроки[0])["blocks"],
			[{"artifact": "summary", "key": "goal", "title": "Цель", "filled": False}],
		)

	def test_материал_наружу_не_выходит(self):
		вход = self.войти(self.уроки[0])

		self.assertEqual(
			set(вход), {"lesson", "course", "title", "hook", "completed", "study", "program_lock", "blocks"}
		)

	def test_неизвестный_урок(self):
		ответ = public.lesson_entry(lesson="нет-такого-урока")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], УРОК_НЕ_НАЙДЕН)

	def test_гостю_вход_закрыт(self):
		frappe.set_user("Guest")

		with self.assertRaises(frappe.AuthenticationError):
			public.lesson_entry(lesson=self.уроки[0])

	# --- вход по курсу: кнопка «Продолжить» (learning-services#301) ---

	def курс_из_двух_уроков(self) -> tuple[str, list[str]]:
		frappe.set_user("Administrator")
		первый = self.уроки[0]
		глава = frappe.db.get_value("Course Lesson", первый, "chapter")
		второй = frappe.get_doc(
			{"doctype": "Course Lesson", "title": f"Второй {frappe.generate_hash(length=6)}", "chapter": глава}
		).insert(ignore_permissions=True).name
		привязать_урок(глава, второй)
		курс = frappe.db.get_value("Course Chapter", глава, "course")
		return курс, [первый, второй]

	def пройти(self, урок: str) -> None:
		frappe.get_doc(
			{"doctype": "LMS Course Progress", "member": self.ученик, "lesson": урок, "status": "Complete"}
		).insert(ignore_permissions=True)

	def test_по_курсу_ведёт_в_первый_незакрытый_урок(self):
		курс, (первый, второй) = self.курс_из_двух_уроков()
		self.пройти(первый)
		frappe.set_user(self.ученик)

		вход = public.lesson_entry(course=курс)["data"]

		self.assertEqual(вход["lesson"], второй)
		self.assertIn(quote(второй, safe=""), вход["study"]["url"])

	def test_пройденный_курс_ведёт_в_первый_урок_для_повтора(self):
		курс, (первый, второй) = self.курс_из_двух_уроков()
		self.пройти(первый)
		self.пройти(второй)
		frappe.set_user(self.ученик)

		вход = public.lesson_entry(course=курс)["data"]

		self.assertEqual(вход["lesson"], первый)
		self.assertTrue(вход["completed"])

	def test_курс_без_уроков_и_пустой_вызов_отказываются(self):
		for параметры in ({"course": "нет-такого-курса"}, {}):
			with self.subTest(параметры=параметры):
				ответ = public.lesson_entry(**параметры)
				self.assertFalse(ответ["ok"])
				self.assertEqual(ответ["error"]["code"], УРОК_НЕ_НАЙДЕН)
