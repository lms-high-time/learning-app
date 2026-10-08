# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Метод `publish_release`: граница прав и форма ответа (learning-services#500).

Поведение публикации проверяет `agent_learning/releases/test_service.py`;
здесь — что оно дошло до метода: кто может звать, что релиз принимается и
объектом, и строкой JSON, что отказ едет кодом контракта; инструкторы курса
по списку (learning-services#512), список курсов с релизом и открытие курса.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import authoring
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
)


class IntegrationTestPublishRelease(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"rel-api-{суффикс}@example.com")
		self.ученик = создать_ученика(f"rel-api-pupil-{суффикс}@example.com")
		self.ключ = f"api-{суффикс}"

	def test_ученику_нельзя(self):
		frappe.set_user(self.ученик)
		with self.assertRaises(frappe.PermissionError):
			authoring.publish_release(release=пример_релиза(self.ключ))
		self.assertFalse(frappe.db.exists("LMS Course", {"course_key": self.ключ}))

	def test_куратор_публикует_объектом(self):
		frappe.set_user(self.куратор)
		ответ = authoring.publish_release(release=пример_релиза(self.ключ))

		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(ответ["data"]["version"], 1)
		self.assertTrue(ответ["data"]["course_created"])

	def test_строка_json_то_же_что_объект(self):
		frappe.set_user(self.куратор)
		authoring.publish_release(release=пример_релиза(self.ключ))

		ответ = authoring.publish_release(release=json.dumps(пример_релиза(self.ключ), ensure_ascii=False))

		self.assertTrue(ответ["data"]["unchanged"], ответ)

	def test_отказ_кодом_контракта(self):
		frappe.set_user(self.куратор)
		ответ = authoring.publish_release(release={"format": "lms-release/2"})

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], "release_format_unsupported")

	def test_пустой_course_ищет_по_ключу(self):
		frappe.set_user(self.куратор)
		первый = authoring.publish_release(release=пример_релиза(self.ключ))["data"]

		ответ = authoring.publish_release(release=пример_релиза(self.ключ), course="")

		self.assertEqual(ответ["data"]["course"], первый["course"])

	def test_только_post(self):
		self.assertEqual(
			set(frappe.allowed_http_methods_for_whitelisted_func[authoring.publish_release]), {"POST"}
		)

	def test_курс_из_релиза_не_правится_по_кусочку(self):
		"""Курс из релиза правится новым релизом: методы сборки по кусочку отказывают."""
		frappe.set_user(self.куратор)
		данные = authoring.publish_release(release=пример_релиза(self.ключ))["data"]
		курс = данные["course"]
		урок = frappe.db.get_value("Course Lesson", {"course": курс, "title": "Урок первый"})
		глава = frappe.db.get_value("Course Lesson", урок, "chapter")
		вызовы = {
			"update_course": dict(course=курс, title="Другое"),
			"add_chapter": dict(course=курс, title="Лишняя"),
			"update_chapter": dict(chapter=глава, title="Другая"),
			"remove_chapter": dict(chapter=глава),
			"reorder_chapters": dict(course=курс, chapters=[глава]),
			"add_lesson": dict(chapter=глава, title="Лишний", body="# Лишний"),
			"update_lesson": dict(lesson=урок, title="Другой"),
			"move_lesson": dict(lesson=урок, position=2),
			"remove_lesson": dict(lesson=урок),
			"reorder_lessons": dict(chapter=глава, lessons=[урок]),
			"set_directive": dict(lesson=урок, teaching_directive="Директива"),
			"set_course_directive": dict(course=курс, teaching_directive="Директива"),
			"set_course_artifact": dict(course=курс, artifact="notebook", title="Тетрадь", blocks=[]),
			"set_course_artifact_template": dict(course=курс, artifact="notebook", template="any"),
			"upgrade_course_artifact": dict(course=курс, artifact="notebook"),
			"add_quiz": dict(lesson=урок, questions=[]),
			"add_question": dict(lesson=урок, question={"question": "?"}),
			"remove_question": dict(lesson=урок, question="any"),
			"add_homework": dict(lesson=урок, title="Задание", description="Сделайте"),
			"update_homework": dict(lesson=урок, title="Задание"),
			"remove_homework": dict(lesson=урок),
			"set_course_map": dict(course=курс, levels=[], nodes=[]),
		}
		for метод, параметры in вызовы.items():
			ответ = getattr(authoring, метод)(**параметры)
			self.assertEqual(ответ.get("error", {}).get("code"), "course_from_release", (метод, ответ))
		self.assertEqual(frappe.db.get_value("Course Lesson", урок, "title"), "Урок первый")

	def test_вопрос_квиза_на_уроке_из_релиза_не_правится(self):
		"""Вопрос Learning, оказавшийся в квизе урока из релиза, — тоже правка курса."""
		from lms_frappe_app.tests.sample_data import создать_вопрос, создать_квиз

		frappe.set_user(self.куратор)
		курс = authoring.publish_release(release=пример_релиза(self.ключ))["data"]["course"]
		урок = frappe.db.get_value("Course Lesson", {"course": курс, "title": "Урок первый"})
		frappe.set_user("Administrator")
		вопрос = создать_вопрос("Столица?", варианты=[("Москва", True), ("Тула", False)])
		создать_квиз(урок, [вопрос])
		frappe.set_user(self.куратор)

		ответ = authoring.update_question(question=вопрос, text="Другой вопрос")

		self.assertEqual(ответ["error"]["code"], "course_from_release", ответ)
		self.assertEqual(frappe.db.get_value("LMS Question", вопрос, "question"), "Столица?")

	def test_руководителю_нельзя(self):
		организация = создать_организацию(f"Релиз {frappe.generate_hash(length=6)}")
		руководитель = создать_менеджера(
			f"rel-api-mgr-{frappe.generate_hash(length=6)}@example.com", организация
		)
		frappe.set_user(руководитель)
		with self.assertRaises(frappe.PermissionError):
			authoring.publish_release(release=пример_релиза(self.ключ))


class IntegrationTestИнструкторыРелиза(IntegrationTestCase):
	"""`publish_release(…, instructors)`: список заменяет инструкторов курса (learning-services#512)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.публикатор = создать_куратора(f"rel-pub-{суффикс}@example.com")
		self.первый = создать_куратора(f"rel-a-{суффикс}@example.com")
		self.второй = создать_куратора(f"rel-b-{суффикс}@example.com", роль="Moderator")
		self.ученик = создать_ученика(f"rel-pupil-{суффикс}@example.com")
		self.ключ = f"ins-{суффикс}"
		frappe.set_user(self.публикатор)

	def опубликовать(self, релиз: dict | None = None, **аргументы) -> dict:
		return authoring.publish_release(release=релиз or пример_релиза(self.ключ), **аргументы)

	def инструкторы(self, курс: str) -> list[str]:
		return frappe.get_all(
			"Course Instructor",
			filters={"parenttype": "LMS Course", "parent": курс},
			pluck="instructor",
			order_by="idx asc",
		)

	def другой_релиз(self) -> dict:
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, второе издание"
		return релиз

	def test_без_списка_новый_курс_получает_публикатора(self):
		данные = self.опубликовать()["data"]

		self.assertEqual(данные["instructors"], [self.публикатор])
		self.assertEqual(self.инструкторы(данные["course"]), [self.публикатор])

	def test_список_заменяет_набор_и_публикатор_не_добавляется(self):
		данные = self.опубликовать(instructors=[self.первый, self.второй.upper()])["data"]

		self.assertEqual(данные["instructors"], [self.первый, self.второй])
		self.assertEqual(self.инструкторы(данные["course"]), [self.первый, self.второй])

		данные = self.опубликовать(self.другой_релиз(), instructors=json.dumps([self.второй]))["data"]

		self.assertEqual((данные["version"], данные["instructors"]), (2, [self.второй]))

	def test_без_списка_инструкторы_не_трогаются(self):
		курс = self.опубликовать(instructors=[self.первый])["data"]["course"]

		данные = self.опубликовать(self.другой_релиз())["data"]

		self.assertEqual((данные["version"], данные["instructors"]), (2, [self.первый]))
		self.assertEqual(self.инструкторы(курс), [self.первый])

	def test_список_применяется_к_неизменному_релизу(self):
		"""Инструкторы не входят в дайджест: смена кураторов — без новой версии."""
		курс = self.опубликовать()["data"]["course"]

		данные = self.опубликовать(instructors=[self.первый])["data"]

		self.assertTrue(данные["unchanged"])
		self.assertEqual((данные["version"], данные["instructors"]), (1, [self.первый]))
		self.assertEqual(self.инструкторы(курс), [self.первый])
		self.assertEqual(frappe.db.count("Agent Course Release", {"course": курс}), 1)

	def test_отказы_до_первой_записи(self):
		курс = self.опубликовать(instructors=[self.первый])["data"]["course"]
		for инструкторы, код, лишние in (
			([], "instructors_empty", None),
			("[]", "instructors_empty", None),
			([self.первый, "nobody-here@example.com"], "instructor_not_found", ["nobody-here@example.com"]),
			([self.второй, self.ученик], "instructor_not_author", [self.ученик]),
		):
			for релиз in (self.другой_релиз(), пример_релиза(f"new-{self.ключ}")):
				ответ = self.опубликовать(релиз, instructors=инструкторы)

				self.assertEqual(ответ.get("error", {}).get("code"), код, (инструкторы, ответ))
				if лишние:
					self.assertEqual(ответ["error"]["users"], лишние)
		self.assertEqual(self.инструкторы(курс), [self.первый])
		self.assertEqual(frappe.db.count("Agent Course Release", {"course": курс}), 1)
		self.assertFalse(frappe.db.exists("LMS Course", {"course_key": f"new-{self.ключ}"}))

	def test_другой_куратор_переопубликует_курс(self):
		"""Уроки курса, опубликованного одним куратором, правит релиз другого:
		Learning даёт Course Creator запись `Course Lesson` только своих."""
		frappe.set_user(self.первый)
		курс = self.опубликовать()["data"]["course"]
		коллега = создать_куратора(f"rel-c-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(коллега)

		данные = self.опубликовать(self.другой_релиз(), instructors=[self.первый])

		self.assertTrue(данные["ok"], данные)
		self.assertEqual(данные["data"]["lessons"]["updated"], ["l-1"])
		self.assertEqual(
			frappe.db.get_value("Course Lesson", {"course": курс, "title": "Урок первый, второе издание"}, "owner"),
			self.первый,
		)
		self.assertEqual(self.инструкторы(курс), [self.первый])

	def test_куратор_переопубликует_курс_администратора(self):
		frappe.set_user("Administrator")
		курс = self.опубликовать()["data"]["course"]
		frappe.set_user(self.первый)

		данные = self.опубликовать(self.другой_релиз())

		self.assertTrue(данные["ok"], данные)
		self.assertEqual((данные["data"]["version"], данные["data"]["lessons"]["updated"]), (2, ["l-1"]))
		self.assertEqual(
			frappe.db.get_value("Course Lesson", {"course": курс, "title": "Урок первый, второе издание"}, "owner"),
			"Administrator",
		)

	def test_отключённый_пользователь_не_инструктор(self):
		"""Отключённая учётная запись курс не ведёт — даже с авторской ролью."""
		отключённый = создать_куратора(f"rel-off-{frappe.generate_hash(length=6)}@example.com")
		frappe.db.set_value("User", отключённый, "enabled", 0)

		ответ = self.опубликовать(instructors=[self.первый, отключённый])

		self.assertEqual(ответ.get("error", {}).get("code"), "instructor_not_found", ответ)
		self.assertEqual(ответ["error"]["users"], [отключённый])
		self.assertFalse(frappe.db.exists("LMS Course", {"course_key": self.ключ}))


class IntegrationTestКурсыИОткрытие(IntegrationTestCase):
	"""`list_courses` с релизом и `publish_course` курса из релиза."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"rel-list-{суффикс}@example.com")
		self.ключ = f"list-{суффикс}"
		frappe.set_user(self.куратор)

	def test_список_курсов_с_ключом_и_действующим_релизом(self):
		анонс = authoring.create_course(title="Анонс", summary="к")["data"]["id"]
		курс = authoring.publish_release(release=пример_релиза(self.ключ))["data"]["course"]
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Другой"
		authoring.publish_release(release=релиз)

		строки = {к["id"]: к for к in authoring.list_courses()["data"]["courses"]}

		self.assertEqual((строки[анонс]["course_key"], строки[анонс]["release"]), (None, None))
		self.assertEqual(строки[курс]["course_key"], self.ключ)
		self.assertEqual(строки[курс]["release"]["version"], 2)
		действующий = frappe.db.get_value("LMS Course", курс, "active_release")
		self.assertEqual(
			строки[курс]["release"]["published_at"],
			frappe.db.get_value("Agent Course Release", действующий, "published_at").isoformat(),
		)

	def test_курс_снимается_с_публикации_и_возвращается_обратно(self):
		"""Снятие — обратимая правка каталога: прогресс ученика переживает и
		снятие, и повторную публикацию."""
		курс = authoring.publish_release(release=пример_релиза(self.ключ))["data"]["course"]
		урок = frappe.db.get_value("Course Lesson", {"course": курс, "title": "Урок первый"})
		ученик = создать_ученика(f"reader-{frappe.generate_hash(length=6)}@example.com")
		self.assertTrue(authoring.publish_course(course=курс)["data"]["published"])
		frappe.get_doc(
			{
				"doctype": "LMS Course Progress",
				"lesson": урок,
				"member": ученик,
				"course": курс,
				"status": "Complete",
			}
		).insert(ignore_permissions=True)

		ответ = authoring.unpublish_course(course=курс)["data"]

		self.assertFalse(ответ["published"])
		self.assertFalse(frappe.db.get_value("LMS Course", курс, "published"))
		self.assertTrue(
			frappe.db.exists("LMS Course Progress", {"member": ученик, "lesson": урок}),
			"прогресс ученика пропал вместе с публикацией",
		)
		self.assertTrue(authoring.publish_course(course=курс)["data"]["published"])
