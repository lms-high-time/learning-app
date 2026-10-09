# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Прогресс записи на курс из релиза — по урокам программы (learning-services#522)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from lms.lms.utils import recalculate_course_progress

from lms_frappe_app.agent_learning import course_progress, quiz, reset, signals
from lms_frappe_app.agent_learning.releases import service
from lms_frappe_app.patches.v0_1 import release_course_progress
from lms_frappe_app.tests.release_sample import добавить_главу, пример_релиза
from lms_frappe_app.tests.sample_data import (
	зачислить_на_курс,
	создать_занятие,
	создать_куратора,
	создать_урок,
	создать_ученика,
	урок_релиза,
)


def без(релиз: dict, *ключи: str) -> dict:
	"""Релиз без уроков `ключи`; глава, оставшаяся без уроков, уходит тоже."""
	релиз["lessons"] = [у for у in релиз["lessons"] if у["key"] not in ключи]
	for глава in релиз["chapters"]:
		глава["lessons"] = [у for у in глава["lessons"] if у not in ключи]
	релиз["chapters"] = [г for г in релиз["chapters"] if г["lessons"]]
	for ключ in ключи:
		del релиз["agent"]["lessons"][ключ]
	return релиз


class IntegrationTestПрогрессКурса(IntegrationTestCase):
	"""Образец: `ch-1` — `l-1`, `l-2`; `ch-2` — `l-3`."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"progress-{суффикс}@example.com")
		self.ученик = создать_ученика(f"progress-pupil-{суффикс}@example.com")
		self.ключ = f"progress-{суффикс}"
		self.курс = self.опубликовать(пример_релиза(self.ключ))["course"]
		self.уроки = {ключ: урок_релиза(self.курс, ключ) for ключ in ("l-1", "l-2", "l-3")}
		зачислить_на_курс(self.ученик, self.курс)

	def опубликовать(self, релиз: dict) -> dict:
		return service.опубликовать(релиз, None, self.куратор)

	def пройти(self, ключ: str, ученик: str | None = None) -> None:
		пройти(ученик or self.ученик, self.уроки[ключ])

	def прогресс(self) -> float:
		return frappe.db.get_value("LMS Enrollment", {"member": self.ученик, "course": self.курс}, "progress")

	def test_пройденный_снятый_урок_не_завышает_прогресс(self):
		self.пройти("l-1")
		self.пройти("l-2")
		self.опубликовать(без(пример_релиза(self.ключ), "l-2"))
		self.assertTrue(frappe.db.exists("Course Lesson", self.уроки["l-2"]))

		self.пройти("l-3")

		self.assertEqual(self.прогресс(), 100)

	def test_программа_сводится_по_верной_доле(self):
		"""Контроллер Learning сводит программу по своей доле до хука — хук сводит заново."""
		программа = frappe.get_doc(
			{
				"doctype": "LMS Program",
				"title": f"Программа {self.ключ}",
				"program_courses": [{"course": self.курс}],
				"program_members": [{"member": self.ученик}],
			}
		).insert(ignore_permissions=True)
		self.пройти("l-1")
		self.пройти("l-2")
		self.опубликовать(без(пример_релиза(self.ключ), "l-2"))

		self.пройти("l-3")

		self.assertEqual(
			frappe.db.get_value(
				"LMS Program Member", {"parent": программа.name, "member": self.ученик}, "progress"
			),
			100,
		)

	def test_публикация_пересчитывает_долю_по_новому_оглавлению(self):
		"""Снятый урок со следами не удаляется, и Learning долю сам не пересчитывает."""
		self.пройти("l-1")
		self.пройти("l-2")
		self.assertAlmostEqual(self.прогресс(), 66.667)

		self.опубликовать(без(пример_релиза(self.ключ), "l-2"))

		self.assertEqual(self.прогресс(), 50)

	def test_снятый_непройденный_урок_не_держит_курс_незавершённым(self):
		"""`l-3` держит прогресс другого ученика: урок остаётся вне программы."""
		другой = создать_ученика(f"other-{self.ключ}@example.com")
		зачислить_на_курс(другой, self.курс)
		self.пройти("l-3", другой)
		self.пройти("l-1")
		self.пройти("l-2")

		self.опубликовать(без(пример_релиза(self.ключ), "l-3"))

		self.assertTrue(frappe.db.exists("Course Lesson", self.уроки["l-3"]))
		self.assertEqual(self.прогресс(), 100)

	def test_патч_приводит_завышенную_долю_к_100(self):
		self.пройти("l-1")
		запись = frappe.db.get_value("LMS Enrollment", {"member": self.ученик, "course": self.курс})
		frappe.db.set_value("LMS Enrollment", запись, "progress", 150)

		release_course_progress.execute()

		self.assertEqual(self.прогресс(), 100)

	def test_патч_поправляет_устаревшую_долю(self):
		self.пройти("l-1")
		запись = frappe.db.get_value("LMS Enrollment", {"member": self.ученик, "course": self.курс})
		frappe.db.set_value("LMS Enrollment", запись, "progress", 80)

		release_course_progress.execute()

		self.assertAlmostEqual(self.прогресс(), 33.333)

	def test_добавленный_урок_не_снимает_100(self):
		"""Ни пересчёт после публикации, ни пересчёт Learning по новому оглавлению."""
		for ключ in ("l-1", "l-2", "l-3"):
			self.пройти(ключ)
		self.assertEqual(self.прогресс(), 100)

		self.опубликовать(добавить_главу(пример_релиза(self.ключ), "ch-3", ["l-4"]))
		self.assertEqual(self.прогресс(), 100)
		recalculate_course_progress(self.курс, self.ученик)

		self.assertEqual(self.прогресс(), 100)
		self.assertEqual(signals.осталось_уроков(self.ученик, self.курс), 1)

	def test_снятая_отметка_опускает_100(self):
		for ключ in ("l-1", "l-2", "l-3"):
			self.пройти(ключ)
		отметка = frappe.db.get_value(
			"LMS Course Progress", {"member": self.ученик, "lesson": self.уроки["l-3"]}
		)

		frappe.delete_doc("LMS Course Progress", отметка, ignore_permissions=True, force=True)

		self.assertAlmostEqual(self.прогресс(), 66.667)
		self.assertFalse(frappe.flags.get(course_progress.СНИМАЕТСЯ))

	def test_снятый_статус_опускает_100(self):
		"""Переход отметки из `Complete` в другой статус — через `validate`."""
		for ключ in ("l-1", "l-2", "l-3"):
			self.пройти(ключ)
		отметка = frappe.get_doc("LMS Course Progress", {"member": self.ученик, "lesson": self.уроки["l-3"]})

		отметка.status = "Incomplete"
		отметка.save(ignore_permissions=True)

		self.assertAlmostEqual(self.прогресс(), 66.667)
		self.assertFalse(frappe.flags.get(course_progress.СНИМАЕТСЯ))

	def test_повторное_прохождение_возвращает_долю(self):
		"""Отметку снял администратор, урок закрыт агентом снова."""
		for ключ in ("l-1", "l-2", "l-3"):
			self.пройти(ключ)
		отметка = frappe.get_doc("LMS Course Progress", {"member": self.ученик, "lesson": self.уроки["l-3"]})
		отметка.status = "Incomplete"
		отметка.save(ignore_permissions=True)

		quiz.отметить_урок_пройденным(
			frappe.get_doc("Agent Learning Session", создать_занятие(self.ученик, self.уроки["l-3"]))
		)

		self.assertEqual(frappe.db.get_value("LMS Course Progress", отметка.name, "status"), "Complete")
		self.assertEqual(self.прогресс(), 100)

	def test_снятая_отметка_опускает_100_при_совпавшей_доле_learning(self):
		"""Доля Learning после снятия совпала с удержанными 100 — записи на курс
		Learning не пишет, и сверяет её снятие отметки."""
		for ключ in ("l-1", "l-2", "l-3"):
			self.пройти(ключ)
		self.опубликовать(без(пример_релиза(self.ключ), "l-2", "l-3"))
		self.опубликовать(добавить_главу(без(пример_релиза(self.ключ), "l-2", "l-3"), "ch-3", ["l-4"]))
		self.assertEqual(self.прогресс(), 100)
		отметка = frappe.db.get_value(
			"LMS Course Progress", {"member": self.ученик, "lesson": self.уроки["l-1"]}
		)

		frappe.delete_doc("LMS Course Progress", отметка, ignore_permissions=True, force=True)

		self.assertEqual(self.прогресс(), 0)
		self.assertFalse(frappe.flags.get(course_progress.СНИМАЕТСЯ))

	def test_сброс_даёт_0(self):
		"""Запись на курс сброс снимает; программа сводится по последней доле — 0."""
		программа = frappe.get_doc(
			{
				"doctype": "LMS Program",
				"title": f"Программа сброса {self.ключ}",
				"program_courses": [{"course": self.курс}],
				"program_members": [{"member": self.ученик}],
			}
		).insert(ignore_permissions=True)
		for ключ in ("l-1", "l-2", "l-3"):
			self.пройти(ключ)

		reset.сбросить_прогресс(self.ученик, self.курс, "Administrator")

		self.assertEqual(
			frappe.db.get_value(
				"LMS Program Member", {"parent": программа.name, "member": self.ученик}, "progress"
			),
			0,
		)
		зачислить_на_курс(self.ученик, self.курс)
		self.assertEqual(self.прогресс(), 0)

	def test_снятый_урок_не_в_остатке_к_сроку(self):
		другой = создать_ученика(f"other-{self.ключ}@example.com")
		зачислить_на_курс(другой, self.курс)
		self.пройти("l-3", другой)

		self.опубликовать(без(пример_релиза(self.ключ), "l-3"))

		self.assertEqual(signals.осталось_уроков(self.ученик, self.курс), 2)

	def test_хук_не_пишет_совпавшую_долю(self):
		self.пройти("l-1")
		запись = frappe.get_doc("LMS Enrollment", {"member": self.ученик, "course": self.курс})
		изменена = frappe.db.get_value("LMS Enrollment", запись.name, "modified")

		with patch.object(course_progress, "update_program_progress") as сводка:
			course_progress.сверить(запись)

		сводка.assert_not_called()
		self.assertAlmostEqual(self.прогресс(), 33.333)
		self.assertEqual(frappe.db.get_value("LMS Enrollment", запись.name, "modified"), изменена)

	def test_пустое_оглавление_курса_из_релиза_даёт_ноль(self):
		self.пройти("l-1")
		frappe.db.delete("Chapter Reference", {"parent": self.курс})
		запись = frappe.get_doc("LMS Enrollment", {"member": self.ученик, "course": self.курс})

		course_progress.сверить(запись)

		self.assertEqual(self.прогресс(), 0)

	def test_курс_без_релиза_считает_learning(self):
		урок = создать_урок(f"Без релиза {self.ключ}")
		курс = frappe.db.get_value("Course Lesson", урок, "course")
		зачислить_на_курс(self.ученик, курс)
		пройти(self.ученик, урок)
		frappe.db.delete("Lesson Reference", {"lesson": урок})
		запись = frappe.get_doc("LMS Enrollment", {"member": self.ученик, "course": курс})

		course_progress.сверить(запись)

		self.assertEqual(frappe.db.get_value("LMS Enrollment", запись.name, "progress"), 100)


def пройти(ученик: str, урок: str) -> None:
	frappe.get_doc(
		{"doctype": "LMS Course Progress", "member": ученик, "lesson": урок, "status": "Complete"}
	).insert(ignore_permissions=True)
