# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Публикация релиза: версия, атомарность, курс (learning-services#500)."""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from lms.lms.utils import get_chapters, get_lessons

from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases import index, places, service
from lms_frappe_app.api import authoring
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	создать_занятие,
	создать_куратора,
	создать_курс,
	создать_урок,
	создать_ученика,
	урок_релиза,
)

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

	def занятие(self, курс: str, ключ: str) -> None:
		"""Занятие ученика по уроку `ключ`: со ссылкой на него снятый урок остаётся."""
		frappe.set_user("Administrator")
		ученик = создать_ученика(f"rel-svc-pupil-{frappe.generate_hash(length=6)}@example.com")
		создать_занятие(ученик, урок_релиза(курс, ключ))
		frappe.set_user(self.куратор)

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
			json.loads(frappe.db.get_value(РЕЛИЗ, ответ["release"], "snapshot"))["lessons"][0]["title"],
			"Урок первый, исправленный",
		)
		self.assertIsNone(frappe.db.get_value(РЕЛИЗ, первый["release"], "snapshot"))

	def test_снятый_урок_в_ответе(self):
		первый = self.опубликовать()
		релиз = пример_релиза(self.ключ)
		релиз["chapters"] = релиз["chapters"][:1]
		релиз["lessons"] = релиз["lessons"][:2]
		del релиз["agent"]["lessons"]["l-3"]

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
		отказ = self.отказ(service.РЕЛИЗ_НЕВЕРЕН, '{"format": "lms-release/1", "x": NaN}')
		self.assertIn("NaN", отказ.подробности["errors"][0]["message"])
		релиз = пример_релиза(self.ключ)
		релиз["agent"] = {"порог": float("inf"), "список": [1, float("nan")]}
		отказ = self.отказ(service.РЕЛИЗ_НЕВЕРЕН, релиз)
		self.assertEqual(
			[о["path"] for о in отказ.подробности["errors"]], ["$.agent.порог", "$.agent.список[1]"]
		)
		релиз = пример_релиза(self.ключ)
		релиз["map"] = {"вес": float("-inf")}
		self.отказ(service.РЕЛИЗ_НЕВЕРЕН, json.dumps(релиз))
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

	def test_пакет_агента_раскладывается_при_публикации(self):
		релиз = пример_релиза(self.ключ)
		del релиз["agent"]["lessons"]["l-3"]

		имя = self.опубликовать(релиз)["release"]

		пакет = релиз["agent"]
		self.assertEqual(index.пакет_урока(имя, "l-1"), пакет["lessons"]["l-1"])
		self.assertEqual(index.пакет_урока(имя, "l-3"), {})
		self.assertEqual(index.рамка(имя)["learn_about_student"], пакет["learn_about_student"])

	def test_ответы_квиза_в_пакете_агента_отказ(self):
		for ключ in ("answers", "correct"):
			релиз = пример_релиза(self.ключ)
			релиз["agent"]["lessons"]["l-2"]["items"]["l-2-D1/V1"] = {ключ: "V1"}
			with self.subTest(ключ=ключ):
				отказ = self.отказ(service.РЕЛИЗ_НЕ_СХОДИТСЯ, релиз)
				self.assertEqual(
					отказ.подробности["problems"],
					[{"code": "agent_leak", "where": f"agent.lessons.l-2.items.l-2-D1/V1.{ключ}"}],
				)
		self.assertIsNone(self.курс_по_ключу())

	def test_лишние_ключи_пакета_агента_отказ_до_первой_записи(self):
		"""Хвост снятого урока, пункт не из целей урока, запись раздела в срезе
		не из документа — отказ; курс и его версии как были. Запись раздела
		документа, который урок не заполняет, публикации не мешает."""
		первый = self.опубликовать()
		курс = первый["course"]

		def хвост(р):
			р["chapters"] = р["chapters"][:1]
			р["lessons"] = р["lessons"][:2]

		порчи = {
			"agent.lessons": (хвост, "l-3"),
			"agent.lessons.l-1.items": (
				lambda р: р["agent"]["lessons"]["l-1"]["items"].update({"l-1-D1/V9": "Подробности."}),
				"l-1-D1/V9",
			),
			"agent.lessons.l-1.sections": (
				lambda р: р["agent"]["lessons"]["l-1"]["sections"].update({"nope": "Запись раздела."}),
				"nope",
			),
		}
		было = {
			doctype: frappe.get_all(doctype, filters={"course": курс}, fields=["name", "modified"])
			for doctype in ("Course Lesson", "Course Chapter")
		}
		for где, (порча, ключ) in порчи.items():
			релиз = пример_релиза(self.ключ)
			релиз["lessons"][0]["title"] = "Урок первый, исправленный"
			порча(релиз)
			with self.subTest(где=где):
				отказ = self.отказ(service.РЕЛИЗ_НЕ_СХОДИТСЯ, релиз)
				self.assertEqual(
					отказ.подробности["problems"], [{"code": "broken_ref", "where": где, "key": ключ}]
				)
		self.assertEqual(frappe.db.count(РЕЛИЗ, {"course": курс}), 1)
		self.assertEqual(frappe.db.get_value("LMS Course", курс, "active_release"), первый["release"])
		self.assertEqual(
			{
				doctype: frappe.get_all(doctype, filters={"course": курс}, fields=["name", "modified"])
				for doctype in было
			},
			было,
		)
		релиз = пример_релиза(self.ключ)
		релиз["agent"]["lessons"]["l-1"]["sections"]["rules"] = "Запись раздела в срезе."
		self.assertEqual(self.опубликовать(релиз)["version"], 2)

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
		for срез in релиз["agent"]["lessons"].values():
			срез["sections"] = {}

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

	def test_открытие_отдаёт_предупреждения_действующего_релиза(self):
		релиз = пример_релиза(self.ключ)
		релиз["chapters"][1]["description"] = ""
		первый = self.опубликовать(релиз)
		self.assertTrue(первый["warnings"])

		ответ = authoring.publish_course(course=первый["course"])

		self.assertEqual(ответ["data"]["warnings"], первый["warnings"])

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
		self.занятие(self.опубликовать()["course"], "l-3")
		без_третьего = пример_релиза(self.ключ)
		без_третьего["chapters"] = без_третьего["chapters"][:1]
		без_третьего["lessons"] = без_третьего["lessons"][:2]
		del без_третьего["agent"]["lessons"]["l-3"]
		self.опубликовать(без_третьего)

		ответ = self.опубликовать()

		self.assertEqual(ответ["lessons"], {"created": [], "updated": [], "removed": [], "restored": ["l-3"]})
		self.assertEqual(ответ["chapters"]["restored"], ["ch-2"])

	def test_вернувшийся_ключ_та_же_запись(self):
		первый = self.опубликовать()
		урок = index.урок(первый["release"], "l-3")["lesson"]
		self.занятие(первый["course"], "l-3")
		без_третьего = пример_релиза(self.ключ)
		без_третьего["chapters"] = без_третьего["chapters"][:1]
		без_третьего["lessons"] = без_третьего["lessons"][:2]
		del без_третьего["agent"]["lessons"]["l-3"]
		self.опубликовать(без_третьего)

		ответ = self.опубликовать()

		self.assertEqual(index.урок(ответ["release"], "l-3")["lesson"], урок)
		self.assertEqual(frappe.db.get_value("Course Lesson", урок, "lesson_key"), "l-3")
		self.assertEqual(frappe.db.count("Course Lesson", {"course": первый["course"]}), 3)

	def test_записи_без_ключа_отказ_до_первой_записи(self):
		"""Сайт без ключей на записях (патч `release_record_keys` не выполнен):
		публикация новой версии завела бы записи заново — отказ."""
		первый = self.опубликовать()
		курс = первый["course"]
		урок = index.урок(первый["release"], "l-2")["lesson"]
		frappe.db.set_value("Course Lesson", урок, "lesson_key", None)
		frappe.db.set_value("Course Chapter", {"course": курс}, "chapter_key", None)
		было = {
			doctype: frappe.get_all(doctype, filters={"course": курс}, fields=["name", "modified"])
			for doctype in ("Course Lesson", "Course Chapter")
		}
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, исправленный"

		отказ = self.отказ(service.КЛЮЧЕЙ_НЕТ, релиз)

		self.assertEqual(отказ.подробности, {"course": курс, "chapters": 2, "lessons": 1})
		self.assertEqual(frappe.db.count(РЕЛИЗ, {"course": курс}), 1)
		self.assertEqual(
			{
				doctype: frappe.get_all(doctype, filters={"course": курс}, fields=["name", "modified"])
				for doctype in было
			},
			было,
		)
		# Тот же релиз — `unchanged`: записей он не заводит, и ключи ему не нужны.
		self.assertTrue(self.опубликовать()["unchanged"])

	def test_запись_вне_релиза_без_ключа_не_мешает(self):
		"""Снятый урок без ключа — не запись действующего релиза: публикация идёт,
		а вернись его ключ — получил бы новую запись."""
		первый = self.опубликовать()
		self.занятие(первый["course"], "l-3")
		без_третьего = пример_релиза(self.ключ)
		без_третьего["chapters"] = без_третьего["chapters"][:1]
		без_третьего["lessons"] = без_третьего["lessons"][:2]
		del без_третьего["agent"]["lessons"]["l-3"]
		self.опубликовать(без_третьего)
		frappe.db.set_value(
			"Course Lesson", {"course": первый["course"], "lesson_key": "l-3"}, "lesson_key", None
		)
		без_третьего["lessons"][0]["title"] = "Урок первый, исправленный"

		ответ = self.опубликовать(без_третьего)

		self.assertEqual((ответ["version"], ответ["lessons"]["updated"]), (3, ["l-1"]))

	def test_курс_из_релиза_удаляется_целиком(self):
		"""С заметками к обеим версиям: заметка ссылается на свой релиз, и
		удаление релизов не встаёт на ней."""
		первый = self.опубликовать()
		курс = первый["course"]
		прежняя = authoring.add_note(course=курс, target="course", text="К первой версии")["data"]["id"]
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, исправленный"
		действующий = self.опубликовать(релиз)["release"]
		уроки = frappe.get_all("Course Lesson", filters={"course": курс}, pluck="name")
		заметка = authoring.add_note(course=курс, target="lesson.l-1", text="Пример")["data"]["id"]
		authoring.reply_note(note=заметка, text="Ответ в нить")
		frappe.set_user("Administrator")

		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(РЕЛИЗ, первый["release"])
		дайджест = frappe.db.get_value(РЕЛИЗ, действующий, "digest")
		places.узлы_карты(действующий, дайджест)
		кэш = places.ключ_кэша(действующий, дайджест)
		self.assertIsNotNone(frappe.cache.get_value(кэш))
		service.удалить_курс(курс)

		self.assertFalse(frappe.db.exists("LMS Course", курс))
		self.assertFalse(frappe.db.exists(РЕЛИЗ, {"course": курс}))
		self.assertFalse(frappe.db.exists("Agent Release Lesson", {"lesson": ("in", уроки)}))
		self.assertFalse(frappe.db.exists("Agent Course Artifact", {"course": курс}))
		self.assertFalse(frappe.db.exists("Agent Author Note", {"name": ("in", [прежняя, заметка])}))
		self.assertFalse(frappe.db.exists("Agent Note Reply", {"parent": заметка}))
		self.assertFalse(frappe.db.exists("Course Lesson", {"name": ("in", уроки)}))
		self.assertIsNone(frappe.cache.get_value(кэш))
		# Флаг снят: релиз другого курса по-прежнему не удаляется.
		другой = service.опубликовать(
			пример_релиза(f"other-{frappe.generate_hash(length=6)}"), None, self.куратор
		)
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(РЕЛИЗ, другой["release"])
