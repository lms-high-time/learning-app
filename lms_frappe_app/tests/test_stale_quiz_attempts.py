# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Патч `stale_quiz_attempts`: открытые попытки — с порогом и на действующем
релизе (learning-services#514)."""

import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import release_quiz
from lms_frappe_app.agent_learning.constants import (
	АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ,
	ПОПЫТКА_АННУЛИРОВАНА,
	ПОПЫТКА_ИДЁТ,
	ПРОВЕРКА_ПОПЫТКА_АННУЛИРОВАНА,
	ПРОВЕРКА_ПОПЫТКА_ПЕРЕНЕСЕНА,
)
from lms_frappe_app.agent_learning.releases import retention
from lms_frappe_app.agent_learning.releases import service as релизы
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.patches.v0_1 import patch_log, stale_quiz_attempts
from lms_frappe_app.tests.release_sample import релиз_двух_целей
from lms_frappe_app.tests.sample_data import (
	зачислить,
	политика_по_умолчанию,
	создать_занятие,
	создать_ученика,
)

ПОПЫТКА = "Agent Quiz Attempt"
ПРИЛОЖЕНИЕ = Path(__file__).resolve().parents[1]


class IntegrationTestПатчОткрытыхПопыток(IntegrationTestCase):
	"""Два курса, у каждого — открытая попытка на первой версии, а действует вторая
	(сайт до выкатки: публикация попыток ещё не переносила). У курса «тот же» во
	второй версии квиз не менялся, у курса «другой» — изменился текст вопроса.
	Третья попытка — другого ученика на действующем релизе, без порога."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"stale-{суффикс}@example.com")
		self.попытки = {}
		self.курсы = {}
		for номер, вид in enumerate(("тот же", "другой")):
			релиз = релиз_двух_целей(f"stale-{номер}-{суффикс}", порог=60)
			курс = релизы.опубликовать(релиз, None, "Administrator")["course"]
			self.курсы[вид] = курс
			прежний = frappe.db.get_value("LMS Course", курс, "active_release")
			self.попытки[вид] = self.начать(курс)
			релиз["lessons"][0]["title"] = "Вторая версия"
			if вид == "другой":
				релиз["lessons"][0]["quiz"]["questions"][0]["text"] = "Другая ситуация"
			# До выкатки: публикация не освобождала прежнюю версию, попытка
			# осталась на ней и без порога.
			with patch.object(retention, "освободить_прежние"):
				релизы.опубликовать(релиз, None, "Administrator")
			frappe.db.set_value(
				ПОПЫТКА,
				self.попытки[вид],
				{"release": прежний, "status": ПОПЫТКА_ИДЁТ, "cancel_reason": None, "finished_at": None},
			)
			frappe.db.delete(
				"Agent Quiz Event", {"attempt": self.попытки[вид], "kind": ("like", "Attempt %")}
			)
		self.на_действующем = self.начать(
			self.курсы["тот же"], создать_ученика(f"stale-2-{суффикс}@example.com")
		)
		frappe.db.set_value(
			ПОПЫТКА, {"name": ("in", [*self.попытки.values(), self.на_действующем])}, "pass_percentage", 0
		)

	def начать(self, курс: str, ученик: str | None = None) -> str:
		ученик = ученик or self.ученик
		run = прохождения.прохождение(ученик, курс, "l-1")
		зачислить(ученик, run.lesson)
		return release_quiz.начать(run, создать_занятие(ученик, run.lesson))["attempt"]

	def попытка(self, имя: str):
		return frappe.db.get_value(
			ПОПЫТКА, имя, ["status", "cancel_reason", "release", "pass_percentage"], as_dict=True
		)

	def события(self, имя: str) -> list[str]:
		return frappe.get_all(
			"Agent Quiz Event", filters={"attempt": имя, "kind": ("like", "Attempt %")}, pluck="kind"
		)

	def test_порог_всем_открытым_перенос_и_аннулирование_один_раз(self):
		with redirect_stdout(io.StringIO()) as вывод:
			stale_quiz_attempts.execute()

		действующий = frappe.db.get_value("LMS Course", self.курсы["тот же"], "active_release")
		перенесена = self.попытка(self.попытки["тот же"])
		self.assertEqual(
			(перенесена.status, перенесена.release, перенесена.pass_percentage),
			(ПОПЫТКА_ИДЁТ, действующий, 60),
		)
		аннулирована = self.попытка(self.попытки["другой"])
		self.assertEqual(
			(аннулирована.status, аннулирована.cancel_reason, аннулирована.pass_percentage),
			(ПОПЫТКА_АННУЛИРОВАНА, АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ, 60),
		)
		self.assertEqual(self.попытка(self.на_действующем).pass_percentage, 60)
		self.assertEqual(self.события(self.попытки["тот же"]), [ПРОВЕРКА_ПОПЫТКА_ПЕРЕНЕСЕНА])
		self.assertEqual(self.события(self.попытки["другой"]), [ПРОВЕРКА_ПОПЫТКА_АННУЛИРОВАНА])
		self.assertIn(
			f"stale_quiz_attempts: {self.курсы['тот же']} — порог записан открытым попыткам: 2; "
			"перенесено 1, аннулировано 0",
			вывод.getvalue(),
		)
		self.assertIn(
			f"stale_quiz_attempts: {self.курсы['другой']} — порог записан открытым попыткам: 1; "
			"перенесено 0, аннулировано 1",
			вывод.getvalue(),
		)

		было = {имя: self.попытка(имя) for имя in [*self.попытки.values(), self.на_действующем]}
		with redirect_stdout(io.StringIO()) as повтор:
			stale_quiz_attempts.execute()

		self.assertEqual({имя: self.попытка(имя) for имя in было}, было)
		self.assertEqual(self.события(self.попытки["тот же"]), [ПРОВЕРКА_ПОПЫТКА_ПЕРЕНЕСЕНА])
		self.assertIn(
			f"stale_quiz_attempts: {self.курсы['тот же']} — порог записан открытым попыткам: 2; "
			"перенесено 0, аннулировано 0",
			повтор.getvalue(),
		)
		self.assertNotIn(self.курсы["другой"], повтор.getvalue())

	def test_идёт_после_заполняющих_и_до_освобождения(self):
		строки = (ПРИЛОЖЕНИЕ / "patches.txt").read_text(encoding="utf-8").splitlines()
		свой = строки.index(patch_log.полное_имя("stale_quiz_attempts"))
		for раньше in ("release_record_keys", "run_objective_texts", "note_lesson_keys"):
			with self.subTest(раньше=раньше):
				self.assertLess(строки.index(patch_log.полное_имя(раньше)), свой)
		self.assertLess(свой, строки.index(patch_log.полное_имя("free_release_content")))
