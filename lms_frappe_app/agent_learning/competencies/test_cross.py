# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Сквозные представления компетенций каталога: карта ученика, старт урока, вид руководителя,
подсказка курса (learning-services#827).

Чистые правила — юнитами; через базу — курсы на одном выпуске каталога: у первого урока A и B
цель по базовой `a` (уровень 2), у третьего урока B — по интегративной `whole` из `a` и `b`, у
первого урока C — по `b` (уровень 3). «Похожа» в каталоге записана в одну сторону: `a → b`.
"""

import json
from datetime import datetime

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.agent_learning.catalog import service as каталог
from lms_frappe_app.agent_learning.competencies import cross, evidence, views
from lms_frappe_app.tests.catalog_sample import ключи_примера, поверх_опубликованного, пример_каталога
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	занятие_релиза,
	зачислить_на_курс,
	курс_из_релиза,
	отметить_все_пункты,
	политика_по_умолчанию,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
	урок_релиза,
)


class _Запись(dict):
	__getattr__ = dict.get


def запись(имя, компетенция, исход, уровень=0, занятие="s1", вид="Evidence", выведено=None, **ещё):
	return _Запись(
		name=имя,
		kind=вид,
		competency=компетенция,
		outcome=исход,
		level=уровень,
		session=занятие,
		withdrawn=json.dumps(выведено) if выведено is not None else None,
		**ещё,
	)


class TestПодтверждения(UnitTestCase):
	def test_лучшее_наивысшее_при_равных_последнее_без_выведенных(self):
		записи = [
			запись("e1", "a", "confirmed", 2),
			запись("e2", "a", "confirmed", 3),
			запись("e3", "a", "confirmed", 3),
			запись("e4", "a", "confirmed", 4),
			запись("r1", "a", None, 3, вид="Revision", выведено=["e4"]),
			запись("e5", "b", "difficulty", 2),
		]
		self.assertEqual({к: з.name for к, з in cross.лучшие_подтверждения(записи).items()}, {"a": "e3"})

	def test_подтверждение_наружу_с_версией(self):
		з = запись(
			"e1", "a", "confirmed", 3, course="c-a", competency_version=1, creation=datetime(2026, 11, 20, 12)
		)
		self.assertEqual(
			cross.подтверждение(з, 2, {"c-a": "Курс A"}),
			{
				"level": 3,
				"recorded_at": "2026-11-20T12:00:00",
				"course": "c-a",
				"course_title": "Курс A",
				"version": 1,
				"current_version": False,
			},
		)
		self.assertTrue(cross.подтверждение(з, 1, {})["current_version"])
		# Компетенции нет в каталоге — текущей версии нет.
		self.assertIsNone(cross.подтверждение(з, None, {})["current_version"])
		self.assertIsNone(cross.подтверждение(None, 1, {}))


class TestПоводыПодсказки(UnitTestCase):
	def test_правило(self):
		случаи = {
			"одно занятие — не повод": (
				[запись("d1", "a", "difficulty", 2), запись("d2", "a", "difficulty", 3)],
				{},
				[],
			),
			"два занятия — повод, уровень наименьший": (
				[запись("d1", "a", "difficulty", 3), запись("d2", "a", "difficulty", 2, занятие="s2")],
				{},
				[{"competencies": ["a"], "level": 2}],
			),
			"подтверждение не ниже после снимает": (
				[
					запись("d1", "a", "difficulty", 2),
					запись("d2", "a", "difficulty", 2, занятие="s2"),
					запись("e1", "a", "confirmed", 2, занятие="s3"),
				],
				{},
				[],
			),
			"подтверждение ниже — не снимает": (
				[
					запись("d1", "a", "difficulty", 3),
					запись("d2", "a", "difficulty", 3, занятие="s2"),
					запись("e1", "a", "confirmed", 2, занятие="s3"),
				],
				{},
				[{"competencies": ["a"], "level": 3}],
			),
			"подтверждение до затруднения — не снимает": (
				[
					запись("e1", "a", "confirmed", 3),
					запись("d1", "a", "difficulty", 3, занятие="s2"),
					запись("d2", "a", "difficulty", 2, занятие="s3"),
				],
				{},
				[{"competencies": ["a"], "level": 2}],
			),
			"выведенное аудитом не считается": (
				[
					запись("d1", "a", "difficulty", 2),
					запись("d2", "a", "difficulty", 2, занятие="s2"),
					запись("x1", "a", None, 2, вид="Audit", выведено=["d2"]),
				],
				{},
				[],
			),
			"пересмотр не снимает": (
				[
					запись("e1", "a", "confirmed", 3),
					запись("d1", "a", "difficulty", 2, занятие="s2"),
					запись("r1", "a", None, 1, вид="Revision", выведено=["e1"]),
					запись("d2", "a", "difficulty", 2, занятие="s3"),
				],
				{},
				[{"competencies": ["a"], "level": 2}],
			),
			# «Похожа» записана у b, а связка находится и от a: читатель симметричен.
			"связка похожих, записанных в одну сторону": (
				[запись("d1", "a", "difficulty", 2), запись("d2", "b", "difficulty", 3, занятие="s2")],
				{"b": ["a"]},
				[{"competencies": ["a", "b"], "level": 2}],
			),
			"не похожие — не связка": (
				[запись("d1", "a", "difficulty", 2), запись("d2", "c", "difficulty", 2, занятие="s2")],
				{"b": ["a"]},
				[],
			),
		}
		for имя, (записи, похожие, ожидаемое) in случаи.items():
			with self.subTest(имя):
				self.assertEqual(cross.поводы_подсказки(записи, похожие), ожидаемое)

	def test_область(self):
		записи = [запись("d1", "b", "difficulty", 2), запись("d2", "b", "difficulty", 2, занятие="s2")]
		self.assertEqual(cross.поводы_подсказки(записи, {}, область={"a"}), [])
		self.assertEqual(len(cross.поводы_подсказки(записи, {"a": ["b"]}, область={"a"})), 0)
		self.assertEqual(len(cross.поводы_подсказки(записи, {}, область={"b"})), 1)


class TestГруппы(UnitTestCase):
	def test_по_классам_схемы(self):
		схема = {
			"key": "dir",
			"title": "Направление",
			"classes": [
				{"key": "p", "title": "Продукт"},
				{"key": "t", "title": "Команда"},
				{"key": "z", "title": "Пусто"},
			],
		}
		компетенции = [
			{"key": "a", "classes": {"dir": "t"}},
			{"key": "b", "classes": {}},
			{"key": "c", "classes": {"dir": "p"}},
		]
		self.assertEqual(
			[
				(г["class"], г["title"], [к["key"] for к in г["competencies"]])
				for г in cross.группы(компетенции, схема)
			],
			[("p", "Продукт", ["c"]), ("t", "Команда", ["a"]), (None, None, ["b"])],
		)
		self.assertEqual(
			[(г["class"], [к["key"] for к in г["competencies"]]) for г in cross.группы(компетенции, None)],
			[(None, ["a", "b", "c"])],
		)


class TestВидРуководителя(UnitTestCase):
	def test_засчитано_ранее_и_записи_назначенных(self):
		карта = [{"key": "a", "title": "A"}, {"key": "b", "title": "B"}]
		цели = {"l-1": [{"competency": "a", "target": 2}, {"competency": "b", "target": 2}]}
		карта.append({"key": "c", "title": "C"})
		цели["l-1"].append({"competency": "c", "target": 2})
		записи = [запись("e1", "b", "confirmed", 2), запись("e2", "c", "confirmed", 1)]
		уровни = views.уровни_по_данным(карта, цели, {"l-1": "passed"}, записи, {"a": 2, "b": 2, "c": 2})
		# Засчитано ранее — с уровнем по видимым записям, если он ниже цели; достиг — просто уровень.
		self.assertEqual(
			[(у["key"], у["status"], у["level"]) for у in уровни],
			[("a", "credited", None), ("b", "level", 2), ("c", "credited", 1)],
		)
		записи = [
			запись("e1", "a", "confirmed", 2, course="assigned"),
			запись("e2", "a", "confirmed", 3, course="own"),
			запись("r1", "a", None, 1, course="personal", вид="Revision", выведено=["e1"]),
			запись("e4", "z", "confirmed", 3, course="assigned"),
		]
		self.assertEqual(
			[з.name for з in views.записи_назначенных(записи, "own", {"a"}, {"assigned"})],
			["e1", "e2"],
		)


ТЕКСТЫ = {"task": "Задание", "student_action": "Сделал сам", "assessment": "Следует из работы"}
ОТМЕТКИ = {"hints": False, "corrected": False, "indirect": False}


class IntegrationTestСквозныеПредставления(IntegrationTestCase):
	"""Курсы на одном выпуске каталога. Ученик записан на A и B.

	- A: цель `a` (2) в каждом уроке; B: `a` (2) в первом, `whole` (3) в третьем;
	- C, P: `b` (3), на витрине; Q: `a` (2) в первых двух уроках, третий закрывается квизом, на
	  витрине; S: `a` (2), не опубликован; D: `b` (3), анонс.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)
		cls.суффикс = суффикс
		cls.к = ключи_примера(суффикс)
		версия = каталог.опубликовать(поверх_опубликованного(пример_каталога(суффикс)), "Administrator")[
			"version"
		]

		def курс(ключ: str, цели: dict[int, list[dict]]) -> tuple[str, str]:
			р = пример_релиза(f"{ключ}-{суффикс}")
			р["catalog"] = версия
			for номер, свои in цели.items():
				р["lessons"][номер]["competencies"] = свои
			return курс_из_релиза(релиз=р)

		a, b, целое = cls.к["a"], cls.к["b"], cls.к["whole"]
		по_a = [{"competency": a, "target": 2}]
		по_b = [{"competency": b, "target": 3}]
		# У A цель по `a` в каждом уроке: урок без целей закрывается квизом.
		cls.курс_а, cls.релиз_а = курс("xv-a", {0: по_a, 1: по_a, 2: по_a})
		cls.курс_б, cls.релиз_б = курс("xv-b", {0: по_a, 2: [{"competency": целое, "target": 3}]})
		cls.курс_в, cls.релиз_в = курс("xv-c", {0: по_b})
		cls.курс_п, cls.релиз_п = курс("xv-p", {0: по_b})
		cls.курс_к, cls.релиз_к = курс("xv-q", {0: по_a, 1: по_a})
		cls.курс_с, cls.релиз_с = курс("xv-s", {0: по_a})
		cls.курс_д, _ = курс("xv-d", {0: по_b})
		cls.релизы = {
			cls.курс_а: cls.релиз_а,
			cls.курс_б: cls.релиз_б,
			cls.курс_в: cls.релиз_в,
			cls.курс_п: cls.релиз_п,
			cls.курс_к: cls.релиз_к,
			cls.курс_с: cls.релиз_с,
		}
		# Подсказка курса предлагает курсы витрины: S не опубликован, D — анонс.
		for к in (cls.курс_а, cls.курс_б, cls.курс_в, cls.курс_п, cls.курс_к, cls.курс_д):
			frappe.db.set_value("LMS Course", к, "published", 1)
		frappe.db.set_value("LMS Course", cls.курс_д, "upcoming", 1)
		cls.цели_подсказки = {cls.курс_в: (b, 3), cls.курс_п: (b, 3), cls.курс_к: (a, 2)}

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		self.ученик = создать_ученика(f"xv-{frappe.generate_hash(length=6)}@example.com")
		for курс in (self.курс_а, self.курс_б):
			зачислить_на_курс(self.ученик, курс)

	def курсы_подсказки(self, *курсы: str) -> list[dict]:
		"""Ожидаемый `courses` подсказки: курсы в порядке названия и имени, с целями."""
		return sorted(
			(
				{
					"course": к,
					"title": frappe.db.get_value("LMS Course", к, "title"),
					"targets": [
						{"competency": self.цели_подсказки[к][0], "target": self.цели_подсказки[к][1]}
					],
				}
				for к in курсы
			),
			key=lambda к: (к["title"], к["course"]),
		)

	def записать(self, курс: str, компетенция: str, исход: str, уровень, урок: str = "l-1") -> dict:
		"""Свидетельство в новом занятии урока `урок` курса `курс`."""
		занятие = frappe.get_doc("Agent Learning Session", занятие_релиза(self.ученик, курс, урок))
		return evidence.записать(
			занятие,
			self.релизы[курс],
			урок,
			компетенция,
			исход,
			уровень,
			ТЕКСТЫ,
			"free",
			ОТМЕТКИ,
			frappe.generate_hash(length=10),
		)

	def пересмотреть(self, курс: str, компетенция: str, уровень: int) -> dict:
		занятие = frappe.get_doc("Agent Learning Session", занятие_релиза(self.ученик, курс, "l-1"))
		return evidence.пересмотреть(
			занятие,
			self.релизы[курс],
			"l-1",
			компетенция,
			уровень,
			"Затруднение",
			frappe.generate_hash(length=10),
		)

	def как_ученик(self, метод, *args, **kwargs) -> dict:
		frappe.set_user(self.ученик)
		try:
			ответ = метод(*args, **kwargs)
			self.assertTrue(ответ["ok"], ответ)
			return ответ["data"]
		finally:
			frappe.set_user("Administrator")

	def пройти_урок(self, курс: str, урок: str, до_закрытия=None) -> dict:
		"""Урок через API ученика: старт, все пункты, `до_закрытия(занятие)`, закрытие — ответы."""
		from lms_frappe_app.api import student

		frappe.set_user(self.ученик)
		try:
			старт = student.start_lesson(lesson=урок_релиза(курс, урок))["data"]
			отметить_все_пункты(
				frappe.db.get_value("Agent Learning Session", старт["session"], "run"), старт["session"]
			)
			if до_закрытия:
				до_закрытия(старт["session"])
			return {
				"start": старт,
				"complete": student.complete_lesson(старт["session"]),
				"session": старт["session"],
			}
		finally:
			frappe.set_user("Administrator")

	def свидетельство_api(self, исход: str, **поля):
		"""`до_закрытия` для `пройти_урок`: свидетельство по `a` методом ученика."""
		from lms_frappe_app.api import student

		def записать(занятие: str) -> None:
			ответ = student.record_evidence(занятие, self.к["a"], исход, f"xv-{исход}-{занятие}", **поля)
			self.assertTrue(ответ["ok"], ответ)

		return записать

	def затруднение_api(self):
		return self.свидетельство_api(
			"difficulty",
			level=2,
			task="Задание",
			student_action="Сделал",
			assessment="Не вышло",
			task_form="free",
			hints=False,
			corrected=False,
			indirect=False,
		)

	def test_сквозная_карта_карта_курса_и_старт_урока(self):
		from lms_frappe_app.api import student

		a, b, целое = self.к["a"], self.к["b"], self.к["whole"]
		self.записать(self.курс_а, a, "confirmed", 3)
		self.записать(self.курс_б, b, "difficulty", 2)

		карта = self.как_ученик(student.my_competencies)
		по_ключу = {к["key"]: к for г in карта["groups"] for к in г["competencies"]}
		with self.subTest("компетенции курсов и журнала, группы по обязательной схеме"):
			self.assertIsNone(карта["course"])
			self.assertEqual(list(по_ключу), [a, целое, b])
			схема = карта["classification"]["key"]
			for группа in карта["groups"]:
				for к in группа["competencies"]:
					self.assertEqual(к["classes"].get(схема), группа["class"])
		with self.subTest("подтверждённый уровень с курсом и версией, без блокировки"):
			self.assertEqual((по_ключу[a]["level"], по_ключу[целое]["courses"]), (3, [self.курс_б]))
			self.assertCountEqual(по_ключу[a]["courses"], [self.курс_а, self.курс_б])
			подтверждено = по_ключу[a]["confirmed"]
			self.assertEqual(
				(
					подтверждено["level"],
					подтверждено["course"],
					подтверждено["version"],
					подтверждено["current_version"],
				),
				(3, self.курс_а, 1, True),
			)
			self.assertEqual(
				подтверждено["course_title"], frappe.db.get_value("LMS Course", self.курс_а, "title")
			)
			self.assertIsNone(по_ключу[b]["level"])
		with self.subTest("похожие в обе стороны, хотя записано a → b"):
			self.assertEqual([п["key"] for п in по_ключу[a]["similar"]], [b])
			self.assertEqual(
				[(п["key"], п["level"], п["confirmed"]["course"]) for п in по_ключу[b]["similar"]],
				[(a, 3, self.курс_а)],
			)
		self.assertEqual(карта["course_hints"], [])

		with self.subTest("подсказка: затруднения из двух занятий на связке похожих, полный состав курсов"):
			# P пройден: запись на курс с долей 100.
			frappe.db.set_value(
				"LMS Enrollment",
				зачислить_на_курс(self.ученик, self.курс_п),
				"progress",
				100,
			)
			self.записать(self.курс_а, a, "difficulty", 3)
			подсказки = self.как_ученик(student.my_competencies)["course_hints"]
			# A и B — записан, P — пройден, S — не опубликован, D — анонс.
			self.assertEqual(
				подсказки,
				[
					{
						"competencies": [
							{"key": b, "title": f"Умение {b}"},
							{"key": a, "title": f"Умение {a}"},
						],
						"level": 2,
						"courses": self.курсы_подсказки(self.курс_в, self.курс_к),
					}
				],
			)
			# Подтверждение b после затруднения снимает его — у a затруднение одно, повода нет.
			self.записать(self.курс_б, b, "confirmed", 2, урок="l-3")
			self.assertEqual(self.как_ученик(student.my_competencies)["course_hints"], [])

		with self.subTest("карта курса — по журналу всех курсов"):
			self.как_ученик(student.start_lesson, lesson=урок_релиза(self.курс_б, "l-1"))
			карта_б = {
				к["key"]: к
				for к in self.как_ученик(student.my_competencies, course=self.курс_б)["competencies"]
			}
			self.assertEqual((карта_б[a]["status"], карта_б[a]["level"]), ("level", 2))

		старт = self.как_ученик(student.start_lesson, lesson=урок_релиза(self.курс_б, "l-1"))
		with self.subTest("старт урока: лучшее подтверждение, похожие, засчёт цели"):
			к = next(к for к in старт["competencies"] if к["key"] == a)
			self.assertEqual(
				(к["confirmed"]["level"], к["confirmed"]["course"], к["credited"]), (3, self.курс_а, True)
			)
			self.assertEqual(
				[(п["key"], п["confirmed"]["level"], п["confirmed"]["course"]) for п in к["similar"]],
				[(b, 2, self.курс_б)],
			)

		with self.subTest("после revise_level: подтверждение выведено, засчёта нет"):
			self.как_ученик(
				student.revise_level,
				session=старт["session"],
				competency=a,
				level=1,
				reason="Затруднение повторилось",
				request_key=f"xv-rev-{self.суффикс}",
			)
			по_ключу = {
				к["key"]: к
				for г in self.как_ученик(student.my_competencies)["groups"]
				for к in г["competencies"]
			}
			self.assertEqual((по_ключу[a]["level"], по_ключу[a]["confirmed"]), (None, None))
			к = next(
				к
				for к in self.как_ученик(student.start_lesson, lesson=урок_релиза(self.курс_б, "l-1"))[
					"competencies"
				]
				if к["key"] == a
			)
			self.assertEqual((к["confirmed"], к["credited"]), (None, False))
			# Пересмотр затруднение не снимает, но оно у a одно — повода нет.
			self.assertEqual(self.как_ученик(student.my_competencies)["course_hints"], [])

	def test_вид_руководителя_по_назначенным_курсам(self):
		"""B назначен организацией, A — личный курс. Засчёт из A руководителю — «засчитано ранее» с
		видимым уровнем; пересмотр в A уровень в его отчёте не снижает; назначенный A виден."""
		a = self.к["a"]
		организация = создать_организацию(f"xv-org-{frappe.generate_hash(length=6)}")
		добавить_в_организацию(self.ученик, организация)
		менеджер = создать_менеджера(f"xv-mg-{frappe.generate_hash(length=6)}@example.com", организация)

		def назначить(курс: str) -> None:
			frappe.get_doc(
				{"doctype": "Course Allocation", "organization": организация, "course": курс}
			).insert(ignore_permissions=True)

		назначить(self.курс_б)
		self.записать(self.курс_а, a, "confirmed", 3)
		закрыт = self.пройти_урок(self.курс_б, "l-1")
		self.assertTrue(закрыт["complete"]["ok"], закрыт)
		self.assertEqual(self.уровень_у_руководителя(менеджер, self.курс_б, a), ("credited", None))

		with self.subTest("засчитано ранее с меньшим видимым уровнем"):
			self.записать(self.курс_б, a, "confirmed", 1)
			self.assertEqual(self.уровень_у_руководителя(менеджер, self.курс_б, a), ("credited", 1))

		with self.subTest("подтверждение в назначенном курсе, пересмотр в личном не виден"):
			self.записать(self.курс_б, a, "confirmed", 2)
			self.assertEqual(self.уровень_у_руководителя(менеджер, self.курс_б, a), ("level", 2))
			self.пересмотреть(self.курс_а, a, 1)
			self.assertEqual(evidence.состояние_компетенции(self.ученик, self.курс_б, a)["level"], 1)
			self.assertEqual(self.уровень_у_руководителя(менеджер, self.курс_б, a), ("level", 2))

		with self.subTest("курс назначен — его записи и пересмотры видны, засчёта из него нет"):
			назначить(self.курс_а)
			self.assertEqual(self.уровень_у_руководителя(менеджер, self.курс_б, a), ("level", 1))

	def уровень_у_руководителя(
		self, менеджер: str, курс: str, компетенция: str, организация: str | None = None
	) -> tuple:
		"""(status, level) компетенции курса у ученика в `org_report` и в `student_detail` — одни."""
		from lms_frappe_app.api import manager

		frappe.set_user(менеджер)
		try:
			строка = next(
				с
				for с in manager.org_report(course=курс, organization=организация)["data"]["rows"]
				if с["user"] == self.ученик
			)
			курс_ученика = next(
				к for к in manager.student_detail(self.ученик)["data"]["courses"] if к["id"] == курс
			)
		finally:
			frappe.set_user("Administrator")
		по_отчёту = next(к for к in строка["competencies"] if к["key"] == компетенция)
		по_ученику = next(к for к in курс_ученика["competencies"] if к["key"] == компетенция)
		self.assertEqual(по_отчёту, по_ученику)
		return по_отчёту["status"], по_отчёту["level"]

	def test_назначенные_курсы_руководителя(self):
		"""Руководитель двух организаций: B назначен первой, A — второй, Q — чужой организацией,
		S — его приостановленной организацией. Уровни B — по записям B и A, и при фильтре отчёта
		по первой организации тоже."""
		from lms_frappe_app.tests.sample_data import добавить_в_организацию as добавить

		a = self.к["a"]
		хвост = frappe.generate_hash(length=6)
		первая, вторая, чужая, приостановленная = (
			создать_организацию(f"xv-{имя}-{хвост}") for имя in ("one", "two", "other", "frozen")
		)
		менеджер = создать_менеджера(f"xv-mg2-{хвост}@example.com", первая)
		for организация in (вторая, приостановленная):
			добавить(менеджер, организация, role="Manager")
		создать_менеджера(f"xv-mg3-{хвост}@example.com", чужая)
		for организация, курс in (
			(первая, self.курс_б),
			(вторая, self.курс_а),
			(чужая, self.курс_к),
			(приостановленная, self.курс_с),
		):
			добавить_в_организацию(self.ученик, организация)
			frappe.get_doc(
				{"doctype": "Course Allocation", "organization": организация, "course": курс}
			).insert(ignore_permissions=True)
		frappe.db.set_value("Learning Organization", приостановленная, "status", "Suspended")

		# Урок B начат — `a` открыта до 2.
		self.записать(self.курс_б, a, "skipped", None)
		self.записать(self.курс_к, a, "confirmed", 2)
		self.записать(self.курс_с, a, "confirmed", 2)
		self.assertEqual(self.уровень_у_руководителя(менеджер, self.курс_б, a), ("no_info", None))
		self.записать(self.курс_а, a, "confirmed", 1)
		for организация in (None, первая):
			with self.subTest(organization=организация):
				self.assertEqual(
					self.уровень_у_руководителя(менеджер, self.курс_б, a, организация), ("level", 1)
				)

	def test_подсказка_в_конце_курса(self):
		"""Последний урок A: отказ `course_queue_open` и закрытие несут подсказку — курсы, где a или
		похожая b в целях не ниже уровня затруднения, кроме A и курсов, на которые ученик записан."""
		from lms_frappe_app.api import student

		a, b = self.к["a"], self.к["b"]
		self.записать(self.курс_б, b, "difficulty", 2)
		первый = self.пройти_урок(self.курс_а, "l-1", self.затруднение_api())
		self.assertEqual(первый["complete"]["data"]["course_hints"], [])
		второй = self.пройти_урок(self.курс_а, "l-2", self.свидетельство_api("skipped", task="Предложил"))[
			"complete"
		]
		self.assertTrue(второй["ok"], второй)
		последний = self.пройти_урок(self.курс_а, "l-3", self.свидетельство_api("skipped", task="Предложил"))
		отказ = последний["complete"]["error"]
		self.assertEqual(отказ["code"], "course_queue_open", отказ)
		ожидаемые = [
			{
				"competencies": [{"key": b, "title": f"Умение {b}"}, {"key": a, "title": f"Умение {a}"}],
				"level": 2,
				"courses": self.курсы_подсказки(self.курс_в, self.курс_п, self.курс_к),
			}
		]
		self.assertEqual(отказ["course_hints"], ожидаемые)
		закрыт = self.как_ученик(student.complete_lesson, последний["session"])
		self.assertEqual(закрыт["course_hints"], ожидаемые)

	def test_подсказка_после_квиза_последнего_урока(self):
		"""Q: третий урок без целей закрывается квизом. Отказ `course_queue_open` у `request_quiz` и
		итог сданной попытки несут подсказку; итог — ту же форму, что `complete_lesson`."""
		from lms_frappe_app.api import student

		a, b = self.к["a"], self.к["b"]
		зачислить_на_курс(self.ученик, self.курс_к)
		self.записать(self.курс_б, b, "difficulty", 2)
		self.assertTrue(self.пройти_урок(self.курс_к, "l-1", self.затруднение_api())["complete"]["ok"])
		второй = self.пройти_урок(self.курс_к, "l-2", self.свидетельство_api("skipped", task="Предложил"))
		self.assertEqual(второй["complete"]["data"]["course_hints"], [])
		ожидаемые = [
			{
				"competencies": [{"key": b, "title": f"Умение {b}"}, {"key": a, "title": f"Умение {a}"}],
				"level": 2,
				"courses": self.курсы_подсказки(self.курс_в, self.курс_п),
			}
		]
		frappe.set_user(self.ученик)
		try:
			старт = student.start_lesson(lesson=урок_релиза(self.курс_к, "l-3"))["data"]
			отметить_все_пункты(
				frappe.db.get_value("Agent Learning Session", старт["session"], "run"), старт["session"]
			)
			отказ = student.request_quiz(старт["session"])
			self.assertEqual(отказ["error"]["code"], "course_queue_open", отказ)
			self.assertEqual(отказ["error"]["course_hints"], ожидаемые)
			квиз = student.request_quiz(старт["session"])["data"]
			итог = student.submit_answer(квиз["attempt"], квиз["question"]["id"], "Первый")["data"]["result"]
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(итог["passed"], итог)
		self.assertTrue({"next_lesson", "empty_blocks", "homework", "course_hints"} <= set(итог))
		self.assertEqual(итог["course_hints"], ожидаемые)
