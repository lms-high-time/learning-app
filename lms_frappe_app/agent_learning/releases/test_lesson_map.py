# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Срез карты урока для кабинета автора (learning-services#560) — без базы."""

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from lms_frappe_app.agent_learning.releases import index, lesson_map, places
from lms_frappe_app.tests.release_sample import пример_карты


def _тезис_причины(карта: dict) -> None:
	"""Ловушке `E1` — причина «не знает» с тезисом `T5`, который опирается на `T4`."""
	карта["decisions"]["D1"]["variants"]["V1b"]["errors"]["E1"]["causes"].append({"kind": "not_know", "thesis": "T5"})
	карта["theses"]["T5"] = {"text": "Тезис причины", "relies_on": ["T4"]}


class TestСрезКартыУрока(UnitTestCase):
	def test_срез_берёт_узлы_урока_и_ссылки_ситуаций(self):
		срез = lesson_map.срез(пример_карты(), "l-1")

		self.assertEqual(
			{коллекция: list(срез[коллекция]) for коллекция in lesson_map.КОЛЛЕКЦИИ},
			{
				"decisions": ["D1", "D2"],
				"results": ["R1"],
				"situations": ["S1", "S2"],
				"theses": ["T4", "T0", "T1", "T2", "T3"],
				"misconceptions": ["M1", "M2"],
				"goals": ["G1"],
			},
		)
		self.assertEqual(срез["lesson"]["title"], "Урок первый")
		# Ссылки ситуаций — из всей карты; решение без вариантов, вариант без ошибок.
		self.assertEqual(set(срез["refs"]), {"D1", "V1a", "D9", "V9a", "E1", "K1"})
		self.assertNotIn("variants", срез["refs"]["D9"])
		self.assertEqual(срез["refs"]["E1"]["when"], "Когда спешат")
		self.assertEqual(
			lesson_map.узлы(срез),
			{"D1", "V1a", "V1b", "E1", "D2", "R1", "K1", "S1", "S2", "T0", "T1", "T2", "T3", "T4", "M1", "M2"},
		)

	def test_тезисы_и_заблуждения_по_правилам_компилятора(self):
		"""Тезисы урока — как их берёт компилятор курса: свои тезисы урока,
		опоры решений и требований вглубь (опора — раньше тезиса), тезисы из
		причин; заблуждения — только из причин; опровержение извне — только
		заблуждению, которое не опровергает ни один тезис урока."""
		for что, правка, тезисы, заблуждения in (
			("образец", None, ["T4", "T0", "T1", "T2", "T3"], ["M1", "M2"]),
			(
				"своё опровержение — чужое не нужно",
				lambda к: к["theses"]["T2"].update(refutes=["M1"]),
				["T4", "T0", "T1", "T2"],
				["M1", "M2"],
			),
			(
				"опровергающих нет",
				lambda к: к["theses"]["T3"].pop("refutes"),
				["T4", "T0", "T1", "T2"],
				["M1", "M2"],
			),
			(
				"цикл опор",
				lambda к: к["theses"]["T0"].update(relies_on=["T1"]),
				["T4", "T0", "T1", "T2", "T3"],
				["M1", "M2"],
			),
			(
				"тезис из причины ошибки",
				_тезис_причины,
				["T4", "T0", "T1", "T5", "T2", "T3"],
				["M1", "M2"],
			),
		):
			with self.subTest(что):
				карта = пример_карты()
				if правка:
					правка(карта)
				срез = lesson_map.срез(карта, "l-1")
				self.assertEqual((list(срез["theses"]), list(срез["misconceptions"])), (тезисы, заблуждения))

	def test_карта_любой_формы_не_роняет_срез(self):
		"""Форму карты задаёт компилятор: чего нет или что не той формы, пропускается."""
		странная = пример_карты()
		странная["decisions"]["D1"]["variants"] = ["не словарь"]
		странная["situations"]["S1"]["sets"] = ["строка", {"decision": 5}]
		странная["lessons"]["l-1"]["quiz"] = "S2"
		странная["theses"] = []
		for карта, урок, ждём in (
			(None, "l-1", None),
			({"opaque": True}, "l-1", None),
			({"lessons": {"l-1": "не словарь"}}, "l-1", None),
			(пример_карты(), "l-9", None),
		):
			with self.subTest(карта=карта, урок=урок):
				self.assertEqual(lesson_map.срез(карта, урок), ждём)
		срез = lesson_map.срез(странная, "l-1")
		self.assertEqual((list(срез["situations"]), list(срез["theses"])), (["S1"], []))
		self.assertEqual(lesson_map.узлы(срез) & {"V1a", "E1"}, set())
		self.assertEqual(lesson_map.узлы(None), set())
		self.assertEqual(lesson_map.собрать("не карта"), {"lessons": {}, "keys": {к: [] for к in lesson_map.КОЛЛЕКЦИИ}})

	def test_путь_отличий_относится_к_уроку(self):
		данные = lesson_map.собрать(пример_карты())
		срез, ключи = данные["lessons"]["l-1"], данные["keys"]
		for путь, ждём in (
			("lessons[l-1]", True),
			("lessons[l-1].quiz.questions[S1/l-1-D1].text", True),
			("lessons[l-10].title", False),
			("agent.lessons.l-1.directive", True),
			("agent.lessons.l-1", True),
			("agent.lessons.l-10", False),
			("map.lessons.l-1.situations", True),
			("map.decisions.D1.variants.V1a.text", True),
			("map.decisions.D9", False),
			("map.situations.S2", True),
			("map.results.R1.requirements.K1.text", True),
			("map.theses.T2.origin.quote", True),
			("map.misconceptions.M2", True),
			("map.misconceptions.M3", False),
			("map.goals.G1", False),
			("map.frame.promise", False),
			("course.title", False),
			("chapters[ch-1].lessons", False),
		):
			with self.subTest(путь=путь):
				self.assertEqual(lesson_map.путь_урока(путь, "l-1", срез, ключи), ждём)
		self.assertFalse(lesson_map.путь_урока("map.decisions.D1", "l-1", None, ключи))

	def test_ключ_узла_с_точкой(self):
		"""Ключи карты бывают с точкой (`T1` и `T1.1`): узел пути — самый длинный
		ключ коллекции всей карты, и он должен быть в срезе урока."""
		карта = пример_карты()
		карта["theses"]["T1.1"] = {"text": "Тезис другого урока"}
		данные = lesson_map.собрать(карта)
		срез, ключи = данные["lessons"]["l-1"], данные["keys"]
		self.assertTrue(lesson_map.путь_урока("map.theses.T1.text", "l-1", срез, ключи))
		self.assertFalse(lesson_map.путь_урока("map.theses.T1.1.text", "l-1", срез, ключи))
		self.assertTrue(lesson_map.путь_урока("map.theses.T1.2.text", "l-1", срез, ключи))

	def test_освобождённая_версия_не_кэшируется(self):
		"""Снимок освободила новая публикация между чтениями: карты нет, и в
		кэш не попадает пустое значение под ключом версии."""
		with (
			patch.object(index, "снимок", side_effect=ValueError("освобождено")),
			patch.object(frappe.cache, "get_value", return_value=None),
			patch.object(frappe.cache, "set_value") as записать,
		):
			self.assertIsNone(lesson_map.срезы("REL-X", "d"))
			self.assertEqual(places.узлы_карты("REL-X", "d"), {})
		записать.assert_not_called()
