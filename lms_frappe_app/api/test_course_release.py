# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Просмотр действующего релиза и история релизов для автора (learning-services#512)."""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import authoring
from lms_frappe_app.tests.release_sample import пример_релиза, релиз_двух_целей
from lms_frappe_app.tests.sample_data import создать_куратора, создать_курс, создать_ученика


class IntegrationTestПросмотрРелиза(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"rel-view-{суффикс}@example.com")
		self.ключ = f"rel-view-{суффикс}"
		frappe.set_user(self.куратор)
		self.первый = self.опубликовать()
		self.курс = self.первый["course"]

	def опубликовать(self, релиз: dict | None = None) -> dict:
		ответ = authoring.publish_release(release=релиз or пример_релиза(self.ключ))
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def просмотр(self, **аргументы) -> dict:
		ответ = authoring.course_release(course=self.курс, **аргументы)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def код(self, ответ: dict) -> str | None:
		return None if ответ["ok"] else ответ["error"]["code"]

	# --- релиз целиком ---

	def test_автор_видит_релиз_с_ответами(self):
		данные = self.просмотр()

		self.assertEqual(set(данные), {"course", "chapters", "lessons", "document"})
		курс = данные["course"]
		self.assertEqual(
			(курс["id"], курс["key"], курс["title"], курс["release"], курс["version"], курс["published_by"]),
			(self.курс, self.ключ, "Пример курса", self.первый["release"], 1, self.куратор),
		)
		self.assertTrue(курс["published_at"])
		self.assertEqual(
			[(г["key"], г["title"], г["lessons"]) for г in данные["chapters"]],
			[("ch-1", "Глава первая", ["l-1", "l-2"]), ("ch-2", "Глава вторая", ["l-3"])],
		)
		self.assertEqual(данные["chapters"][0]["description"], "Что изменится после первой главы.")
		второй = данные["lessons"][1]
		self.assertEqual([у["key"] for у in данные["lessons"]], ["l-1", "l-2", "l-3"])
		self.assertEqual(
			(
				второй["title"],
				второй["hook"],
				второй["chapter"],
				второй["pass_percentage"],
				второй["sections"],
			),
			("Урок второй", "Зачин урока «Урок второй»", "ch-1", 70, ["log", "rules"]),
		)
		self.assertIsNone(второй["homework"])
		self.assertEqual(данные["lessons"][2]["homework"]["due_days"], 3)
		self.assertEqual(второй["objectives"], пример_релиза()["lessons"][1]["objectives"])
		self.assertEqual(
			второй["questions"],
			[
				{
					"key": "S1/l-2-D1",
					"objective": "l-2-D1",
					"text": "Ситуация и вопрос",
					"options": [{"key": "V1", "text": "Первый"}, {"key": "V2", "text": "Второй"}],
					"correct": "V1",
					"explanation": "Потому что так велит условие.",
				}
			],
		)
		документ = данные["document"]
		образец = пример_релиза()["document"]
		self.assertEqual(
			(документ["key"], документ["title"], документ["purpose"]),
			("notebook", "Тетрадь", "Зачем ученику тетрадь."),
		)
		self.assertEqual(документ["sections"], образец["sections"])
		self.assertNotIn("agent", данные)

	def test_новый_релиз_виден_сразу(self):
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, переписанный"
		второй = self.опубликовать(релиз)

		данные = self.просмотр()

		self.assertEqual((данные["course"]["version"], данные["course"]["release"]), (2, второй["release"]))
		self.assertEqual(данные["lessons"][0]["title"], "Урок первый, переписанный")

	def test_релиз_без_документа(self):
		курс = self.опубликовать(релиз_двух_целей(f"{self.ключ}-two"))["course"]

		данные = authoring.course_release(course=курс)["data"]

		self.assertIsNone(данные["document"])
		self.assertEqual([ц["key"] for ц in данные["lessons"][0]["objectives"]], ["l-1-D1", "l-1-D2"])

	# --- урок ---

	def test_урок_с_пакетом_агента_и_рамкой(self):
		данные = self.просмотр(lesson="l-2")

		self.assertEqual(set(данные), {"course", "lesson", "agent"})
		self.assertEqual(данные["course"]["version"], 1)
		self.assertEqual(данные["lesson"]["key"], "l-2")
		self.assertEqual(данные["lesson"], self.просмотр()["lessons"][1])
		пакет = пример_релиза()["agent"]
		self.assertEqual(
			данные["agent"],
			{
				"frame": пакет["frame"],
				"learn_about_student": пакет["learn_about_student"],
				"lesson": пакет["lessons"]["l-2"],
			},
		)

	def test_урок_без_рамки_в_релизе(self):
		курс = self.опубликовать(релиз_двух_целей(f"{self.ключ}-two"))["course"]

		данные = authoring.course_release(course=курс, lesson="l-1")["data"]

		self.assertEqual(set(данные["agent"]), {"lesson"})
		self.assertTrue(данные["agent"]["lesson"]["directive"])

	def test_неизвестный_урок_и_урок_снятый_из_релиза(self):
		ответ = authoring.course_release(course=self.курс, lesson="l-9")
		self.assertEqual(self.код(ответ), "lesson_not_in_release")
		self.assertEqual(
			(ответ["error"]["course"], ответ["error"]["lesson_key"], ответ["error"]["release"]),
			(self.курс, "l-9", self.первый["release"]),
		)

		релиз = пример_релиза(self.ключ)
		релиз["chapters"].pop()
		релиз["lessons"].pop()
		del релиз["agent"]["lessons"]["l-3"]
		self.опубликовать(релиз)

		self.assertEqual(
			self.код(authoring.course_release(course=self.курс, lesson="l-3")), "lesson_not_in_release"
		)

	# --- отказы ---

	def test_курс_без_релиза_и_неизвестный_курс(self):
		анонс = создать_курс(f"Анонс {frappe.generate_hash(length=6)}")

		self.assertEqual(self.код(authoring.course_release(course=анонс)), "course_not_released")
		self.assertEqual(
			self.код(authoring.course_release(course=анонс, lesson="l-1")), "course_not_released"
		)
		self.assertEqual(self.код(authoring.course_release(course="такого-курса-нет")), "course_not_found")
		self.assertEqual(self.код(authoring.course_releases(course="такого-курса-нет")), "course_not_found")
		self.assertEqual(authoring.course_releases(course=анонс)["data"], {"course": анонс, "releases": []})

	def test_ученику_тестеру_и_гостю_просмотр_и_история_закрыты(self):
		"""Ответы квиза — только авторским ролям: тестер курса — ученик с
		ранним доступом, а не автор. Гостю — 401: сначала вход."""
		тестер = создать_ученика(f"rel-view-t-{frappe.generate_hash(length=6)}@example.com")
		self.assertTrue(authoring.add_testers(course=self.курс, users=тестер)["ok"])
		for пользователь, ошибка in (
			(создать_ученика(f"rel-view-s-{frappe.generate_hash(length=6)}@example.com"), frappe.PermissionError),
			(тестер, frappe.PermissionError),
			("Guest", frappe.AuthenticationError),
		):
			frappe.set_user(пользователь)
			for вызов in (
				lambda: authoring.course_release(course=self.курс),
				lambda: authoring.course_release(course=self.курс, lesson="l-1"),
				lambda: authoring.course_releases(course=self.курс),
			):
				with self.subTest(пользователь=пользователь), self.assertRaises(ошибка):
					вызов()

	def test_методы_читаются_и_get_и_post(self):
		for метод in (authoring.course_release, authoring.course_releases):
			with self.subTest(метод=метод.__name__):
				глаголы = set(frappe.allowed_http_methods_for_whitelisted_func[метод])
				self.assertLessEqual({"GET", "POST"}, глаголы)

	# --- история ---

	def test_история_свежие_вперёд(self):
		релиз = пример_релиза(self.ключ)
		релиз["document"] = None
		for урок in релиз["lessons"]:
			урок["sections"] = []
		for срез in релиз["agent"]["lessons"].values():
			срез["sections"] = {}
		второй = self.опубликовать(релиз)
		# Тот же релиз ещё раз — не новая версия.
		self.опубликовать(релиз)

		данные = authoring.course_releases(course=self.курс)["data"]

		self.assertEqual(данные["course"], self.курс)
		история = данные["releases"]
		self.assertEqual(
			[(р["release"], р["version"], р["active"], р["document_key"]) for р in история],
			[(второй["release"], 2, True, None), (self.первый["release"], 1, False, "notebook")],
		)
		self.assertEqual({р["published_by"] for р in история}, {self.куратор})
		self.assertGreaterEqual(история[0]["published_at"], история[1]["published_at"])
		self.assertEqual(len({р["digest"] for р in история}), 2)
		self.assertTrue(all(len(р["digest"]) == 64 for р in история))
