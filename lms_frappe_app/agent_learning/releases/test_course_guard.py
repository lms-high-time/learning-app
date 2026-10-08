# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Курс из релиза правит только публикация (learning-services#500, #512).

Поля релиза у `LMS Course`, порядок глав курса, хуки `validate` и `on_trash`
у `Course Chapter` и `Course Lesson`.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.doctype.agent_course_release.test_agent_course_release import (
	вставить_релиз,
)
from lms_frappe_app.agent_learning.errors import КУРС_ИЗ_РЕЛИЗА, Отказ
from lms_frappe_app.agent_learning.releases import service
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	курс_из_релиза,
	создать_куратора,
	создать_курс,
	создать_урок,
	урок_релиза,
)


class IntegrationTestПоляРелизаУКурса(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"rel-guard-{суффикс}@example.com")
		self.курс = создать_курс(f"Правила {суффикс}")
		# Куратор — преподаватель курса: Learning даёт ему править курс.
		курс = frappe.get_doc("LMS Course", self.курс)
		курс.append("instructors", {"instructor": self.куратор})
		курс.save()

	def test_ключ_курса_правкой_не_ставится(self):
		курс = frappe.get_doc("LMS Course", self.курс)
		курс.course_key = f"hand-{frappe.generate_hash(length=6)}"
		with self.assertRaises(frappe.ValidationError):
			курс.save()

	def test_ключ_курса_из_релиза_правкой_не_меняется(self):
		frappe.set_user(self.куратор)
		ответ = service.опубликовать(
			пример_релиза(f"guard-{frappe.generate_hash(length=6)}"), None, self.куратор
		)
		курс = frappe.get_doc("LMS Course", ответ["course"])
		курс.course_key = "other-key"
		with self.assertRaises(frappe.ValidationError):
			курс.save()
		курс.reload()
		курс.short_introduction = "Правка карточки в desk"
		курс.save()

	def test_действующий_релиз_только_свой(self):
		чужой = вставить_релиз(создать_курс(f"Чужой {frappe.generate_hash(length=6)}"))
		курс = frappe.get_doc("LMS Course", self.курс)
		курс.active_release = чужой
		курс.flags.from_release = True
		with self.assertRaises(frappe.ValidationError):
			курс.save()


class IntegrationTestСтруктураКурсаИзРелиза(IntegrationTestCase):
	"""Desk и любой путь мимо публикации: главы, уроки и порядок глав курса из релиза."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		self.ключ = f"guard-{frappe.generate_hash(length=6)}"
		self.курс, _ = курс_из_релиза(релиз=пример_релиза(self.ключ))
		self.урок = урок_релиза(self.курс, "l-1")
		self.глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		self.свободный_урок = создать_урок(f"Без релиза {frappe.generate_hash(length=6)}")
		self.свободная_глава, self.свободный_курс = frappe.db.get_value(
			"Course Lesson", self.свободный_урок, ["chapter", "course"]
		)

	def отказ(self, действие):
		with self.assertRaises(Отказ) as пойман:
			действие()
		self.assertEqual(пойман.exception.код, КУРС_ИЗ_РЕЛИЗА)
		return пойман.exception

	def test_урок_и_глава_курса_из_релиза_не_правятся(self):
		урок = frappe.get_doc("Course Lesson", self.урок)
		урок.title = "Правка в desk"
		self.assertEqual(self.отказ(урок.save).подробности, {"course": self.курс})
		глава = frappe.get_doc("Course Chapter", self.глава)
		глава.title = "Правка в desk"
		self.отказ(глава.save)
		глава.reload()
		глава.lessons = глава.lessons[::-1]
		self.отказ(глава.save)
		self.assertEqual(frappe.db.get_value("Course Lesson", self.урок, "title"), "Урок первый")

	def test_в_курс_из_релиза_не_добавить(self):
		self.отказ(
			frappe.get_doc({"doctype": "Course Chapter", "course": self.курс, "title": "Лишняя"}).insert
		)
		self.отказ(
			frappe.get_doc({"doctype": "Course Lesson", "chapter": self.глава, "title": "Лишний"}).insert
		)

	def test_главу_и_урок_курса_из_релиза_не_удалить(self):
		self.отказ(lambda: frappe.delete_doc("Course Lesson", self.урок))
		self.отказ(lambda: frappe.delete_doc("Course Chapter", self.глава))
		self.assertTrue(frappe.db.exists("Course Lesson", self.урок))

	def test_смена_курса_проверяет_прежний_и_новый(self):
		# Из курса из релиза — в свободный.
		глава = frappe.get_doc("Course Chapter", self.глава)
		глава.course = self.свободный_курс
		self.отказ(глава.save)
		урок = frappe.get_doc("Course Lesson", self.урок)
		урок.chapter = self.свободная_глава
		self.assertEqual(self.отказ(урок.save).подробности, {"course": self.курс})
		# Из свободного — в курс из релиза: курс урока приходит из главы.
		глава = frappe.get_doc("Course Chapter", self.свободная_глава)
		глава.course = self.курс
		self.отказ(глава.save)
		урок = frappe.get_doc("Course Lesson", self.свободный_урок)
		урок.chapter = self.глава
		self.assertEqual(self.отказ(урок.save).подробности, {"course": self.курс})

	def test_курс_без_релиза_правится_свободно(self):
		урок = frappe.get_doc("Course Lesson", self.свободный_урок)
		урок.title = "Правка в desk"
		урок.save()
		глава = frappe.get_doc("Course Chapter", self.свободная_глава)
		глава.title = "Правка в desk"
		глава.lessons = []
		глава.save()
		вторая = frappe.get_doc(
			{"doctype": "Course Chapter", "course": self.свободный_курс, "title": "Вторая"}
		).insert()
		курс = frappe.get_doc("LMS Course", self.свободный_курс)
		курс.append("chapters", {"chapter": вторая.name})
		курс.save()
		курс.chapters = курс.chapters[::-1]
		курс.save()
		frappe.delete_doc("Course Lesson", урок.name)
		self.assertFalse(frappe.db.exists("Course Lesson", урок.name))

	def test_порядок_и_набор_глав_курса_из_релиза_не_правятся(self):
		курс = frappe.get_doc("LMS Course", self.курс)
		курс.chapters = курс.chapters[::-1]
		self.отказ(курс.save)
		курс.reload()
		курс.chapters = курс.chapters[:1]
		self.отказ(курс.save)
		курс.reload()
		лишняя = frappe.get_doc(
			{"doctype": "Course Chapter", "course": self.свободный_курс, "title": "Чужая"}
		).insert()
		курс.append("chapters", {"chapter": лишняя.name})
		self.отказ(курс.save)

	def test_карточка_и_публикация_курса_из_релиза_правятся(self):
		курс = frappe.get_doc("LMS Course", self.курс)
		курс.title = "Новое название"
		курс.short_introduction = "Новое описание"
		курс.published = 1
		курс.save()
		курс.published = 0
		курс.save()
		self.assertEqual(frappe.db.get_value("LMS Course", self.курс, "title"), "Новое название")

	def test_новая_версия_релиза_проходит(self):
		"""Проекция меняет порядок глав, переносит урок между главами и правит его — под флагом."""
		релиз = пример_релиза(self.ключ)
		уроки = {у["key"]: у for у in релиз["lessons"]}
		первая, вторая = релиз["chapters"]
		первая["lessons"], вторая["lessons"] = ["l-2"], ["l-3", "l-1"]
		релиз["chapters"] = [вторая, первая]
		релиз["lessons"] = [уроки["l-3"], уроки["l-1"], уроки["l-2"]]
		уроки["l-1"]["chapter"] = вторая["key"]
		уроки["l-1"]["title"] = "Урок первый, исправленный"
		куратор = создать_куратора(f"rel-guard-{frappe.generate_hash(length=6)}@example.com")

		ответ = service.опубликовать(релиз, None, "Administrator", [куратор])

		self.assertEqual((ответ["version"], ответ["unchanged"]), (2, False))
		self.assertEqual(frappe.db.get_value("Course Lesson", self.урок, "title"), "Урок первый, исправленный")
		# Тот же релиз со сменой инструкторов — `unchanged` под флагом.
		повтор = service.опубликовать(релиз, None, "Administrator", ["Administrator"])
		self.assertEqual((повтор["unchanged"], повтор["instructors"]), (True, ["Administrator"]))

	def test_удаление_курса_целиком_проходит(self):
		другой, _ = курс_из_релиза()
		service.удалить_курс(self.курс)

		self.assertFalse(frappe.db.exists("LMS Course", self.курс))
		self.assertFalse(frappe.db.exists("Course Lesson", self.урок))
		# Флаг удаления снят: другой курс из релиза по-прежнему охраняется.
		self.отказ(lambda: frappe.delete_doc("Course Lesson", урок_релиза(другой, "l-1")))
