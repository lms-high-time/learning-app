# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Курс из релиза правит только публикация (learning-services#500, #512).

Поля релиза у `LMS Course`, порядок глав курса, хуки `validate` и `on_trash`
у `Course Chapter` и `Course Lesson`, удаление строк оглавления, переименование.
Пути — те, которыми ходят Desk и Learning: `frappe.client`, `frappe.delete_doc`,
`delete_documents`.
"""

import frappe
from frappe.client import rename_doc, set_value
from frappe.client import save as сохранить_из_desk
from frappe.tests import IntegrationTestCase
from lms.lms.api import delete_documents

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
		with self.assertRaisesRegex(frappe.ValidationError, "релиз другого курса"):
			курс.save()

		курс = frappe.get_doc("LMS Course", self.курс)
		курс.active_release = f"REL-нет-{frappe.generate_hash(length=6)}"
		курс.flags.from_release = True
		# Обычную правку остановит ещё проверка Link; хук держит и путь мимо неё.
		курс.flags.ignore_links = True
		with self.assertRaisesRegex(frappe.ValidationError, "Такого релиза нет"):
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
		было = [с.chapter for с in курс.chapters]
		курс.chapters = курс.chapters[::-1]
		for idx, строка in enumerate(курс.chapters, start=1):
			строка.idx = idx
		self.отказ(курс.save)
		# Строки, переставленные в памяти без `idx`, порядка Learning не меняют:
		# он читается по `idx`.
		курс.reload()
		курс.chapters = курс.chapters[::-1]
		курс.save()
		курс.reload()
		self.assertEqual([с.chapter for с in курс.chapters], было)
		курс.chapters = курс.chapters[:1]
		self.отказ(курс.save)
		курс.reload()
		лишняя = frappe.get_doc(
			{"doctype": "Course Chapter", "course": self.свободный_курс, "title": "Чужая"}
		).insert()
		курс.append("chapters", {"chapter": лишняя.name})
		self.отказ(курс.save)

	def test_строку_оглавления_курса_из_релиза_не_удалить(self):
		"""Строку удаляет `frappe.delete_doc` по праву `delete` на родителе:
		Desk (`delete_items`) — куратором, `delete_documents` — модератором."""
		куратор = создать_куратора(f"rel-guard-{frappe.generate_hash(length=6)}@example.com")
		модератор = создать_куратора(f"rel-guard-{frappe.generate_hash(length=6)}@example.com", "Moderator")
		глава = frappe.db.get_value("Chapter Reference", {"parent": self.курс, "chapter": self.глава}, "name")
		урок = frappe.db.get_value("Lesson Reference", {"parent": self.глава, "lesson": self.урок}, "name")

		frappe.set_user(куратор)
		self.отказ(lambda: frappe.delete_doc("Chapter Reference", глава))
		self.отказ(lambda: frappe.delete_doc("Lesson Reference", урок))
		frappe.set_user(модератор)
		self.отказ(lambda: delete_documents("Chapter Reference", [глава]))
		self.отказ(lambda: delete_documents("Lesson Reference", [урок]))

		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("Chapter Reference", глава))
		self.assertTrue(frappe.db.exists("Lesson Reference", урок))

	def test_строку_оглавления_курса_без_релиза_удалить_можно(self):
		модератор = создать_куратора(f"rel-guard-{frappe.generate_hash(length=6)}@example.com", "Moderator")
		урок = frappe.db.get_value("Lesson Reference", {"parent": self.свободная_глава}, "name")
		глава = frappe.db.get_value("Chapter Reference", {"parent": self.свободный_курс}, "name")
		self.assertTrue(урок and глава)

		frappe.set_user(модератор)
		delete_documents("Lesson Reference", [урок])
		frappe.delete_doc("Chapter Reference", глава)

		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("Lesson Reference", урок))
		self.assertFalse(frappe.db.exists("Chapter Reference", глава))

	def test_порядок_оглавления_через_idx_не_обойти(self):
		"""`frappe.client.save` с переставленными `idx` и `frappe.client.set_value`
		по строке: второй сохраняет родителя, и его `validate` видит перестановку."""

		def порядок() -> list[str]:
			return frappe.get_all(
				"Chapter Reference", filters={"parent": self.курс}, pluck="chapter", order_by="idx asc"
			)

		было = порядок()
		курс = frappe.get_doc("LMS Course", self.курс).as_dict()
		for строка, idx in zip(курс["chapters"], range(len(курс["chapters"]), 0, -1), strict=True):
			строка["idx"] = idx
		self.отказ(lambda: сохранить_из_desk(frappe.as_json(курс)))
		self.отказ(lambda: set_value("Chapter Reference", курс["chapters"][0]["name"], '{"idx": 5}'))
		урок = frappe.db.get_value("Lesson Reference", {"parent": self.глава, "lesson": self.урок}, "name")
		self.отказ(lambda: set_value("Lesson Reference", урок, '{"idx": 5}'))
		self.assertEqual(порядок(), было)

	def test_курс_главу_и_урок_курса_из_релиза_не_переименовать(self):
		for doctype, имя, свободный in (
			("Course Lesson", self.урок, self.свободный_урок),
			("Course Chapter", self.глава, self.свободная_глава),
			("LMS Course", self.курс, self.свободный_курс),
		):
			with self.subTest(doctype=doctype):
				self.отказ(lambda doctype=doctype, имя=имя: rename_doc(doctype, имя, f"{имя}-renamed"))
				self.отказ(
					lambda doctype=doctype, имя=имя, свободный=свободный: rename_doc(
						doctype, имя, свободный, merge=True
					)
				)
				# Слияние записи курса без релиза с записью курса из релиза — тоже правка его.
				self.отказ(
					lambda doctype=doctype, имя=имя, свободный=свободный: rename_doc(
						doctype, свободный, имя, merge=True
					)
				)
				self.assertTrue(frappe.db.exists(doctype, имя))
				self.assertTrue(frappe.db.exists(doctype, свободный))

	def test_курс_без_релиза_переименовывается(self):
		другой_урок = создать_урок(f"Без релиза {frappe.generate_hash(length=6)}")

		новое = rename_doc("Course Lesson", self.свободный_урок, f"{self.свободный_урок}-renamed")
		rename_doc("Course Lesson", другой_урок, новое, merge=True)
		курс = rename_doc("LMS Course", self.свободный_курс, f"{self.свободный_курс}-renamed")

		self.assertTrue(frappe.db.exists("Course Lesson", новое))
		self.assertFalse(frappe.db.exists("Course Lesson", другой_урок))
		self.assertTrue(frappe.db.exists("LMS Course", курс))

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
		self.assertEqual(
			frappe.db.get_value("Course Lesson", self.урок, "title"), "Урок первый, исправленный"
		)
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
