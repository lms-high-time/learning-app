# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Проекция релиза в главы и уроки Learning (learning-services#500)."""

import frappe
from frappe.tests import IntegrationTestCase
from lms.lms.utils import get_chapters, get_lessons

from lms_frappe_app.agent_learning import structure
from lms_frappe_app.agent_learning.doctype.agent_course_release.test_agent_course_release import (
	вставить_релиз,
)
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
		self.assertEqual(projection.известные(self.курс), {"chapters": итог.главы, "lessons": итог.уроки})

	def test_ключ_пишется_при_создании_и_не_меняется_при_правке(self):
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		self.assertEqual(frappe.db.get_value("Course Lesson", первый.уроки["l-2"], "lesson_key"), "l-2")
		self.assertEqual(frappe.db.get_value("Course Chapter", первый.главы["ch-2"], "chapter_key"), "ch-2")
		релиз = пример_релиза()
		релиз["lessons"][1].update(chapter="ch-2", title="Урок второй, перенесённый")
		релиз["chapters"][0]["lessons"] = ["l-1"]
		релиз["chapters"][1].update(title="Глава вторая, исправленная", lessons=["l-2", "l-3"])

		итог = projection.спроецировать(self.курс, релиз, projection.известные(self.курс), прежние(первый))

		self.assertEqual(итог.обновлено, {"chapters": ["ch-2"], "lessons": ["l-2"]})
		self.assertEqual(
			frappe.db.get_value("Course Lesson", первый.уроки["l-2"], ["lesson_key", "title"]),
			("l-2", "Урок второй, перенесённый"),
		)
		self.assertEqual(frappe.db.get_value("Course Chapter", первый.главы["ch-2"], "chapter_key"), "ch-2")
		self.assertEqual(projection.известные(self.курс), {"chapters": первый.главы, "lessons": первый.уроки})

	def test_известные_только_записи_курса_с_ключом(self):
		"""Глава и урок курса без ключа — не из релиза: им ключ не сопоставляется."""
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		глава = frappe.get_doc(
			{"doctype": "Course Chapter", "title": "Своя глава", "course": self.курс}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{"doctype": "Course Lesson", "title": "Свой урок", "course": self.курс, "chapter": глава.name}
		).insert(ignore_permissions=True)
		другой = курс_куратора(self.куратор)
		projection.спроецировать(другой, пример_релиза(), ПУСТО, {})

		self.assertEqual(projection.известные(self.курс), {"chapters": первый.главы, "lessons": первый.уроки})

	def test_правка_названия_меняет_ту_же_запись(self):
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		релиз = пример_релиза()
		релиз["lessons"][0]["title"] = "Урок первый, исправленный"

		итог = projection.спроецировать(self.курс, релиз, projection.известные(self.курс), прежние(первый))

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

		итог = projection.спроецировать(self.курс, релиз, projection.известные(self.курс), прежние(первый))

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

		итог = projection.спроецировать(self.курс, релиз, projection.известные(self.курс), прежние(первый))

		self.assertEqual(итог.снято, {"chapters": ["ch-2"], "lessons": ["l-3"]})
		self.assertTrue(frappe.db.exists("Course Lesson", первый.уроки["l-3"]))
		self.assertNotIn(первый.уроки["l-3"], self.уроки())
		self.assertEqual([г["name"] for г in get_chapters(self.курс)], [первый.главы["ch-1"]])
		self.assertFalse(frappe.get_all("Lesson Reference", filters={"parent": первый.главы["ch-2"]}))

	def test_вернувшийся_ключ_получает_ту_же_запись(self):
		"""Ключ снятой записи остаётся на ней: вернувшийся ключ находит её без
		истории релизов — релизов в этом тесте нет вовсе."""
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		релиз = пример_релиза()
		релиз["chapters"] = релиз["chapters"][:1]
		релиз["lessons"] = релиз["lessons"][:2]
		второй = projection.спроецировать(self.курс, релиз, projection.известные(self.курс), прежние(первый))
		self.assertEqual(projection.известные(self.курс)["lessons"]["l-3"], первый.уроки["l-3"])
		self.assertEqual(projection.известные(self.курс)["chapters"]["ch-2"], первый.главы["ch-2"])
		self.assertFalse(frappe.db.exists("Agent Course Release", {"course": self.курс}))

		итог = projection.спроецировать(
			self.курс, пример_релиза(), projection.известные(self.курс), прежние(второй)
		)

		self.assertEqual(итог.уроки["l-3"], первый.уроки["l-3"])
		self.assertEqual(итог.главы["ch-2"], первый.главы["ch-2"])
		self.assertEqual(итог.создано, {"chapters": [], "lessons": []})
		self.assertEqual(итог.возвращено, {"chapters": ["ch-2"], "lessons": ["l-3"]})
		self.assertEqual(self.уроки(), [первый.уроки[к] for к in ("l-1", "l-2", "l-3")])

	def test_без_изменений_ничего_не_сохраняет(self):
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		было = {имя: frappe.db.get_value("Course Lesson", имя, "modified") for имя in первый.уроки.values()}
		курс_был = frappe.db.get_value("LMS Course", self.курс, "modified")

		итог = projection.спроецировать(
			self.курс, пример_релиза(), projection.известные(self.курс), прежние(первый)
		)

		пусто = {"chapters": [], "lessons": []}
		self.assertEqual((итог.создано, итог.обновлено, итог.снято), (пусто, пусто, пусто))
		self.assertEqual(
			{имя: frappe.db.get_value("Course Lesson", имя, "modified") for имя in первый.уроки.values()},
			было,
		)
		self.assertEqual(frappe.db.get_value("LMS Course", self.курс, "modified"), курс_был)

	def test_запись_чужого_курса_не_берётся(self):
		"""Те же ключи в другом курсе — свои записи: ключ ищется среди записей курса."""
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		другой = курс_куратора(self.куратор)

		итог = projection.спроецировать(другой, пример_релиза(), projection.известные(другой), {})

		self.assertNotEqual(итог.уроки["l-1"], первый.уроки["l-1"])
		self.assertEqual(итог.создано["lessons"], ["l-1", "l-2", "l-3"])
		self.assertEqual(frappe.db.get_value("Course Lesson", итог.уроки["l-1"], "lesson_key"), "l-1")
		self.assertEqual(projection.известные(self.курс)["lessons"], первый.уроки)

	def test_снятый_урок_не_возвращается_в_программу(self):
		"""Курс с действующим релизом: состав — только строки-ссылки, без запасного пути."""
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		frappe.db.set_value("LMS Course", self.курс, "active_release", вставить_релиз(self.курс))
		релиз = пример_релиза()
		релиз["chapters"] = релиз["chapters"][:1]
		релиз["lessons"] = релиз["lessons"][:2]

		projection.спроецировать(self.курс, релиз, projection.известные(self.курс), прежние(первый))

		self.assertEqual(structure.уроки_курса(self.курс), [первый.уроки["l-1"], первый.уроки["l-2"]])
		self.assertEqual(structure.уроков_в_курсах([self.курс]), {self.курс: 2})
		self.assertEqual([г["name"] for г in structure.главы_курса(self.курс)], [первый.главы["ch-1"]])
		self.assertEqual(structure.уроки_главы(первый.главы["ch-2"]), [])

	def test_курс_без_релиза_держит_запасной_путь(self):
		"""Урок без строки-ссылки у курса без релиза по-прежнему в программе — в конце главы."""
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		frappe.db.delete("Lesson Reference", {"parent": первый.главы["ch-1"], "lesson": первый.уроки["l-1"]})

		self.assertEqual(
			structure.уроки_главы(первый.главы["ch-1"]), [первый.уроки["l-2"], первый.уроки["l-1"]]
		)
		self.assertEqual(structure.уроков_в_курсах([self.курс]), {self.курс: 3})
