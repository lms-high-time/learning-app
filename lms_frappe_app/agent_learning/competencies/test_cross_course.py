# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Компетенции каталога в нескольких курсах (learning-services#826).

Два курса на одном выпуске каталога: у первого урока обоих цель по базовой
компетенции `a` (уровень 2), у третьего урока курса B — по интегративной `whole`
из `a` и `b`.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.catalog import service as каталог
from lms_frappe_app.agent_learning.competencies import evidence
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.tests.catalog_sample import ключи_примера, поверх_опубликованного, пример_каталога
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	занятие_релиза,
	зачислить_на_курс,
	курс_из_релиза,
	политика_по_умолчанию,
	создать_ученика,
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

	def test_свидетельство_по_карте_курса_с_версией(self):
		"""Компетенция — из карты курса: цели и их составляющие по каталогу; запись несёт версию
		компетенции."""
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
