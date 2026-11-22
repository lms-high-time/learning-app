# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Релиз курса на каталоге компетенций: проверка ссылок и карта из каталога (learning-services#825).

Релиз с картой внутри проверяют тесты карты (`releases.test_checks`,
`competencies.*`) — они идут без изменений.
"""

import copy
import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.catalog import read
from lms_frappe_app.agent_learning.catalog import service as каталог
from lms_frappe_app.agent_learning.competencies import lesson, views
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases import index, view
from lms_frappe_app.agent_learning.releases import service as релизы
from lms_frappe_app.tests.catalog_sample import (
	ключи_примера,
	компетенция,
	поверх_опубликованного,
	пример_каталога,
)
from lms_frappe_app.tests.release_sample import пример_релиза


class IntegrationTestРелизНаКаталоге(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.суффикс = frappe.generate_hash(length=6)
		cls.к = ключи_примера(cls.суффикс)
		cls.каталог = поверх_опубликованного(пример_каталога(cls.суффикс))
		# Компетенция каталога вне карты курса: ни цель урока, ни составляющая.
		cls.каталог["competencies"].append(компетенция(f"lonely-{cls.суффикс}", cls.к["classes"]))
		cls.выпуск = каталог.опубликовать(cls.каталог, "Administrator")

	@staticmethod
	def последняя_версия() -> int:
		return frappe.db.sql(f"select max(version) from `tab{read.ВЫПУСК}`")[0][0]

	def релиз(self) -> dict:
		"""Релиз курса на выпуске каталога: цели уроков `l-1` (базовая) и `l-3` (интегративная),
		контекст курса по интегративной — в срезе урока `l-3`."""
		р = пример_релиза(f"on-catalog-{frappe.generate_hash(length=6)}")
		р["catalog"] = self.последняя_версия()
		р["lessons"][0]["competencies"] = [{"competency": self.к["a"], "target": 2}]
		р["lessons"][2]["competencies"] = [{"competency": self.к["whole"], "target": 3}]
		р["agent"]["lessons"]["l-3"]["competencies"] = {self.к["whole"]: "Контекст курса"}
		return р

	def test_карта_курса_из_каталога(self):
		ответ = релизы.опубликовать(self.релиз(), None, "Administrator")
		релиз = ответ["release"]
		к = self.к

		with self.subTest("релиз ссылается на выпуск, карты внутри нет"):
			запись = frappe.db.get_value(
				index.РЕЛИЗ, релиз, ["competency_catalog", "competency_map"], as_dict=True
			)
			self.assertEqual(запись.competency_catalog, self.выпуск["catalog_release"])
			self.assertEqual(json.loads(запись.competency_map), [])
			self.assertEqual(view.история(ответ["course"], релиз)[0]["catalog"], self.выпуск["version"])

		with self.subTest("карта: цели по порядку уроков, затем составляющие"):
			карта = index.карта_компетенций(релиз)
			self.assertEqual([к_["key"] for к_ in карта], [к["a"], к["whole"], к["b"]])
			целое = карта[1]
			self.assertEqual(
				(целое["kind"], целое["classes"][к["scheme"]], целое["version"], целое["parts"]),
				(
					"integrative",
					к["x"],
					1,
					[{"competency": к["a"], "from_level": 1}, {"competency": к["b"], "from_level": 3}],
				),
			)
			self.assertEqual(целое["levels"]["3"], f"Признак уровня 3 для {к['whole']}")
			self.assertEqual(карта[0]["similar"], [к["b"]])

		with self.subTest("старт урока: признаки из каталога, контекст из среза"):
			пакет, цели = index.пакет_и_компетенции_урока(релиз, "l-3")
			старт = lesson.для_старта(релиз, цели, пакет)
			self.assertEqual([с["key"] for с in старт], [к["whole"], к["a"], к["b"]])
			self.assertEqual(
				(старт[0]["target"], старт[0]["text"], старт[0]["version"], старт[0]["levels"]["4"]),
				(3, "Контекст курса", 1, f"Признак уровня 4 для {к['whole']}"),
			)

		with self.subTest("уровни ученика по курсам"):
			уровни = views.уровни_по_курсам("Administrator", {ответ["course"]: релиз})
			self.assertEqual([у["key"] for у in уровни[ответ["course"]]], [к["a"], к["whole"], к["b"]])

		with self.subTest("правка каталога видна курсу без нового релиза"):
			правка = copy.deepcopy(self.каталог)
			целое = next(к_ for к_ in правка["competencies"] if к_["key"] == к["whole"])
			целое["title"] = "Новое название"
			целое["levels"]["2"] = "Новый признак"
			каталог.опубликовать(правка, "Administrator")
			свежая = index.карта_компетенций(релиз)[1]
			self.assertEqual((свежая["title"], свежая["version"]), ("Новое название", 2))

	def test_ссылки_на_каталог_проверяются(self):
		# Новый выпуск каталога — редакторская правка компетенции вне курса: у релиза, собранного
		# по прежнему выпуску, версия устарела.
		одинокая = компетенция(f"lonely-{self.суффикс}", self.к["classes"])
		одинокая["title"] = "Другое название"
		каталог.опубликовать(
			поверх_опубликованного(
				{"format": "lms-competency-catalog/1", "classifications": [], "competencies": [одинокая]}
			),
			"Administrator",
		)
		последняя = self.последняя_версия()

		with self.subTest("контекст по составляющей цели — из карты курса"):
			р = self.релиз()
			р["agent"]["lessons"]["l-3"]["competencies"][self.к["b"]] = "Контекст составляющей"
			релизы.опубликовать(р, None, "Administrator")

		def проблемы(правка) -> list[dict]:
			р = self.релиз()
			правка(р)
			with self.assertRaises(Отказ) as отказ:
				релизы.опубликовать(р, None, "Administrator")
			self.assertEqual(отказ.exception.код, релизы.РЕЛИЗ_НЕ_СХОДИТСЯ)
			self.assertFalse(frappe.db.exists("LMS Course", {"course_key": р["course"]["key"]}))
			return отказ.exception.подробности["problems"]

		for случай, правка, ожидаемое in (
			(
				"слаг цели не из каталога",
				lambda р: р["lessons"][1].update(competencies=[{"competency": "no-such-skill", "target": 1}]),
				[{"code": "broken_ref", "where": "lessons[l-2].competencies", "key": "no-such-skill"}],
			),
			(
				# Сравнение различает регистр, хотя база — нет.
				"слаг в другом регистре",
				lambda р: р["lessons"][0].update(
					competencies=[{"competency": self.к["a"].upper(), "target": 1}]
				),
				[{"code": "broken_ref", "where": "lessons[l-1].competencies", "key": self.к["a"].upper()}],
			),
			(
				"контекст курса по компетенции не из каталога",
				lambda р: р["agent"]["lessons"]["l-3"]["competencies"].update({"no-such-skill": "Контекст"}),
				[{"code": "broken_ref", "where": "agent.lessons.l-3.competencies", "key": "no-such-skill"}],
			),
			(
				"контекст курса по компетенции каталога вне карты курса",
				lambda р: р["agent"]["lessons"]["l-3"]["competencies"].update(
					{f"lonely-{self.суффикс}": "Контекст"}
				),
				[
					{
						"code": "broken_ref",
						"where": "agent.lessons.l-3.competencies",
						"key": f"lonely-{self.суффикс}",
					}
				],
			),
			(
				"не последний выпуск каталога",
				lambda р: р.update(catalog=последняя - 1),
				[
					{
						"code": read.ВЕРСИЯ_УСТАРЕЛА,
						"where": "catalog",
						"version": последняя - 1,
						"latest": последняя,
						"message": "релиз собран не по последнему выпуску каталога",
					}
				],
			),
			(
				"неопубликованная версия каталога",
				lambda р: р.update(catalog=10**6),
				[
					{
						"code": read.ВЕРСИЯ_НЕИЗВЕСТНА,
						"where": "catalog",
						"version": 10**6,
						"message": "выпуск каталога этой версии не опубликован",
					}
				],
			),
		):
			with self.subTest(случай):
				self.assertEqual(проблемы(правка), ожидаемое)
