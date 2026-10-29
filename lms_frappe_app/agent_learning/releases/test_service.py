# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Публикация релиза: версия, атомарность, курс (learning-services#500)."""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from lms.lms.utils import get_chapters, get_lessons

from lms_frappe_app.agent_learning.errors import КУРС_ИЗ_РЕЛИЗА, Отказ
from lms_frappe_app.agent_learning.releases import index, places, schema, service
from lms_frappe_app.api import authoring
from lms_frappe_app.tests.release_sample import перевести, пример_релиза
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

	def test_отказы_схемы_и_проверок(self):
		"""Разбор и проверки — до первой записи: по строке проводки на каждую
		ступень. Сами ступени целиком проверяет `TestРазборИПроверкаРелиза`."""
		self.отказ(service.РЕЛИЗ_НЕВЕРЕН, "{не json")
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

	def test_переводы_карточки_приходят_и_уходят_с_релизом(self):
		"""Перевод из релиза — в `course_translations`; тот же релиз ничего не
		пишет, релиз без переводов снимает их и с курса (learning-services#738)."""
		ответ = self.опубликовать(перевести(пример_релиза(self.ключ)))
		курс = ответ["course"]
		записано = json.loads(frappe.db.get_value("LMS Course", курс, "course_translations"))
		self.assertEqual(записано["en"]["title"], "Sample course")
		отметка = frappe.db.get_value("LMS Course", курс, "modified")

		self.assertTrue(self.опубликовать(перевести(пример_релиза(self.ключ)))["unchanged"])
		self.assertEqual(frappe.db.get_value("LMS Course", курс, "modified"), отметка)

		self.опубликовать()
		self.assertEqual(json.loads(frappe.db.get_value("LMS Course", курс, "course_translations")), {})

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

	def test_открытие_отдаёт_предупреждения_действующего_релиза(self):
		релиз = пример_релиза(self.ключ)
		релиз["chapters"][1]["description"] = ""
		первый = self.опубликовать(релиз)
		self.assertTrue(первый["warnings"])

		ответ = authoring.publish_course(course=первый["course"])

		self.assertEqual(ответ["data"]["warnings"], первый["warnings"])
		self.assertEqual(frappe.db.get_value("LMS Course", первый["course"], "published"), 1)

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

		self.assertEqual(
			(ответ["lessons"], ответ["chapters"]["restored"]),
			({"created": [], "updated": [], "removed": [], "restored": ["l-3"]}, ["ch-2"]),
		)
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
		self.assertFalse(frappe.db.exists("Agent Lesson Homework", {"lesson": ("in", уроки)}))
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
		with self.assertRaises(Отказ) as пойман:
			frappe.delete_doc("Course Lesson", урок_релиза(другой["course"], "l-1"))
		self.assertEqual(пойман.exception.код, КУРС_ИЗ_РЕЛИЗА)


def _релиз(**правки) -> dict:
	"""`пример_релиза` с правкой по пути: `{"lessons.0.body": "x"}`."""
	релиз = пример_релиза("unit")
	for путь, значение in правки.items():
		*до, последний = путь.split(".")
		узел = релиз
		for шаг in до:
			узел = узел[int(шаг)] if isinstance(узел, list) else узел[шаг]
		узел[int(последний) if isinstance(узел, list) else последний] = значение
	return релиз


class TestРазборИПроверкаРелиза(UnitTestCase):
	"""Без базы: первые ступени публикации — `_разобрать` и `_проверить` (learning-services#536)."""

	def отказ(self, функция, релиз) -> Отказ:
		with self.assertRaises(Отказ) as пойман:
			функция(релиз)
		return пойман.exception

	def test_разбор_принимает_объект_и_строку(self):
		релиз = пример_релиза("unit")
		with self.subTest("объект — он же"):
			self.assertIs(service._разобрать(релиз), релиз)
		with self.subTest("строка JSON — объект"):
			self.assertEqual(service._разобрать(json.dumps(релиз, ensure_ascii=False)), релиз)

	def test_отказы_разбора(self):
		"""Отказ `release_invalid`: `errors` — не больше `ОШИБОК_НЕ_БОЛЬШЕ`, `total` — все."""
		с_нечислами = пример_релиза("unit")
		с_нечислами["agent"] = {"порог": float("inf"), "список": [1, float("nan")]}
		с_бесконечностью_строкой = пример_релиза("unit")
		с_бесконечностью_строкой["map"] = {"вес": float("-inf")}
		много = пример_релиза("unit")
		много["agent"] = {"много": [float("nan")] * 25}
		случаи = (
			("не JSON", "{не json", ("$",), 1),
			("JSON-массив строкой", "[]", ("$",), 1),
			("массив", [], ("$",), 1),
			("None", None, ("$",), 1),
			("NaN в строке JSON", '{"format": "lms-release/1", "x": NaN}', ("$",), 1),
			("-Infinity в строке JSON", json.dumps(с_бесконечностью_строкой), ("$",), 1),
			("нечисла в agent", с_нечислами, ("$.agent.порог", "$.agent.список[1]"), 2),
			(
				"нечисел больше предела",
				много,
				tuple(f"$.agent.много[{номер}]" for номер in range(schema.ОШИБОК_НЕ_БОЛЬШЕ)),
				25,
			),
		)
		for что, релиз, пути, всего in случаи:
			with self.subTest(что):
				отказ = self.отказ(service._разобрать, релиз)
				self.assertEqual(
					(отказ.код, tuple(о["path"] for о in отказ.подробности["errors"]), отказ.подробности["total"]),
					(service.РЕЛИЗ_НЕВЕРЕН, пути, всего),
				)
		with self.subTest("строка JSON называет константу"):
			отказ = self.отказ(service._разобрать, '{"format": "lms-release/1", "x": NaN}')
			self.assertIn("NaN", отказ.подробности["errors"][0]["message"])

	def test_проверка_отдаёт_предупреждения(self):
		случаи = (
			("годный релиз", _релиз(), []),
			(
				"пустое описание курса",
				_релиз(**{"course.summary": ""}),
				[{"code": "public_text_empty", "where": "course.summary"}],
			),
		)
		for что, релиз, ожидание in случаи:
			with self.subTest(что):
				предупреждения = service._проверить(релиз)
				for предупреждение in ожидание:
					self.assertIn(предупреждение, предупреждения)
				if not ожидание:
					self.assertEqual(предупреждения, [])

	def test_отказы_проверки(self):
		"""Ступени по очереди: формат, публичная схема, правила сервера."""
		лишние = _релиз()
		for номер in range(25):
			лишние[f"x{номер}"] = 1
		вопрос = пример_релиза("unit")["lessons"][0]["quiz"]["questions"][0]["key"]
		неверный_ответ = _релиз(**{f"lessons.0.quiz.answers.{вопрос}.correct": "V9"})
		схема_и_правила = _релиз(
			**{f"lessons.0.quiz.answers.{вопрос}.correct": "V9", "lessons.0.body": "материал"}
		)
		случаи = (
			(
				"другой формат",
				_релиз(format="lms-release/2"),
				(service.ФОРМАТ_НЕ_ТОТ, {"format": "lms-release/2", "supported": [schema.ФОРМАТ]}),
			),
			("без формата", {}, (service.ФОРМАТ_НЕ_ТОТ, {"format": None, "supported": [schema.ФОРМАТ]})),
			(
				"формат раньше схемы",
				{"format": "lms-release/2", "lessons": "не список"},
				(service.ФОРМАТ_НЕ_ТОТ, {"format": "lms-release/2", "supported": [schema.ФОРМАТ]}),
			),
			(
				"поле вне схемы",
				_релиз(**{"lessons.0.body": "материал"}),
				(service.РЕЛИЗ_НЕВЕРЕН, {"paths": ["$.lessons[0].body"], "total": 1}),
			),
			(
				"схема раньше правил сервера",
				схема_и_правила,
				(service.РЕЛИЗ_НЕВЕРЕН, {"paths": ["$.lessons[0].body"], "total": 1}),
			),
			(
				"ошибок схемы больше предела",
				лишние,
				(service.РЕЛИЗ_НЕВЕРЕН, {"paths": schema.ОШИБОК_НЕ_БОЛЬШЕ, "total": 25}),
			),
			(
				"части не сходятся",
				неверный_ответ,
				(service.РЕЛИЗ_НЕ_СХОДИТСЯ, {"codes": ["quiz_correct"], "total": 1}),
			),
		)
		for что, релиз, ожидание in случаи:
			with self.subTest(что):
				отказ = self.отказ(service._проверить, релиз)
				подробности = отказ.подробности
				if отказ.код == service.РЕЛИЗ_НЕВЕРЕН:
					пути = [о["path"] for о in подробности["errors"]]
					подробности = {
						"paths": пути if isinstance(ожидание[1]["paths"], list) else len(пути),
						"total": подробности["total"],
					}
				elif отказ.код == service.РЕЛИЗ_НЕ_СХОДИТСЯ:
					подробности = {
						"codes": [п["code"] for п in подробности["problems"]],
						"total": подробности["total"],
					}
				self.assertEqual((отказ.код, подробности), ожидание)


class TestПереводыКарточки(UnitTestCase):
	def test_переводы_карточки_из_релиза(self):
		"""Пустые тексты перевода — как у русской карточки: краткое — название,
		полное — краткое; цель главы без перевода — русское название
		(learning-services#738)."""
		for случай, правка, ожидается in (
			("без переводов", lambda р: None, {}),
			# Русский текст — основные поля: перевода на русский не бывает.
			(
				"русский пропускается",
				lambda р: р["course"].update(translations={"ru": {"title": "Пример"}}),
				{},
			),
			(
				"полный",
				перевести,
				{
					"en": {
						"title": "Sample course",
						"short_introduction": "Short card",
						"description": "Course page text.",
						"objectives": ["Chapter 1", "Chapter 2"],
					}
				},
			),
			(
				"только название, одна глава",
				lambda р: перевести(р, главы=1)["course"].update(
					translations={"en": {"title": "T", "summary": ""}}
				),
				{
					"en": {
						"title": "T",
						"short_introduction": "T",
						"description": "T",
						"objectives": ["Chapter 1", "Глава вторая"],
					}
				},
			),
		):
			with self.subTest(случай):
				р = пример_релиза()
				правка(р)
				self.assertEqual(service.переводы_карточки(р), ожидается)
