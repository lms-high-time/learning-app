# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Тексты релиза с угловыми скобками — как есть на всём пути: релиз → индекс и
проекция → ответы ученику и автору; кабинет их экранирует
(lms-high-time/learning-services#521)."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.releases import index
from lms_frappe_app.api import authoring, student
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	зачислить_на_курс,
	создать_куратора,
	создать_ученика,
	урок_релиза,
)
from lms_frappe_app.tests.test_author_page import страница

#: Тег, который очистка HTML Frappe вырезает целиком.
ТЕГ = "<role>"
#: Скобки без тега: очистка сделала бы из них `a<b>d</b>`.
СКОБКИ = "a<b and c>d"


def релиз_с_тегами(ключ: str) -> dict:
	"""Образец релиза, где тексты для ученика и автора несут `ТЕГ` или `СКОБКИ`.

	Названий глав и уроков здесь нет: `<` и `>` в них релиз не пускает
	(`title_forbidden_chars`)."""
	р = пример_релиза(ключ)
	р["course"]["promise"] = f"Обещание: {ТЕГ}"
	р["chapters"][0]["description"] = f"Глава про {ТЕГ}"
	у = р["lessons"][0]
	у["hook"] = f"Зачин: {СКОБКИ}"
	цель = у["objectives"][0]
	цель["text"] = f"Цель: объяснить {ТЕГ}"
	цель["goals"][0]["title"] = f"Термин {ТЕГ}"
	вопрос = у["quiz"]["questions"][0]
	вопрос["text"] = f"Что делает тег {ТЕГ} в промпте?"
	вопрос["options"][0]["text"] = f"Задаёт роль: {ТЕГ}"
	у["quiz"]["answers"][вопрос["key"]]["explanation"] = f"Тег {ТЕГ} задаёт роль, а {СКОБКИ}."
	домашка = р["lessons"][2]["homework"]
	домашка["title"], домашка["description"] = f"Задание {ТЕГ}", f"Опишите `{ТЕГ}` и {СКОБКИ}."
	документ = р["document"]
	документ["title"], документ["purpose"] = f"Тетрадь {ТЕГ}", f"Зачем {СКОБКИ}"
	раздел = документ["sections"][0]
	раздел["title"], раздел["description"] = f"Журнал {ТЕГ}", f"Что пишут: {СКОБКИ}"
	return р


class IntegrationTestТекстыРелиза(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"texts-{суффикс}@example.com")
		self.ключ = f"texts-{суффикс}"
		self.релиз = релиз_с_тегами(self.ключ)
		self.первая = self.опубликовать(self.релиз)
		self.курс = self.первая["course"]

	def опубликовать(self, релиз: dict) -> dict:
		frappe.set_user(self.куратор)
		ответ = authoring.publish_release(release=релиз)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def данные(self, ответ: dict) -> dict:
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def test_индекс_хранит_тексты_как_есть(self):
		релиз = self.первая["release"]
		у = self.релиз["lessons"][0]
		вопрос = у["quiz"]["questions"][0]
		раздел = self.релиз["document"]["sections"][0]

		self.assertEqual(index.главы(релиз)[0]["description"], f"Глава про {ТЕГ}")
		self.assertEqual(index.урок(релиз, "l-1").hook, у["hook"])
		[цель] = index.цели_урока(релиз, "l-1")
		self.assertEqual(
			(цель["text"], цель["goals"][0]["title"]), (у["objectives"][0]["text"], f"Термин {ТЕГ}")
		)
		[строка] = index.вопросы_урока(релиз, "l-1", с_ответами=True)
		self.assertEqual(
			(строка["text"], строка["options"][0]["text"], строка["explanation"]),
			(
				вопрос["text"],
				вопрос["options"][0]["text"],
				у["quiz"]["answers"][вопрос["key"]]["explanation"],
			),
		)
		self.assertEqual(
			(index.разделы(релиз)["log"]["title"], index.разделы(релиз)["log"]["description"]),
			(раздел["title"], раздел["description"]),
		)

	def test_проекция_хранит_тексты_как_есть(self):
		урок = урок_релиза(self.курс, "l-1")
		глава = frappe.db.get_value("Course Lesson", урок, "chapter")
		домашка = frappe.db.get_value(
			"Agent Lesson Homework", {"lesson": урок_релиза(self.курс, "l-3")}, ["title", "description"]
		)
		документ = frappe.get_doc(
			"Agent Course Artifact", {"course": self.курс, "slug": "notebook", "is_active": 1}
		)

		self.assertEqual(frappe.db.get_value("Course Lesson", урок, "lesson_hook"), f"Зачин: {СКОБКИ}")
		self.assertEqual(
			frappe.db.get_value("Course Chapter", глава, "chapter_description"), f"Глава про {ТЕГ}"
		)
		self.assertEqual(frappe.db.get_value("LMS Course", self.курс, "course_promise"), f"Обещание: {ТЕГ}")
		self.assertEqual(домашка, (f"Задание {ТЕГ}", f"Опишите `{ТЕГ}` и {СКОБКИ}."))
		self.assertEqual((документ.title, документ.purpose), (f"Тетрадь {ТЕГ}", f"Зачем {СКОБКИ}"))
		self.assertEqual(
			(документ.blocks[0].title, документ.blocks[0].description),
			(f"Журнал {ТЕГ}", f"Что пишут: {СКОБКИ}"),
		)

	def test_повторная_публикация_не_переписывает_проекцию(self):
		"""Записанное совпадает с релизом: новая версия с другим названием курса
		не трогает ни глав, ни уроков, ни схемы документа."""
		self.релиз["course"]["title"] = "Курс с новым названием"

		вторая = self.опубликовать(self.релиз)

		self.assertEqual(вторая["version"], 2)
		for вид in ("chapters", "lessons"):
			self.assertEqual(вторая[вид]["updated"], [], вид)
		self.assertEqual(вторая["document"], self.первая["document"])

	def test_ученик_получает_тексты_как_есть(self):
		у = self.релиз["lessons"][0]
		вопрос = у["quiz"]["questions"][0]
		frappe.set_user("Administrator")
		ученик = создать_ученика(f"texts-s-{frappe.generate_hash(length=6)}@example.com")
		зачислить_на_курс(ученик, self.курс)
		frappe.set_user(ученик)

		старт = self.данные(student.start_lesson(lesson=урок_релиза(self.курс, "l-1"), frame=False))
		self.assertEqual(старт["lesson"]["hook"], у["hook"])
		self.assertEqual(старт["course_promise"], f"Обещание: {ТЕГ}")
		[цель] = старт["lesson_map"]
		self.assertEqual(
			(цель["text"], цель["goals"][0]["title"]), (у["objectives"][0]["text"], f"Термин {ТЕГ}")
		)
		for пункт in цель["goals"]:
			if пункт["required"]:
				self.данные(
					student.mark_goal(старт["session"], пункт["key"], "done", "Назвал своими словами")
				)

		квиз = self.данные(student.request_quiz(старт["session"]))
		self.assertEqual(квиз["question"]["text"], вопрос["text"])
		self.assertEqual(квиз["question"]["options"][0]["text"], вопрос["options"][0]["text"])
		ответ = self.данные(student.submit_answer(квиз["attempt"], вопрос["key"], "V1", "Первый"))
		self.assertEqual(ответ["verdict"]["explanation"], у["quiz"]["answers"][вопрос["key"]]["explanation"])

	def test_автор_получает_тексты_как_есть(self):
		frappe.set_user(self.куратор)
		урок = self.данные(authoring.course_release(course=self.курс, lesson="l-1"))["lesson"]
		целиком = self.данные(authoring.course_release(course=self.курс))
		у = self.релиз["lessons"][0]

		self.assertEqual(урок["hook"], у["hook"])
		self.assertEqual(урок["objectives"], у["objectives"])
		вопрос = урок["questions"][0]
		эталон = у["quiz"]["questions"][0]
		self.assertEqual((вопрос["text"], вопрос["options"]), (эталон["text"], эталон["options"]))
		self.assertEqual(вопрос["explanation"], у["quiz"]["answers"][эталон["key"]]["explanation"])
		self.assertEqual(целиком["chapters"][0]["description"], f"Глава про {ТЕГ}")
		self.assertEqual(
			(целиком["document"]["title"], целиком["document"]["purpose"]),
			(f"Тетрадь {ТЕГ}", f"Зачем {СКОБКИ}"),
		)
		self.assertEqual(целиком["document"]["sections"][0]["title"], f"Журнал {ТЕГ}")

	def test_кабинет_экранирует_тексты(self):
		html = страница(self.куратор, course=self.курс, lesson="l-1")

		self.assertIn("Что делает тег &lt;role&gt; в промпте?", html)
		self.assertIn("Задаёт роль: &lt;role&gt;", html)
		self.assertIn("Цель: объяснить &lt;role&gt;", html)
		self.assertIn("a&lt;b and c&gt;d", html)
		self.assertNotIn(ТЕГ, html)
		self.assertNotIn("<b and c>", html)
