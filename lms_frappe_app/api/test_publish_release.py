# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Метод `publish_release`: граница прав и форма ответа (learning-services#500).

Поведение публикации проверяет `agent_learning/releases/test_service.py`;
здесь — что оно дошло до метода: кто может звать, что релиз принимается и
объектом, и строкой JSON, что отказ едет кодом контракта; инструкторы курса
по списку (learning-services#512), коммит источника (learning-services#514),
список курсов с релизом и открытие курса.
"""

import json
from unittest import mock

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.releases import service
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

	def test_знак_не_для_имени_в_названии_отказ_до_записи(self):
		"""`<` и `>` в названии главы или урока — отказ по контракту до первой
		записи, а не `NameError` Frappe посреди публикации."""
		frappe.set_user(self.куратор)
		релиз = пример_релиза(self.ключ)
		релиз["chapters"][0]["title"] = "Глава <b>"
		релиз["lessons"][0]["title"] = "Урок a > b"

		with (
			mock.patch.object(service, "_завести_курс") as завести,
			mock.patch.object(service.projection, "спроецировать") as спроецировать,
		):
			ответ = authoring.publish_release(release=релиз)

		self.assertEqual(ответ["error"]["code"], "release_inconsistent", ответ)
		self.assertEqual(
			[(п["code"], п["where"], п["chars"]) for п in ответ["error"]["problems"]],
			[
				("title_forbidden_chars", "chapters[ch-1].title", ["<", ">"]),
				("title_forbidden_chars", "lessons[l-1].title", [">"]),
			],
		)
		завести.assert_not_called()
		спроецировать.assert_not_called()
		self.assertFalse(frappe.db.exists("LMS Course", {"course_key": self.ключ}))

	def test_знак_не_для_имени_в_новом_релизе_курс_не_трогает(self):
		frappe.set_user(self.куратор)
		курс = authoring.publish_release(release=пример_релиза(self.ключ))["data"]["course"]
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок <новый>"

		ответ = authoring.publish_release(release=релиз)

		self.assertEqual(ответ["error"]["code"], "release_inconsistent", ответ)
		self.assertEqual(frappe.db.count("Agent Course Release", {"course": курс}), 1)
		self.assertFalse(frappe.db.exists("Course Lesson", {"course": курс, "title": "Урок <новый>"}))

	def test_только_post(self):
		self.assertEqual(
			set(frappe.allowed_http_methods_for_whitelisted_func[authoring.publish_release]), {"POST"}
		)

	def test_курс_из_релиза_не_правится_мимо_релиза(self):
		"""Курс из релиза правится новым релизом: правка карточки мимо него — отказ."""
		frappe.set_user(self.куратор)
		курс = authoring.publish_release(release=пример_релиза(self.ключ))["data"]["course"]

		ответ = authoring.update_course(course=курс, title="Другое")

		self.assertEqual(ответ.get("error", {}).get("code"), "course_from_release", ответ)
		self.assertNotEqual(frappe.db.get_value("LMS Course", курс, "title"), "Другое")

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


class IntegrationTestКоммитРелиза(IntegrationTestCase):
	"""`publish_release(…, commit)`: коммит источника пишется в новую версию
	(learning-services#514)."""

	#: SHA-1 и SHA-256 — git знает оба формата хеша.
	КОММИТ = "0123456789abcdef0123456789abcdef01234567"
	ДРУГОЙ = "fedcba9876543210" * 4

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		frappe.set_user(создать_куратора(f"rel-commit-{суффикс}@example.com"))
		self.ключ = f"commit-{суффикс}"

	def опубликовать(self, релиз: dict | None = None, **аргументы) -> dict:
		return authoring.publish_release(release=релиз or пример_релиза(self.ключ), **аргументы)

	def другой_релиз(self) -> dict:
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, второе издание"
		return релиз

	def коммиты(self, курс: str) -> list[tuple[int, str | None]]:
		return [
			(р.version, р.source_commit)
			for р in frappe.get_all(
				"Agent Course Release",
				filters={"course": курс},
				fields=["version", "source_commit"],
				order_by="version asc",
			)
		]

	def test_коммит_пишется_в_новую_версию(self):
		данные = self.опубликовать(commit=self.КОММИТ)["data"]

		self.assertEqual((данные["version"], данные["commit"]), (1, self.КОММИТ))

		данные = self.опубликовать(self.другой_релиз(), commit=self.ДРУГОЙ)["data"]

		self.assertEqual((данные["version"], данные["commit"]), (2, self.ДРУГОЙ))
		self.assertEqual(self.коммиты(данные["course"]), [(1, self.КОММИТ), (2, self.ДРУГОЙ)])
		история = authoring.course_releases(course=данные["course"])["data"]["releases"]
		self.assertEqual([(р["version"], р["commit"]) for р in история], [(2, self.ДРУГОЙ), (1, self.КОММИТ)])

	def test_на_unchanged_коммит_первой_публикации(self):
		"""Тот же релиз из другого коммита — `unchanged`: коммит в дайджест не
		входит, а запись версии неизменяема."""
		курс = self.опубликовать(commit=self.КОММИТ)["data"]["course"]

		for коммит in (self.ДРУГОЙ, None):
			with self.subTest(коммит=коммит):
				данные = self.опубликовать(commit=коммит)["data"]

				self.assertTrue(данные["unchanged"])
				self.assertEqual((данные["version"], данные["commit"]), (1, self.КОММИТ))
		self.assertEqual(self.коммиты(курс), [(1, self.КОММИТ)])

	def test_без_параметра_коммита_нет(self):
		данные = self.опубликовать()["data"]

		self.assertIn("commit", данные)
		self.assertIsNone(данные["commit"])
		self.assertEqual(self.коммиты(данные["course"]), [(1, None)])
		[версия] = authoring.course_releases(course=данные["course"])["data"]["releases"]
		self.assertIsNone(версия["commit"])

	def test_версия_без_коммита_его_не_получает(self):
		"""Версия, впервые опубликованная без коммита, так и остаётся без него."""
		курс = self.опубликовать()["data"]["course"]

		данные = self.опубликовать(commit=self.КОММИТ)["data"]

		self.assertTrue(данные["unchanged"])
		self.assertIsNone(данные["commit"])
		self.assertEqual(self.коммиты(курс), [(1, None)])

	def test_неверный_коммит_отказ_до_первой_записи(self):
		курс = self.опубликовать(commit=self.КОММИТ)["data"]["course"]
		for коммит in (
			"",
			self.КОММИТ[:7],
			self.КОММИТ.upper(),
			self.КОММИТ + "8",
			f" {self.КОММИТ}",
			f"{self.КОММИТ}\n",
			"g" * 40,
			self.ДРУГОЙ[:63],
			40,
			[self.КОММИТ],
		):
			for релиз in (self.другой_релиз(), пример_релиза(f"new-{self.ключ}")):
				with (
					self.subTest(коммит=коммит, ключ=релиз["course"]["key"]),
					mock.patch.object(service, "_разобрать") as разобрать,
				):
					ответ = self.опубликовать(релиз, commit=коммит)

					self.assertEqual(ответ.get("error", {}).get("code"), "invalid_commit", ответ)
					разобрать.assert_not_called()
		self.assertEqual(self.коммиты(курс), [(1, self.КОММИТ)])
		self.assertFalse(frappe.db.exists("LMS Course", {"course_key": f"new-{self.ключ}"}))


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
