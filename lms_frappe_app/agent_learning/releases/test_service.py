# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Публикация релиза: версия, атомарность, курс (learning-services#500)."""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from lms.lms.utils import get_chapters, get_lessons

from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases import service
from lms_frappe_app.api import authoring
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import создать_куратора, создать_курс, создать_урок

РЕЛИЗ = "Agent Course Release"


class IntegrationTestПубликацияРелиза(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"rel-svc-{суффикс}@example.com")
		self.ключ = f"sample-{суффикс}"
		frappe.set_user(self.куратор)

	def опубликовать(self, релиз: dict | str | None = None, course: str | None = None) -> dict:
		return service.опубликовать(
			релиз if релиз is not None else пример_релиза(self.ключ), course, self.куратор
		)

	def курс_по_ключу(self, ключ: str | None = None) -> str | None:
		return frappe.db.get_value("LMS Course", {"course_key": ключ or self.ключ})

	def отказ(self, код: str, *args, **kwargs) -> Отказ:
		with self.assertRaises(Отказ) as пойман:
			self.опубликовать(*args, **kwargs)
		self.assertEqual(пойман.exception.код, код, пойман.exception.подробности)
		return пойман.exception

	def test_первый_релиз_заводит_черновик(self):
		ответ = self.опубликовать()

		self.assertTrue(ответ["course_created"])
		self.assertEqual((ответ["version"], ответ["unchanged"], ответ["published"]), (1, False, False))
		self.assertEqual(ответ["course_key"], self.ключ)
		self.assertEqual(
			ответ["lessons"], {"created": ["l-1", "l-2", "l-3"], "updated": [], "removed": [], "restored": []}
		)
		self.assertEqual(ответ["chapters"]["created"], ["ch-1", "ch-2"])
		self.assertEqual(ответ["document"], {"artifact": "notebook", "version": 1})
		self.assertEqual(ответ["warnings"], [])
		курс = frappe.get_doc("LMS Course", ответ["course"])
		self.assertEqual(курс.course_key, self.ключ)
		self.assertEqual(курс.active_release, ответ["release"])
		self.assertEqual(курс.title, "Пример курса")
		self.assertEqual(курс.short_introduction, "Короткая карточка")
		self.assertEqual(курс.course_promise, "К концу курса вы умеете пример.")
		self.assertEqual(json.loads(курс.course_attribution)["license"], "CC BY-SA 4.0")
		self.assertEqual([и.instructor for и in курс.instructors], [self.куратор])
		self.assertEqual(
			[у["title"] for у in get_lessons(курс.name)], ["Урок первый", "Урок второй", "Урок третий"]
		)
		релиз = frappe.db.get_value(
			РЕЛИЗ,
			ответ["release"],
			["course_key", "published_by", "release_format", "document_key"],
			as_dict=True,
		)
		self.assertEqual(
			(релиз.course_key, релиз.published_by, релиз.release_format, релиз.document_key),
			(self.ключ, self.куратор, "lms-release/1", "notebook"),
		)

	def test_повтор_того_же_без_новой_версии(self):
		первый = self.опубликовать()

		ответ = self.опубликовать()

		self.assertTrue(ответ["unchanged"])
		self.assertFalse(ответ["course_created"])
		self.assertEqual((ответ["version"], ответ["release"]), (1, первый["release"]))
		self.assertEqual(ответ["document"], {"artifact": "notebook", "version": 1})
		self.assertEqual(ответ["lessons"], {"created": [], "updated": [], "removed": [], "restored": []})
		self.assertEqual(frappe.db.count(РЕЛИЗ, {"course": первый["course"]}), 1)

	def test_тот_же_релиз_строкой_и_в_другом_порядке_ключей_без_новой_версии(self):
		self.опубликовать()
		релиз = пример_релиза(self.ключ)
		строка = json.dumps(dict(reversed(list(релиз.items()))), ensure_ascii=False)

		self.assertTrue(self.опубликовать(строка)["unchanged"])

	def test_правка_следующая_версия(self):
		первый = self.опубликовать()
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, исправленный"

		ответ = self.опубликовать(релиз)

		self.assertEqual((ответ["version"], ответ["unchanged"]), (2, False))
		self.assertEqual(ответ["lessons"]["updated"], ["l-1"])
		self.assertEqual(ответ["course"], первый["course"])
		self.assertEqual(
			frappe.db.get_value("LMS Course", ответ["course"], "active_release"), ответ["release"]
		)
		self.assertEqual(
			json.loads(frappe.db.get_value(РЕЛИЗ, первый["release"], "snapshot"))["lessons"][0]["title"],
			"Урок первый",
		)

	def test_снятый_урок_в_ответе(self):
		первый = self.опубликовать()
		релиз = пример_релиза(self.ключ)
		релиз["chapters"] = релиз["chapters"][:1]
		релиз["lessons"] = релиз["lessons"][:2]

		ответ = self.опубликовать(релиз)

		self.assertEqual(ответ["lessons"]["removed"], ["l-3"])
		self.assertEqual(ответ["chapters"]["removed"], ["ch-2"])
		self.assertEqual(len(get_lessons(первый["course"])), 2)

	def test_опубликованный_курс_остаётся_опубликованным(self):
		первый = self.опубликовать()
		frappe.db.set_value("LMS Course", первый["course"], "published", 1)
		релиз = пример_релиза(self.ключ)
		релиз["course"]["title"] = "Пример курса, второе издание"

		ответ = self.опубликовать(релиз)

		self.assertTrue(ответ["published"])
		self.assertEqual(frappe.db.get_value("LMS Course", ответ["course"], "published"), 1)

	def test_отказ_посреди_записи_откатывает_всё(self):
		with patch(
			"lms_frappe_app.agent_learning.releases.document.спроецировать", side_effect=Отказ("boom", "сбой")
		):
			self.отказ("boom")

		self.assertIsNone(self.курс_по_ключу())
		self.assertFalse(frappe.db.exists("Course Chapter", {"title": "Глава первая", "owner": self.куратор}))
		self.assertFalse(frappe.db.exists("Course Lesson", {"title": "Урок первый", "owner": self.куратор}))
		self.assertFalse(frappe.db.exists(РЕЛИЗ, {"course_key": self.ключ}))

	def test_отказ_посреди_правки_оставляет_прежний_релиз(self):
		первый = self.опубликовать()
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, исправленный"
		with patch(
			"lms_frappe_app.agent_learning.releases.document.спроецировать", side_effect=Отказ("boom", "сбой")
		):
			self.отказ("boom", релиз)

		self.assertEqual(
			frappe.db.get_value("LMS Course", первый["course"], "active_release"), первый["release"]
		)
		self.assertEqual(get_lessons(первый["course"])[0]["title"], "Урок первый")
		self.assertEqual(frappe.db.count(РЕЛИЗ, {"course": первый["course"]}), 1)

	def test_отказы_схемы_и_проверок(self):
		self.отказ(service.РЕЛИЗ_НЕВЕРЕН, "{не json")
		self.отказ(service.РЕЛИЗ_НЕВЕРЕН, "[]")
		релиз = пример_релиза(self.ключ)
		релиз["format"] = "lms-release/2"
		отказ = self.отказ(service.ФОРМАТ_НЕ_ТОТ, релиз)
		self.assertEqual(отказ.подробности["supported"], ["lms-release/1"])
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["body"] = "материал"
		отказ = self.отказ(service.РЕЛИЗ_НЕВЕРЕН, релиз)
		self.assertEqual(отказ.подробности["errors"][0]["path"], "$.lessons[0].body")
		self.assertEqual(отказ.подробности["total"], 1)
		релиз = пример_релиза(self.ключ)
		вопрос = релиз["lessons"][0]["quiz"]["questions"][0]["key"]
		релиз["lessons"][0]["quiz"]["answers"][вопрос]["correct"] = "V9"
		отказ = self.отказ(service.РЕЛИЗ_НЕ_СХОДИТСЯ, релиз)
		self.assertEqual(отказ.подробности["problems"][0]["code"], "quiz_correct")
		self.assertIsNone(self.курс_по_ключу())

	def test_курс_передан(self):
		frappe.set_user("Administrator")
		анонс = создать_курс(f"Анонс {frappe.generate_hash(length=6)}")
		со_старыми_уроками = frappe.db.get_value(
			"Course Chapter",
			frappe.db.get_value(
				"Course Lesson", создать_урок(f"Старый {frappe.generate_hash(length=6)}"), "chapter"
			),
			"course",
		)
		с_другим_ключом = создать_курс(f"Другой {frappe.generate_hash(length=6)}")
		frappe.db.set_value(
			"LMS Course", с_другим_ключом, "course_key", f"other-{frappe.generate_hash(length=6)}"
		)
		ещё_анонс = создать_курс(f"Анонс {frappe.generate_hash(length=6)}")
		frappe.set_user(self.куратор)

		ответ = self.опубликовать(course=анонс)
		self.assertEqual((ответ["course"], ответ["course_created"]), (анонс, False))
		self.assertEqual(frappe.db.get_value("LMS Course", анонс, "course_key"), self.ключ)
		self.assertEqual(len(get_lessons(анонс)), 3)

		другой_ключ = f"fresh-{frappe.generate_hash(length=6)}"
		отказ = self.отказ(service.У_КУРСА_ЕСТЬ_УРОКИ, пример_релиза(другой_ключ), со_старыми_уроками)
		self.assertEqual(отказ.подробности["lessons"], 1)
		self.отказ(service.КЛЮЧ_НЕ_ТОТ, пример_релиза(другой_ключ), с_другим_ключом)
		self.отказ(service.КЛЮЧ_ЗАНЯТ, пример_релиза(self.ключ), ещё_анонс)
		self.отказ("course_not_found", пример_релиза(другой_ключ), "нет-такого-курса")
		self.assertIsNone(self.курс_по_ключу(другой_ключ))

	def test_предупреждения_и_запасная_карточка(self):
		релиз = пример_релиза(self.ключ)
		релиз["course"]["summary"] = ""

		ответ = self.опубликовать(релиз)

		self.assertIn({"code": "public_text_empty", "where": "course.summary"}, ответ["warnings"])
		self.assertEqual(
			frappe.db.get_value("LMS Course", ответ["course"], "short_introduction"), "Пример курса"
		)

	def test_релиз_без_документа(self):
		релиз = пример_релиза(self.ключ)
		релиз["document"] = None
		for урок in релиз["lessons"]:
			урок["sections"] = []

		ответ = self.опубликовать(релиз)

		self.assertIsNone(ответ["document"])
		self.assertIsNone(frappe.db.get_value(РЕЛИЗ, ответ["release"], "document_key"))
		self.assertTrue(self.опубликовать(релиз)["unchanged"])

	def test_курс_из_релиза_открывается_публикацией(self):
		"""Директив и квизов Learning у курса из релиза нет: релиз проверен целиком при публикации."""
		первый = self.опубликовать()

		ответ = authoring.publish_course(course=первый["course"])

		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(ответ["data"]["warnings"], [])
		self.assertEqual(frappe.db.get_value("LMS Course", первый["course"], "published"), 1)

	def test_новый_релиз_двигает_ревизию(self):
		первый = self.опубликовать()
		до = authoring.course_revision(course=первый["course"])["data"]["revision"]
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][2]["homework"]["due_days"] = 5

		self.опубликовать(релиз)

		self.assertGreater(authoring.course_revision(course=первый["course"])["data"]["revision"], до)

	def test_отказ_после_записи_релиза_оставляет_всё_как_было(self):
		"""Отказ на последнем шаге: релиз, строки индекса, новая версия схемы,
		карточка, действующий релиз и порядок глав — прежние."""
		первый = self.опубликовать()
		курс = первый["course"]
		строк_индекса = frappe.db.count("Agent Release Goal")
		главы_до = [г["name"] for г in get_chapters(курс)]
		релиз = пример_релиза(self.ключ)
		релиз["course"]["title"] = "Пример курса, второе издание"
		релиз["document"]["sections"][0]["columns"][0]["title"] = "Тема встречи"
		релиз["chapters"].reverse()
		релиз["lessons"] = [релиз["lessons"][2], релиз["lessons"][0], релиз["lessons"][1]]
		with patch(
			"lms_frappe_app.agent_learning.releases.service._карточка", side_effect=Отказ("boom", "сбой")
		):
			self.отказ("boom", релиз)

		self.assertEqual(frappe.db.count(РЕЛИЗ, {"course": курс}), 1)
		self.assertEqual(frappe.db.count("Agent Release Goal"), строк_индекса)
		self.assertEqual(
			frappe.db.get_value(
				"Agent Course Artifact", {"course": курс, "slug": "notebook", "is_active": 1}, "version"
			),
			1,
		)
		self.assertEqual(frappe.db.count("Agent Course Artifact", {"course": курс}), 1)
		self.assertEqual(
			frappe.db.get_value("LMS Course", курс, ["title", "active_release"]),
			("Пример курса", первый["release"]),
		)
		self.assertEqual([г["name"] for г in get_chapters(курс)], главы_до)

	def test_гонка_первых_публикаций_одного_ключа(self):
		"""Вторая из двух одновременных первых публикаций упирается в уникальный
		ключ курса — отказ кодом контракта, а не ошибка сервера."""
		self.опубликовать()
		with patch("lms_frappe_app.agent_learning.releases.service._курс", return_value=None):
			отказ = self.отказ(service.КЛЮЧ_ЗАНЯТ)
		self.assertEqual(отказ.подробности["release_key"], self.ключ)
		self.assertEqual(frappe.db.count("LMS Course", {"course_key": self.ключ}), 1)

	def test_вернувшийся_ключ_в_ответе(self):
		self.опубликовать()
		без_третьего = пример_релиза(self.ключ)
		без_третьего["chapters"] = без_третьего["chapters"][:1]
		без_третьего["lessons"] = без_третьего["lessons"][:2]
		self.опубликовать(без_третьего)

		ответ = self.опубликовать()

		self.assertEqual(ответ["lessons"], {"created": [], "updated": [], "removed": [], "restored": ["l-3"]})
		self.assertEqual(ответ["chapters"]["restored"], ["ch-2"])

	def test_курс_из_релиза_удаляется_целиком(self):
		первый = self.опубликовать()
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, исправленный"
		self.опубликовать(релиз)
		курс = первый["course"]
		уроки = frappe.get_all("Course Lesson", filters={"course": курс}, pluck="name")
		frappe.set_user("Administrator")

		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(РЕЛИЗ, первый["release"])
		service.удалить_курс(курс)

		self.assertFalse(frappe.db.exists("LMS Course", курс))
		self.assertFalse(frappe.db.exists(РЕЛИЗ, {"course": курс}))
		self.assertFalse(frappe.db.exists("Agent Release Lesson", {"lesson": ("in", уроки)}))
		self.assertFalse(frappe.db.exists("Agent Course Artifact", {"course": курс}))
		self.assertFalse(frappe.db.exists("Course Lesson", {"name": ("in", уроки)}))
		# Флаг снят: релиз другого курса по-прежнему не удаляется.
		другой = service.опубликовать(
			пример_релиза(f"other-{frappe.generate_hash(length=6)}"), None, self.куратор
		)
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(РЕЛИЗ, другой["release"])
