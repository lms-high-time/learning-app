# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Патч `run_objective_texts`: тексты целей — в строках прохождений
(learning-services#514)."""

import io
from contextlib import redirect_stdout
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.releases import retention
from lms_frappe_app.agent_learning.releases import service as релизы
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.patches.v0_1 import run_objective_texts
from lms_frappe_app.tests.release_sample import релиз_двух_целей
from lms_frappe_app.tests.sample_data import создать_ученика

ПРОХОЖДЕНИЕ = "Agent Lesson Run"
ЦЕЛЬ = "Agent Lesson Run Objective"
ПЕРВЫЕ = {"l-1-D1": "Цель с обязательными пунктами", "l-1-D2": "Цель без обязательных пунктов"}


class IntegrationTestТекстыЦелейПрохождений(IntegrationTestCase):
	"""Живое и архивное прохождения на первой версии курса; вторая переписала
	текст цели `l-1-D1` и сняла `l-1-D2`, а фоновая сверка их ещё не догнала."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ключ = ключ = f"run-texts-{суффикс}"
		первый = релизы.опубликовать(релиз_двух_целей(ключ), None, "Administrator")
		self.курс, self.первый = первый["course"], первый["release"]
		self.прохождения = [
			прохождения.прохождение(
				создать_ученика(f"run-texts-{номер}-{суффикс}@example.com"), self.курс, "l-1"
			).name
			for номер in (1, 2)
		]
		frappe.db.set_value(
			ПРОХОЖДЕНИЕ,
			self.прохождения[1],
			{"student": None, "archived_at": now_datetime()},
			update_modified=False,
		)
		второй = релиз_двух_целей(ключ)
		второй["lessons"][0]["objectives"][0]["text"] = "Цель, переписанная автором"
		второй["lessons"][0]["objectives"].pop()
		del второй["agent"]["lessons"]["l-1"]["items"]["return:R1"]
		# Сайт до патча: публикация не освобождала прежнюю версию, а текстов в
		# строках прохождений нет.
		with patch.object(frappe, "enqueue"), patch.object(retention, "освободить_прежние"):
			self.второй = релизы.опубликовать(второй, None, "Administrator")["release"]
		frappe.db.set_value(ЦЕЛЬ, {"parent": ("in", self.прохождения)}, "text", None, update_modified=False)

	def тексты(self) -> list[dict[str, str | None]]:
		return [
			dict(
				frappe.get_all(ЦЕЛЬ, filters={"parent": имя}, fields=["objective_key", "text"], as_list=True)
			)
			for имя in self.прохождения
		]

	def test_по_релизу_прохождения_архивные_тоже(self):
		with redirect_stdout(io.StringIO()) as вывод:
			run_objective_texts.execute()
		with redirect_stdout(io.StringIO()) as повтор:
			run_objective_texts.execute()

		self.assertEqual(self.тексты(), [ПЕРВЫЕ, ПЕРВЫЕ])
		self.assertIn(
			f"run_objective_texts: {self.курс} — текстов записано: релиз прохождения 4, "
			"последний релиз с целью 0; без текста осталось 0",
			вывод.getvalue(),
		)
		self.assertNotIn(self.курс, повтор.getvalue())

	def test_без_индекса_релиза_прохождения_по_последнему_релизу_с_целью(self):
		frappe.db.delete("Agent Release Objective", {"parent": self.первый})

		run_objective_texts.заполнить()

		# Снятой цели нет ни в одном оставшемся индексе — текст взять неоткуда.
		ожидаемо = {"l-1-D1": "Цель, переписанная автором", "l-1-D2": None}
		self.assertEqual(self.тексты(), [ожидаемо, ожидаемо])

	def test_цель_снятая_до_патча_получает_последний_текст(self):
		"""Сверка довела прохождения до второй версии, где цели `l-1-D2` уже нет:
		её текст — из первой, последней версии курса с этой целью."""
		frappe.db.set_value(
			ПРОХОЖДЕНИЕ, {"name": ("in", self.прохождения)}, "release", self.второй, update_modified=False
		)

		записано = run_objective_texts.заполнить()

		ожидаемо = {"l-1-D1": "Цель, переписанная автором", "l-1-D2": ПЕРВЫЕ["l-1-D2"]}
		self.assertEqual(self.тексты(), [ожидаемо, ожидаемо])
		self.assertEqual(
			записано, {"релиз прохождения": {self.курс: 2}, "последний релиз с целью": {self.курс: 2}}
		)

	def test_из_нескольких_версий_с_целью_главнее_свежая(self):
		третий = релиз_двух_целей(self.ключ)
		третий["lessons"][0]["objectives"][1]["text"] = "Цель вернулась другой"
		with patch.object(frappe, "enqueue"), patch.object(retention, "освободить_прежние"):
			релизы.опубликовать(третий, None, "Administrator")
		frappe.db.set_value(
			ПРОХОЖДЕНИЕ, {"name": ("in", self.прохождения)}, "release", self.второй, update_modified=False
		)

		run_objective_texts.заполнить()

		self.assertEqual([т["l-1-D2"] for т in self.тексты()], ["Цель вернулась другой"] * 2)

	def test_заполненный_текст_не_трогается_и_повтор_ничего_не_меняет(self):
		frappe.db.set_value(
			ЦЕЛЬ,
			{"parent": self.прохождения[0], "objective_key": "l-1-D1"},
			"text",
			"Текст сверки",
			update_modified=False,
		)

		run_objective_texts.заполнить()
		после_первого = self.тексты()
		повтор = run_objective_texts.заполнить()

		self.assertEqual(после_первого[0], {**ПЕРВЫЕ, "l-1-D1": "Текст сверки"})
		self.assertEqual(self.тексты(), после_первого)
		self.assertEqual(повтор, {"релиз прохождения": {}, "последний релиз с целью": {}})
