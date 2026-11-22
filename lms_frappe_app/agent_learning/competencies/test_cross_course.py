# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Компетенции каталога в нескольких курсах: засчёт, пересмотр, аудит, очередь, сброс
(learning-services#826).

Два курса на одном выпуске каталога: у первого урока обоих цель по базовой
компетенции `a` (уровень 2), у третьего урока курса B — по интегративной `whole`
из `a` и `b`. Правило закрытия без базы — `test_closing`.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.catalog import service as каталог
from lms_frappe_app.agent_learning.competencies import audit, closing, evidence, queue
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.tests.catalog_sample import ключи_примера, поверх_опубликованного, пример_каталога
from lms_frappe_app.tests.release_sample import пример_релиза, с_компетенциями
from lms_frappe_app.tests.sample_data import (
	занятие_релиза,
	зачислить_на_курс,
	курс_из_релиза,
	отметить_все_пункты,
	политика_по_умолчанию,
	создать_ученика,
	урок_релиза,
)

ТЕКСТЫ = {"task": "Задание", "student_action": "Сделал сам", "assessment": "Следует из работы"}
ОТМЕТКИ = {"hints": False, "corrected": False, "indirect": False}


class IntegrationTestСквозныеКомпетенции(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)
		cls.к = ключи_примера(суффикс)
		версия = каталог.опубликовать(поверх_опубликованного(пример_каталога(суффикс)), "Administrator")[
			"version"
		]

		def релиз(ключ: str, с_целым: bool) -> dict:
			р = пример_релиза(f"{ключ}-{суффикс}")
			р["catalog"] = версия
			р["lessons"][0]["competencies"] = [{"competency": cls.к["a"], "target": 2}]
			if с_целым:
				р["lessons"][2]["competencies"] = [{"competency": cls.к["whole"], "target": 3}]
			return р

		cls.курс_а, cls.релиз_а = курс_из_релиза(релиз=релиз("cross-a", False))
		cls.курс_б, cls.релиз_б = курс_из_релиза(релиз=релиз("cross-b", True))
		cls.цели = [{"competency": cls.к["a"], "target": 2}]

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.ученик = создать_ученика(f"cross-{frappe.generate_hash(length=6)}@example.com")
		for курс in (self.курс_а, self.курс_б):
			зачислить_на_курс(self.ученик, курс)

	def записать(self, курс: str, релиз: str, компетенция: str, исход: str, уровень, ключ: str) -> dict:
		занятие = frappe.get_doc("Agent Learning Session", занятие_релиза(self.ученик, курс, "l-1"))
		return evidence.записать(
			занятие, релиз, "l-1", компетенция, исход, уровень, ТЕКСТЫ, "free", ОТМЕТКИ, ключ
		)

	def подтвердить_в_а(self, уровень: int, ключ: str) -> str:
		return self.записать(self.курс_а, self.релиз_а, self.к["a"], "confirmed", уровень, ключ)["event"]

	def закрыть_урок_б(self) -> dict:
		"""Урок `l-1` курса B через API ученика: старт, все пункты, закрытие; ответ закрытия."""
		from lms_frappe_app.api import student

		frappe.set_user(self.ученик)
		try:
			старт = student.start_lesson(lesson=урок_релиза(self.курс_б, "l-1"))["data"]
			отметить_все_пункты(
				frappe.db.get_value("Agent Learning Session", старт["session"], "run"), старт["session"]
			)
			return {"start": старт, "complete": student.complete_lesson(старт["session"])}
		finally:
			frappe.set_user("Administrator")

	def проверка_б(self) -> tuple[list[dict], list[dict]]:
		return closing.проверка(self.ученик, self.курс_б, self.релиз_б, "l-1", self.цели)

	def test_свидетельство_по_карте_курса_с_версией(self):
		"""Компетенция — из карты курса: цели и их составляющие по каталогу; запись несёт версию
		компетенции, а состояние в ответе — по всем курсам."""
		e1 = self.подтвердить_в_а(3, "v-1")
		self.assertEqual(frappe.db.get_value(evidence.ЖУРНАЛ, e1, "competency_version"), 1)
		# `b` — составляющая цели курса B, в карте курса A её нет.
		with self.assertRaises(Отказ) as отказ:
			self.записать(self.курс_а, self.релиз_а, self.к["b"], "skipped", None, "v-2")
		self.assertEqual(
			(отказ.exception.код, отказ.exception.подробности),
			("competency_unknown", {"competency": self.к["b"]}),
		)
		self.assertEqual(
			self.записать(self.курс_б, self.релиз_б, self.к["b"], "skipped", None, "v-3")["competency"],
			self.к["b"],
		)
		в_б = self.записать(self.курс_б, self.релиз_б, self.к["a"], "insufficient", None, "v-4")
		self.assertEqual(в_б["state"], {"level": 3, "last_outcome": "insufficient", "last_level": None})

	def test_засчёт_закрывает_урок_другого_курса(self):
		e1 = self.подтвердить_в_а(3, "c-1")
		итог = self.закрыть_урок_б()
		старт, закрыт = итог["start"], итог["complete"]
		self.assertEqual(старт["competencies"][0]["state"]["level"], 3)
		self.assertTrue(закрыт["ok"], закрыт)
		self.assertEqual(
			frappe.get_all(
				"Agent Session Event",
				filters={"session": старт["session"], "kind": "Goal Credited"},
				fields=["credited_by", "competency", "target", "note"],
			),
			[
				{
					"credited_by": e1,
					"competency": self.к["a"],
					"target": 2,
					"note": f"{self.к['a']} не ниже 2: засчитано ранее",
				}
			],
		)
		# Журнал занятий читает руководитель организации: источник и уровень засчёта ему не видны.
		заметка = frappe.db.get_value(
			"Agent Session Event", {"session": старт["session"], "kind": "Goal Credited"}, "note"
		)
		self.assertNotIn(self.курс_а, заметка)
		self.assertNotIn("уровень", заметка)
		# Очередь и состояние — по всем курсам, открытый уровень — по курсу B.
		карта = queue.карта_ученика(self.ученик, self.курс_б, self.релиз_б)
		self.assertEqual(
			(карта["state"][self.к["a"]]["level"], карта["queue"], карта["open_levels"]),
			(3, [], {self.к["a"]: 2}),
		)

	def test_пересмотр_и_аудит_во_всех_курсах(self):
		e1 = self.подтвердить_в_а(3, "r-1")
		занятие_б = frappe.get_doc("Agent Learning Session", занятие_релиза(self.ученик, self.курс_б, "l-1"))
		пересмотр = evidence.пересмотреть(
			занятие_б, self.релиз_б, "l-1", self.к["a"], 1, "Затруднение повторилось", "r-2"
		)
		self.assertEqual(
			json.loads(frappe.db.get_value(evidence.ЖУРНАЛ, пересмотр["event"], "withdrawn")), [e1]
		)
		self.assertIsNone(
			evidence.состояние_компетенции(self.ученик, self.курс_а, self.к["a"], True)["level"]
		)
		self.assertEqual(self.проверка_б(), (self.цели, []))

		# Подтверждение после пересмотра засчитывается, отменённое аудитом — нет.
		e2 = self.подтвердить_в_а(2, "r-3")
		self.assertEqual(self.проверка_б()[1][0]["evidence"], e2)
		заключение = audit.заключить(e2, audit.ПРОТИВОРЕЧИЕ, "Уровень не следует из записи")
		self.assertEqual((заключение["withdrawn"], заключение["state"]["level"]), (True, None))
		self.assertEqual(frappe.db.get_value(evidence.ЖУРНАЛ, заключение["audit"], "competency_version"), 1)
		self.assertEqual(self.проверка_б(), (self.цели, []))

	def test_пересброс_курса(self):
		"""Сброс архивирует только свидетельства своего курса: после сброса курса B подтверждение из
		курса A засчитывается, а сброс курса A не открывает урок курса B, закрытый засчётом."""
		from lms_frappe_app.agent_learning.reset import сбросить_прогресс

		e1 = self.подтвердить_в_а(3, "s-1")
		self.assertTrue(self.закрыть_урок_б()["complete"]["ok"])

		сбросить_прогресс(self.ученик, self.курс_б, "Administrator")
		self.assertEqual(self.проверка_б()[1][0]["evidence"], e1)
		зачислить_на_курс(self.ученик, self.курс_б)
		self.assertTrue(self.закрыть_урок_б()["complete"]["ok"])

		сбросить_прогресс(self.ученик, self.курс_а, "Administrator")
		self.assertEqual(
			frappe.db.get_value(
				"Agent Lesson Run",
				{"student": self.ученик, "course": self.курс_б, "lesson_key": "l-1"},
				"status",
			),
			queue.ПРОЙДЕН,
		)
		self.assertEqual(self.проверка_б(), (self.цели, []))

	def test_граница_ключей_карты_внутри_релиза(self):
		"""Запись другого курса без версии компетенции — ключ карты внутри релиза — не засчитывается,
		даже если совпала со слагом; курс с картой внутри релиза читает журнал только по себе."""

		def вставить(курс: str, компетенция: str, версия: int) -> None:
			frappe.get_doc(
				{
					"doctype": evidence.ЖУРНАЛ,
					"kind": evidence.СВИДЕТЕЛЬСТВО,
					"student": self.ученик,
					"course": курс,
					"lesson_key": "l-1",
					"competency": компетенция,
					"competency_version": версия,
					"outcome": evidence.ПОДТВЕРЖДЕНО,
					"level": 4,
					"request_key": frappe.generate_hash(length=10),
				}
			).insert(ignore_permissions=True)

		with self.subTest("слаг в записи без версии"):
			вставить(self.курс_а, self.к["a"], 0)
			self.assertEqual(self.проверка_б(), (self.цели, []))

		with self.subTest("курс с картой внутри релиза"):
			курс, релиз = курс_из_релиза(
				релиз=с_компетенциями(пример_релиза(f"cross-map-{frappe.generate_hash(length=6)}"))
			)
			вставить(self.курс_а, "B1", 1)
			self.assertEqual(
				closing.непроверенные(self.ученик, курс, релиз, "l-1", [{"competency": "B1", "target": 2}]),
				[{"competency": "B1", "target": 2}],
			)
