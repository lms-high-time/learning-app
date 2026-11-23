# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Аудит свидетельств карты компетенций (learning-services#811)."""

import json

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.agent_learning.competencies import queue


class _Запись(dict):
	__getattr__ = dict.get


def запись(
	имя, компетенция, исход=None, уровень=0, вид="Evidence", выведено=None, заключение=None, проверено="e1"
):
	return _Запись(
		audited=проверено,
		name=имя,
		kind=вид,
		competency=компетенция,
		outcome=исход,
		level=уровень,
		conclusion=заключение,
		assessment=f"причина {имя}",
		withdrawn=json.dumps(выведено) if выведено is not None else None,
	)


class TestСомненияАудита(UnitTestCase):
	def test_открытие_и_выход(self):
		for случай, записи, итог in (
			(
				"недостаточно оснований — на уровне проверенного",
				[
					запись("e1", "base-1", "confirmed", 3),
					запись("a1", "base-1", уровень=3, вид="Audit", заключение="insufficient"),
				],
				{"base-1": ("audit_doubt", 3)},
			),
			(
				"отмена — повторная проверка",
				[
					запись("e1", "base-1", "confirmed", 3),
					запись(
						"a1", "base-1", уровень=3, вид="Audit", заключение="contradiction", выведено=["e1"]
					),
				],
				{"base-1": ("audit_withdrawn", 3)},
			),
			(
				"достаточно и нельзя проверить — ничего",
				[
					запись("a1", "base-1", уровень=3, вид="Audit", заключение="sufficient"),
					запись("a2", "base-2", уровень=2, вид="Audit", заключение="unverifiable"),
				],
				{},
			),
			(
				"подтверждение не ниже уровня после заключения закрывает",
				[
					запись("a1", "base-1", уровень=2, вид="Audit", заключение="insufficient"),
					запись("e2", "base-1", "confirmed", 2),
				],
				{},
			),
			(
				"затруднение на уровне не выше — повторная проверка была",
				[
					запись(
						"a1", "base-1", уровень=3, вид="Audit", заключение="contradiction", выведено=["e1"]
					),
					запись("e2", "base-1", "difficulty", 3),
				],
				{},
			),
			(
				"пересмотр закрывает",
				[
					запись("a1", "base-1", уровень=3, вид="Audit", заключение="insufficient"),
					запись("r1", "base-1", уровень=0, вид="Revision"),
				],
				{},
			),
			(
				"открыто несколько — наивысшее, позднее подтверждение низкого не снимает высокое",
				[
					запись("a1", "base-1", уровень=3, вид="Audit", заключение="insufficient", проверено="e1"),
					запись("a2", "base-1", уровень=1, вид="Audit", заключение="insufficient", проверено="e0"),
					запись("e2", "base-1", "confirmed", 1),
				],
				{"base-1": ("audit_doubt", 3)},
			),
			(
				"позднее «достаточно» по той же записи снимает прежнее",
				[
					запись("a1", "base-1", уровень=3, вид="Audit", заключение="insufficient", проверено="e1"),
					запись("a2", "base-1", уровень=3, вид="Audit", заключение="sufficient", проверено="e1"),
				],
				{},
			),
			(
				"противоречие без отмены (затруднение, пересмотр) — сомнение, а не отмена",
				[запись("a1", "base-1", уровень=2, вид="Audit", заключение="contradiction", проверено="r1")],
				{"base-1": ("audit_doubt", 2)},
			),
			(
				"подтверждение ниже — не закрывает",
				[
					запись("a1", "base-1", уровень=3, вид="Audit", заключение="insufficient"),
					запись("e2", "base-1", "confirmed", 2),
				],
				{"base-1": ("audit_doubt", 3)},
			),
		):
			with self.subTest(случай):
				self.assertEqual(
					{к: (п["reason"], п["level"]) for к, п in queue.сомнения_аудита(записи).items()}, итог
				)


class TestУровниЦелыми(UnitTestCase):
	def test_дробный_уровень_в_индексе_целый(self):
		"""Схема принимает `3.0` за целое; в индекс уровень ложится целым."""
		from lms_frappe_app.agent_learning.releases import index

		self.assertEqual(
			index._цели_целыми([{"competency": "K1", "target": 3.0}]), [{"competency": "K1", "target": 3}]
		)


class IntegrationTestАудит(IntegrationTestCase):
	def test_отмена_выводит_подтверждение_и_ставит_в_очередь(self):
		from lms_frappe_app.agent_learning.competencies import evidence
		from lms_frappe_app.api import authoring, student
		from lms_frappe_app.tests.release_sample import пример_релиза, с_каталогом_компетенций
		from lms_frappe_app.tests.sample_data import (
			зачислить_на_курс,
			курс_из_релиза,
			создать_ученика,
			урок_релиза,
		)

		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		ключ = f"audit-{frappe.generate_hash(length=6)}"
		курс, _ = курс_из_релиза(релиз=с_каталогом_компетенций(пример_релиза(ключ)))
		ученик = создать_ученика(f"{ключ}@example.com")
		зачислить_на_курс(ученик, курс)
		frappe.set_user(ученик)
		занятие = student.start_lesson(lesson=урок_релиза(курс, "l-1"))["data"]["session"]
		поля = {
			"task": "Выбрать устройство",
			"student_action": "Выбрал из предложенного",
			"assessment": "Применяет",
			"task_form": "free",
			"hints": False,
			"corrected": False,
			"indirect": False,
		}
		первое = student.record_evidence(занятие, "base-1", "confirmed", "a-1", level=1, **поля)["data"][
			"event"
		]
		второе = student.record_evidence(занятие, "base-1", "confirmed", "a-2", level=3, **поля)["data"][
			"event"
		]

		# Ученик своим токеном аудит не пишет; методист — тоже: тексты свидетельств — работа ученика.
		with self.assertRaises(frappe.PermissionError):
			authoring.audit_evidence(event=второе, conclusion="contradiction", reason="Сам себе")
		from lms_frappe_app.tests.sample_data import создать_куратора

		frappe.set_user("Administrator")
		методист = создать_куратора(f"{ключ}-author@example.com")
		frappe.set_user(методист)
		for имя, вызов in {
			"выборка": lambda: authoring.audit_sample(course=курс),
			"заключение": lambda: authoring.audit_evidence(event=второе, conclusion="sufficient"),
		}.items():
			with self.subTest(имя), self.assertRaises(frappe.PermissionError):
				вызов()

		frappe.set_user("Administrator")
		self.assertEqual(
			authoring.audit_evidence(event=второе, conclusion="insufficient")["error"]["code"],
			"audit_reason_required",
		)
		итог = authoring.audit_evidence(
			event=второе, conclusion="contradiction", reason="Выбор из предложенного — не применяет"
		)["data"]
		self.assertEqual((итог["withdrawn"], итог["state"]["level"]), (True, 1))
		self.assertEqual(
			json.loads(frappe.db.get_value(evidence.ЖУРНАЛ, итог["audit"], "withdrawn")), [второе]
		)
		карта = queue.карта_ученика(ученик, курс, frappe.db.get_value("LMS Course", курс, "active_release"))
		self.assertEqual(
			[(п["competency"], п["reason"], п["level"]) for п in карта["queue"]],
			[("base-1", "audit_withdrawn", 3)],
		)
		# Проверенное в выборку больше не идёт; первое подтверждение — ещё да.
		выборка = [з["event"] for з in authoring.audit_sample(course=курс)["data"]["events"]]
		self.assertNotIn(второе, выборка)
		self.assertIn(первое, выборка)
