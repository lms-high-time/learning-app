# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Проекция релиза в главы и уроки Learning (learning-services#500)."""

import frappe
from frappe.tests import IntegrationTestCase
from lms.lms.utils import get_chapters, get_lessons

from lms_frappe_app.agent_learning.releases import projection
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import создать_куратора

ПУСТО = {"chapters": {}, "lessons": {}}


def курс_куратора(куратор: str) -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "LMS Course",
				"title": f"Релизный курс {frappe.generate_hash(length=6)}",
				"short_introduction": "Курс для тестов",
				"description": "Курс для тестов",
				"published": 0,
				"instructors": [{"instructor": куратор}],
			}
		)
		.insert()
		.name
	)


def известные(итог) -> dict:
	return {"chapters": dict(итог.главы), "lessons": dict(итог.уроки)}


def прежние(итог) -> dict:
	return {"chapters": list(итог.главы), "lessons": list(итог.уроки)}


class IntegrationTestПроекция(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.куратор = создать_куратора(f"rel-proj-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(self.куратор)
		self.курс = курс_куратора(self.куратор)

	def уроки(self) -> list[str]:
		return [урок["name"] for урок in get_lessons(self.курс)]

	def test_первая_проекция_создаёт_главы_и_уроки_в_порядке(self):
		итог = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})

		главы = get_chapters(self.курс)
		self.assertEqual([г["name"] for г in главы], [итог.главы["ch-1"], итог.главы["ch-2"]])
		self.assertEqual(self.уроки(), [итог.уроки[к] for к in ("l-1", "l-2", "l-3")])
		урок = frappe.get_doc("Course Lesson", итог.уроки["l-1"])
		self.assertEqual(урок.lesson_hook, "Зачин урока «Урок первый»")
		self.assertFalse(урок.body)
		self.assertEqual(урок.course, self.курс)
		self.assertEqual(
			frappe.db.get_value("Course Chapter", итог.главы["ch-1"], "chapter_description"),
			"Что изменится после первой главы.",
		)
		self.assertEqual(итог.создано, {"chapters": ["ch-1", "ch-2"], "lessons": ["l-1", "l-2", "l-3"]})

	def test_правка_названия_меняет_ту_же_запись(self):
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		релиз = пример_релиза()
		релиз["lessons"][0]["title"] = "Урок первый, исправленный"

		итог = projection.спроецировать(self.курс, релиз, известные(первый), прежние(первый))

		self.assertEqual(итог.уроки["l-1"], первый.уроки["l-1"])
		self.assertEqual(
			frappe.db.get_value("Course Lesson", итог.уроки["l-1"], "title"), "Урок первый, исправленный"
		)
		self.assertEqual(итог.обновлено["lessons"], ["l-1"])
		self.assertEqual(итог.создано, {"chapters": [], "lessons": []})

	def test_перенос_урока_в_другую_главу(self):
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		релиз = пример_релиза()
		релиз["lessons"][1]["chapter"] = "ch-2"
		релиз["chapters"][0]["lessons"] = ["l-1"]
		релиз["chapters"][1]["lessons"] = ["l-2", "l-3"]

		итог = projection.спроецировать(self.курс, релиз, известные(первый), прежние(первый))

		self.assertEqual(итог.уроки["l-2"], первый.уроки["l-2"])
		self.assertEqual(
			frappe.db.get_value("Course Lesson", итог.уроки["l-2"], "chapter"), итог.главы["ch-2"]
		)
		self.assertEqual(self.уроки(), [итог.уроки[к] for к in ("l-1", "l-2", "l-3")])
		self.assertEqual(
			[урок["name"] for урок in get_lessons(self.курс, frappe._dict(name=итог.главы["ch-2"]))],
			[итог.уроки["l-2"], итог.уроки["l-3"]],
		)

	def test_снятый_урок_уходит_из_порядка_и_остаётся(self):
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		релиз = пример_релиза()
		релиз["chapters"] = релиз["chapters"][:1]
		релиз["lessons"] = релиз["lessons"][:2]

		итог = projection.спроецировать(self.курс, релиз, известные(первый), прежние(первый))

		self.assertEqual(итог.снято, {"chapters": ["ch-2"], "lessons": ["l-3"]})
		self.assertTrue(frappe.db.exists("Course Lesson", первый.уроки["l-3"]))
		self.assertNotIn(первый.уроки["l-3"], self.уроки())
		self.assertEqual([г["name"] for г in get_chapters(self.курс)], [первый.главы["ch-1"]])
		self.assertFalse(frappe.get_all("Lesson Reference", filters={"parent": первый.главы["ch-2"]}))

	def test_вернувшийся_ключ_получает_ту_же_запись(self):
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		релиз = пример_релиза()
		релиз["chapters"] = релиз["chapters"][:1]
		релиз["lessons"] = релиз["lessons"][:2]
		второй = projection.спроецировать(self.курс, релиз, известные(первый), прежние(первый))
		вся_история = {
			"chapters": {**первый.главы, **второй.главы},
			"lessons": {**первый.уроки, **второй.уроки},
		}

		итог = projection.спроецировать(self.курс, пример_релиза(), вся_история, прежние(второй))

		self.assertEqual(итог.уроки["l-3"], первый.уроки["l-3"])
		self.assertEqual(итог.главы["ch-2"], первый.главы["ch-2"])
		self.assertEqual(итог.создано, {"chapters": [], "lessons": []})
		self.assertEqual(self.уроки(), [первый.уроки[к] for к in ("l-1", "l-2", "l-3")])

	def test_без_изменений_ничего_не_сохраняет(self):
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		было = {имя: frappe.db.get_value("Course Lesson", имя, "modified") for имя in первый.уроки.values()}
		курс_был = frappe.db.get_value("LMS Course", self.курс, "modified")

		итог = projection.спроецировать(self.курс, пример_релиза(), известные(первый), прежние(первый))

		пусто = {"chapters": [], "lessons": []}
		self.assertEqual((итог.создано, итог.обновлено, итог.снято), (пусто, пусто, пусто))
		self.assertEqual(
			{имя: frappe.db.get_value("Course Lesson", имя, "modified") for имя in первый.уроки.values()},
			было,
		)
		self.assertEqual(frappe.db.get_value("LMS Course", self.курс, "modified"), курс_был)

	def test_запись_чужого_курса_не_берётся(self):
		"""Карта «ключ → запись» по истории курса; запись другого курса — не наша."""
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		другой = курс_куратора(self.куратор)

		итог = projection.спроецировать(другой, пример_релиза(), известные(первый), {})

		self.assertNotEqual(итог.уроки["l-1"], первый.уроки["l-1"])
		self.assertEqual(итог.создано["lessons"], ["l-1", "l-2", "l-3"])
