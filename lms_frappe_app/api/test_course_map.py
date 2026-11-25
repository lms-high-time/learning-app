# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import public
from lms_frappe_app.api.authoring import КУРС_НЕ_НАЙДЕН
from lms_frappe_app.tests.sample_data import (
	привязать_урок,
	раздел,
	зачислить,
	создать_ученика,
	создать_урок,
	схема_документа,
)


class IntegrationTestCourseMap(IntegrationTestCase):
	"""Карта курса без релиза — то, что видят гость и зачисленный ученик.

	Цели уроков задаёт только релиз (`test_lesson_readers`): у курса без
	релиза уроки приходят с пустым списком целей.
	"""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.урок = создать_урок(f"Урок карты {суффикс}")
		глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		self.курс = frappe.db.get_value("Course Chapter", глава, "course")
		frappe.db.set_value("LMS Course", self.курс, "published", 1)

	def карта(self) -> dict:
		return public.course_map(course=self.курс)["data"]

	# --- программа курса: зачин, пройденность, следующий урок (learning-services#322) ---

	def test_гость_видит_зачин_но_не_свой_путь(self):
		"""Зачин — без пробелов по краям, пустой или из одних пробелов — `null`; пройденности и
		следующего урока у гостя нет."""
		for зачин, видит in (("  Зачем это вам  ", "Зачем это вам"), ("   ", None), (None, None)):
			with self.subTest(зачин=зачин):
				frappe.set_user("Administrator")
				frappe.db.set_value("Course Lesson", self.урок, "lesson_hook", зачин)
				frappe.set_user("Guest")

				карта = self.карта()
				урок = карта["chapters"][0]["lessons"][0]

				self.assertEqual(урок["hook"], видит)
				self.assertNotIn("completed", урок, "пройденность — только своя, у гостя ключа нет")
				self.assertNotIn("next_lesson", карта)
				self.assertEqual(карта["competencies"], [], "курс без релиза — без компетенций")

	def test_зачисленный_видит_пройденность_и_следующий_урок(self):
		"""У пройденного курса следующего урока нет; новый урок — следующий."""
		ученик = создать_ученика(f"map-{frappe.generate_hash(length=6)}@example.com")
		зачислить(ученик, self.урок)
		frappe.get_doc(
			{"doctype": "LMS Course Progress", "member": ученик, "lesson": self.урок, "status": "Complete"}
		).insert(ignore_permissions=True)
		frappe.set_user(ученик)

		self.assertIsNone(self.карта()["next_lesson"])

		frappe.set_user("Administrator")
		глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		второй = frappe.get_doc(
			{"doctype": "Course Lesson", "title": f"Второй {frappe.generate_hash(length=6)}", "chapter": глава}
		).insert(ignore_permissions=True).name
		привязать_урок(глава, второй)
		frappe.set_user(ученик)

		карта = self.карта()
		уроки = {у["id"]: у for у in карта["chapters"][0]["lessons"]}

		self.assertTrue(уроки[self.урок]["completed"])
		self.assertFalse(уроки[второй]["completed"])
		self.assertEqual(карта["next_lesson"], второй, "тот же урок, что у lesson_entry(course)")

	def test_непубликованный_курс_гостю_отказ(self):
		frappe.db.set_value("LMS Course", self.курс, "published", 0)
		frappe.set_user("Guest")

		ответ = public.course_map(course=self.курс)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], КУРС_НЕ_НАЙДЕН)

	def test_порядок_уроков_совпадает_с_программой_learning(self):
		второй = создать_урок(f"Второй урок {frappe.generate_hash(length=4)}")
		глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		привязать_урок(глава, второй)
		frappe.set_user("Guest")

		from lms.lms.utils import get_course_outline

		наш = [
			урок["id"]
			for гл in self.карта()["chapters"]
			for урок in гл["lessons"]
		]
		их = [
			урок["name"]
			for гл in get_course_outline(self.курс)
			for урок in гл.get("lessons", [])
		]

		self.assertEqual(наш, их)


class IntegrationTestCourseMapDocuments(IntegrationTestCase):
	"""Документ курса на карте: что соберёт курс и на каком уроке (#340)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.урок = создать_урок(f"Урок документа {суффикс}")
		глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		self.курс = frappe.db.get_value("Course Chapter", глава, "course")
		frappe.db.set_value("LMS Course", self.курс, "published", 1)
		схема_документа(
			self.курс,
			"summary",
			"Резюме проекта",
			[раздел("goal", "Цель", lesson=self.урок, description="Одной фразой"), раздел("sponsor", "Спонсор")],
		)

	def карта(self) -> dict:
		return public.course_map(course=self.курс)["data"]

	def test_гость_видит_документ_и_блоки_урока_без_заполненности(self):
		"""Без документа у курса — пустые списки документов и блоков урока."""
		frappe.set_user("Guest")
		карта = self.карта()

		self.assertEqual(
			карта["documents"],
			[
				{
					"artifact": "summary",
					"title": "Резюме проекта",
					# Все разделы по порядку схемы, и не только собранные на уроке (#856).
					"blocks": [{"key": "goal", "title": "Цель"}, {"key": "sponsor", "title": "Спонсор"}],
				}
			],
		)
		self.assertEqual(
			карта["chapters"][0]["lessons"][0]["blocks"],
			[{"artifact": "summary", "key": "goal", "title": "Цель", "description": "Одной фразой"}],
		)

		frappe.db.delete("Agent Course Artifact", {"course": self.курс})
		карта = self.карта()

		self.assertEqual(карта["documents"], [])
		self.assertEqual(карта["chapters"][0]["lessons"][0]["blocks"], [])

	def test_ученик_видит_заполненность(self):
		ученик = создать_ученика(f"map-doc-{frappe.generate_hash(length=6)}@example.com")
		зачислить(ученик, self.урок)
		frappe.set_user(ученик)
		from lms_frappe_app.api import student

		student.update_artifact(self.курс, "summary", "goal", fields={"goal": "Открыть седьмую кофейню"})
		карта = self.карта()

		документ = карта["documents"][0]
		self.assertEqual((документ["blocks_filled"], документ["blocks_total"]), (1, 2))
		self.assertEqual(
			документ["blocks"],
			[
				{"key": "goal", "title": "Цель", "filled": True},
				{"key": "sponsor", "title": "Спонсор", "filled": False},
			],
		)
		self.assertTrue(карта["chapters"][0]["lessons"][0]["blocks"][0]["filled"])


class IntegrationTestCourseMapCompetencies(IntegrationTestCase):
	"""Компетенции курса на каталоге — вкладка «Умения» на странице курса (learning-services#843,
	#854): у каждой — цель курса, зачисленному — ещё его уровень (#857).

	Группировка и порядок — юнитом `views.для_витрины`, уровни — `views.уровни_по_данным`; здесь
	проводка: карта курса по действующему релизу, гостю и зачисленному.
	"""

	def курс_с_картой(self) -> tuple[str, dict, str]:
		"""Курс на каталоге: (курс, ключи примера, отдельная базовая). Цели уроков: интегративная
		(3), отдельная базовая (2), вторая часть интегративной (2); первая часть — только
		составляющей по каталогу, своей цели у неё нет."""
		from lms_frappe_app.agent_learning.catalog import service as каталог
		from lms_frappe_app.tests.catalog_sample import (
			ключи_примера,
			компетенция,
			поверх_опубликованного,
			пример_каталога,
		)
		from lms_frappe_app.tests.release_sample import пример_релиза
		from lms_frappe_app.tests.sample_data import курс_из_релиза

		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		к = ключи_примера(суффикс)
		отдельная = f"solo-{суффикс}"
		образец = пример_каталога(суффикс)
		образец["competencies"].append(компетенция(отдельная, к["classes"]))
		релиз = пример_релиза(f"map-cat-{суффикс}")
		релиз["catalog"] = каталог.опубликовать(поверх_опубликованного(образец), "Administrator")["version"]
		релиз["lessons"][0]["competencies"] = [{"competency": к["whole"], "target": 3}]
		релиз["lessons"][1]["competencies"] = [{"competency": отдельная, "target": 2}]
		релиз["lessons"][2]["competencies"] = [{"competency": к["b"], "target": 2}]
		курс, _ = курс_из_релиза(релиз=релиз)
		frappe.db.set_value("LMS Course", курс, "published", 1)
		return курс, к, отдельная

	def test_гость_видит_интегративную_с_частями_и_отдельную_базовую(self):
		курс, к, отдельная = self.курс_с_картой()
		frappe.set_user("Guest")

		компетенции = public.course_map(course=курс)["data"]["competencies"]

		def кратко(ключ: str, цель: int | None) -> dict:
			return {
				"key": ключ,
				"kind": "base",
				"title": f"Умение {ключ}",
				"description": f"Что человек делает, владея умением {ключ}.",
				"target": цель,
			}

		# Гостю — цель курса, но не уровни: ключей `status` и `level` нет вовсе.
		self.assertEqual(
			компетенции,
			[
				{
					**кратко(к["whole"], 3),
					"kind": "integrative",
					"parts": [кратко(к["b"], 2), кратко(к["a"], None)],
				},
				кратко(отдельная, 2),
			],
		)

	def test_зачисленный_видит_свой_уровень_у_компетенций_с_целью(self):
		from lms_frappe_app.tests.sample_data import зачислить_на_курс

		курс, к, отдельная = self.курс_с_картой()
		ученик = создать_ученика(f"map-lvl-{frappe.generate_hash(length=6)}@example.com")
		зачислить_на_курс(ученик, курс)
		frappe.set_user(ученик)

		компетенции = public.course_map(course=курс)["data"]["competencies"]
		целое, отдельно = компетенции
		части = {ч["key"]: ч for ч in целое["parts"]}

		# Ни один урок не начат: курс ни одну компетенцию ещё не открыл.
		for компетенция in (целое, отдельно, части[к["b"]]):
			self.assertEqual(компетенция["status"], "locked", компетенция["key"])
			self.assertIsNone(компетенция["level"], компетенция["key"])
		# Составляющую без своей цели курс не ведёт — уровня у неё нет.
		self.assertIsNone(части[к["a"]]["target"])
		self.assertNotIn("status", части[к["a"]])
		self.assertNotIn("level", части[к["a"]])
