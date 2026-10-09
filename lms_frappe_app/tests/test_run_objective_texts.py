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
		ключ = f"run-texts-{суффикс}"
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
		with patch.object(frappe, "enqueue"):
			релизы.опубликовать(второй, None, "Administrator")
		# Сайт до патча: текстов в строках прохождений нет.
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

		self.assertEqual(self.тексты(), [ПЕРВЫЕ, ПЕРВЫЕ])
		self.assertIn("run_objective_texts:", вывод.getvalue())

	def test_без_индекса_релиза_прохождения_по_действующему(self):
		frappe.db.delete("Agent Release Objective", {"parent": self.первый})

		run_objective_texts.заполнить()

		# Снятой цели в действующем релизе нет — текст взять неоткуда.
		ожидаемо = {"l-1-D1": "Цель, переписанная автором", "l-1-D2": None}
		self.assertEqual(self.тексты(), [ожидаемо, ожидаемо])

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
		self.assertEqual(повтор, {"релиз прохождения": 0, "действующий релиз": 0})
