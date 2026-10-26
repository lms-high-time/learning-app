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
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	НАСТРОЙКИ,
)
from lms_frappe_app.agent_learning.errors import УРОК_НЕ_НАЙДЕН
from lms_frappe_app.api import public
from lms_frappe_app.tests.sample_data import (
	зачислить,
	политика_по_умолчанию,
	привязать_урок,
	раздел,
	создать_занятие,
	создать_куратора,
	создать_урок,
	создать_ученика,
	схема_документа,
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

	def пробный_урок(self, урок: str) -> None:
		"""Урок, начатый в веб-чате: занятие с отметкой веб-канала — так его считает проба."""
		frappe.db.set_value("Agent Learning Session", создать_занятие(self.ученик, урок), "web_chat", 1)

	def войти(self, урок: str) -> dict:
		ответ = public.lesson_entry(lesson=урок)
		self.assertTrue(ответ["ok"], ответ.get("error"))
		return ответ["data"]

	def test_дорога_на_занятие(self):
		"""Проводка `дорога_на_занятие`: адрес сервиса и число пробных — из
		настроек, начатые уроки — из занятий веб-чата. Пробный урок один и уже
		начат в чате — туда чат пускает без остатка. Прочие случаи —
		`TestДорогаНаЗанятие`."""
		frappe.set_user("Administrator")
		прежний = frappe.db.get_single_value(НАСТРОЙКИ, "web_demo_lessons")
		self.addCleanup(self.задать, "web_demo_lessons", прежний)
		self.задать("web_demo_lessons", 1)
		frappe.set_user(self.ученик)
		self.пробный_урок(self.уроки[0])

		вход = self.войти(self.уроки[0])

		self.assertEqual(
			вход["study"],
			{
				"channel": "web",
				"url": f"{СЕРВИС}/chat?lesson={quote(self.уроки[0], safe='')}",
				"demo_left": 0,
			},
		)

	def test_зачин_пройденность_и_блоки_документа(self):
		"""Блоки документа курса, которые собирают на занятии этого урока
		(learning-services#340); зачин и пройденность урока."""
		frappe.set_user("Administrator")
		курс = frappe.db.get_value("Course Lesson", self.уроки[0], "course")
		схема_документа(
			курс,
			"summary",
			"Резюме проекта",
			[раздел("goal", "Цель", lesson=self.уроки[0]), раздел("sponsor", "Спонсор")],
		)
		frappe.set_user(self.ученик)
		with self.subTest("learning-services#340: блоки документа этого урока"):
			self.assertEqual(
				self.войти(self.уроки[0])["blocks"],
				[
					{
						"artifact": "summary",
						"key": "goal",
						"title": "Цель",
						"description": None,
						"filled": False,
					}
				],
			)

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
		with self.subTest("зачин и пройденность"):
			вход = self.войти(self.уроки[0])
			пустой = self.войти(self.уроки[1])

			self.assertEqual(вход["hook"], "Зачем это вам")
			self.assertTrue(вход["completed"])
			self.assertIsNone(пустой["hook"], "пустой зачин — null, а не пустая строка")
			self.assertFalse(пустой["completed"])

	def test_гостю_вход_закрыт(self):
		frappe.set_user("Guest")

		with self.assertRaises(frappe.AuthenticationError):
			public.lesson_entry(lesson=self.уроки[0])

	# --- вход по курсу: кнопка «Продолжить» (learning-services#301) ---

	def курс_из_двух_уроков(self) -> tuple[str, list[str]]:
		frappe.set_user("Administrator")
		первый = self.уроки[0]
		глава = frappe.db.get_value("Course Lesson", первый, "chapter")
		второй = (
			frappe.get_doc(
				{
					"doctype": "Course Lesson",
					"title": f"Второй {frappe.generate_hash(length=6)}",
					"chapter": глава,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		привязать_урок(глава, второй)
		курс = frappe.db.get_value("Course Chapter", глава, "course")
		return курс, [первый, второй]

	def пройти(self, урок: str) -> None:
		frappe.get_doc(
			{"doctype": "LMS Course Progress", "member": self.ученик, "lesson": урок, "status": "Complete"}
		).insert(ignore_permissions=True)

	def test_по_курсу_ведёт_в_первый_незакрытый_урок(self):
		"""Пройденный курс ведёт в первый урок — для повтора."""
		курс, (первый, второй) = self.курс_из_двух_уроков()
		self.пройти(первый)
		frappe.set_user(self.ученик)

		with self.subTest("первый незакрытый"):
			вход = public.lesson_entry(course=курс)["data"]

			self.assertEqual(вход["lesson"], второй)
			self.assertIn(quote(второй, safe=""), вход["study"]["url"])

		frappe.set_user("Administrator")
		self.пройти(второй)
		frappe.set_user(self.ученик)
		with self.subTest("пройденный курс — первый урок для повтора"):
			вход = public.lesson_entry(course=курс)["data"]

			self.assertEqual(вход["lesson"], первый)
			self.assertTrue(вход["completed"])

	def test_урок_курса_вне_витрины_не_зачисленному_не_найден(self):
		"""Урок неопубликованного курса или анонса не зачисленному не существует —
		как курс в `course_map`. Опубликованный курс открыт и не зачисленному:
		страница урока зовёт вход до записи; автору курс открыт всегда — он
		смотрит черновик в режиме ученика (learning-services#692)."""
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		урок = создать_урок(f"Вне витрины {суффикс}")
		автор = создать_куратора(f"entry-cc-{суффикс}@example.com", "Moderator")
		курс = frappe.db.get_value("Course Lesson", урок, "course")
		витрина = {
			"неопубликованный": ({"published": 0, "upcoming": 0}, False),
			"анонс": ({"published": 1, "upcoming": 1}, False),
			"опубликованный": ({"published": 1, "upcoming": 0}, True),
		}
		for что, (поля, открыт) in витрина.items():
			with self.subTest(что):
				frappe.set_user("Administrator")
				frappe.db.set_value("LMS Course", курс, поля)
				frappe.set_user(self.ученик)
				for параметры in ({"lesson": урок}, {"course": курс}):
					ответ = public.lesson_entry(**параметры)
					if открыт:
						self.assertTrue(ответ["ok"], ответ.get("error"))
						self.assertEqual(ответ["data"]["lesson"], урок)
					else:
						self.assertFalse(ответ["ok"])
						self.assertEqual(ответ["error"]["code"], УРОК_НЕ_НАЙДЕН)
				frappe.set_user(автор)
				ответ = public.lesson_entry(lesson=урок)
				self.assertTrue(ответ["ok"], ответ.get("error"))

	def test_курс_без_уроков_и_пустой_вызов_отказываются(self):
		for параметры in ({"lesson": "нет-такого-урока"}, {"course": "нет-такого-курса"}, {}):
			with self.subTest(параметры=параметры):
				ответ = public.lesson_entry(**параметры)
				self.assertFalse(ответ["ok"])
				self.assertEqual(ответ["error"]["code"], УРОК_НЕ_НАЙДЕН)


class TestДорогаНаЗанятие(UnitTestCase):
	"""Без базы: куда вести со страницы урока — `дорога_на_занятие` (lms-platform#309)."""

	def test_дорога(self):
		"""Пока есть пробные — в веб-чат на этот урок; без сервиса агента — на
		страницу агента, даже когда пробные есть; кончились — к своему агенту;
		урок, начатый в чате, ведёт в чат и без пробных."""
		чат = f"{СЕРВИС}/chat?lesson=L1"
		агент = {"channel": "agent", "url": "/lms/agent"}
		случаи = {
			"пока есть пробные": ("L1", СЕРВИС, set(), 2, {"channel": "web", "url": чат, "demo_left": 2}),
			"остался один пробный": ("L1", СЕРВИС, {"L0"}, 2, {"channel": "web", "url": чат, "demo_left": 1}),
			"без сервиса агента": ("L1", "", set(), 2, {**агент, "demo_left": 2}),
			"пробные кончились": ("L1", СЕРВИС, {"L0", "L2"}, 2, {**агент, "demo_left": 0}),
			"начатый в чате урок": (
				"L1",
				СЕРВИС,
				{"L0", "L1"},
				2,
				{"channel": "web", "url": чат, "demo_left": 0},
			),
			"начатый в чате урок без сервиса": ("L1", "", {"L1"}, 2, {**агент, "demo_left": 1}),
			"начато больше лимита — остаток не меньше нуля": (
				"L1",
				СЕРВИС,
				{"L0", "L2", "L3"},
				2,
				{**агент, "demo_left": 0},
			),
			"пробных уроков нет": ("L1", СЕРВИС, set(), 0, {**агент, "demo_left": 0}),
			"урок в адресе экранирован целиком": (
				"урок 1/2",
				СЕРВИС,
				set(),
				2,
				{
					"channel": "web",
					"url": f"{СЕРВИС}/chat?lesson=%D1%83%D1%80%D0%BE%D0%BA%201%2F2",
					"demo_left": 2,
				},
			),
		}
		for что, (урок, сервис, использовано, пробных, ожидаемо) in случаи.items():
			with self.subTest(что):
				self.assertEqual(public.дорога_на_занятие(урок, сервис, использовано, пробных), ожидаемо)
