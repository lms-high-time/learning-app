# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Патч `prune_projection`: главы и уроки, снятые прежними версиями, —
убраны (learning-services#514)."""

import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.releases import projection, service
from lms_frappe_app.patches.v0_1 import patch_log, prune_projection
from lms_frappe_app.tests.release_sample import добавить_главу, пример_релиза
from lms_frappe_app.tests.sample_data import создать_занятие, создать_урок, создать_ученика, урок_релиза
from lms_frappe_app.tests.test_old_model_patches import отметить_патч

ПРИЛОЖЕНИЕ = Path(__file__).resolve().parents[1]


class IntegrationTestПатчУборки(IntegrationTestCase):
	"""Курс, опубликованный до выкатки тремя версиями: вторая сняла главу `ch-3`
	с уроками `l-4` и `l-5`, третья — урок `l-3` с главой `ch-2`. По `l-4` есть
	занятие; у `l-3` нет ключа — запись проиграла соответствие ключа."""

	def setUp(self):
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		ключ = f"prune-patch-{суффикс}"
		полный = добавить_главу(пример_релиза(ключ), "ch-3", ["l-4", "l-5"])
		# До выкатки публикация снятое не убирала.
		with patch.object(projection, "убрать"):
			self.курс = service.опубликовать(полный, None, "Administrator")["course"]
			self.уроки = {к: урок_релиза(self.курс, к) for к in ("l-3", "l-4", "l-5")}
			self.главы = {
				к: frappe.db.get_value("Course Lesson", self.уроки[у], "chapter")
				for к, у in (("ch-2", "l-3"), ("ch-3", "l-4"))
			}
			self.занятие = создать_занятие(
				создать_ученика(f"prune-patch-{суффикс}@example.com"), self.уроки["l-4"]
			)
			service.опубликовать(пример_релиза(ключ), None, "Administrator")
			без_третьего = пример_релиза(ключ)
			без_третьего["chapters"] = без_третьего["chapters"][:1]
			без_третьего["lessons"] = без_третьего["lessons"][:2]
			del без_третьего["agent"]["lessons"]["l-3"]
			service.опубликовать(без_третьего, None, "Administrator")
		frappe.db.set_value("Course Lesson", self.уроки["l-3"], "lesson_key", None)
		self.чужой = создать_урок(f"Урок курса без релиза {суффикс}")
		frappe.db.delete("Lesson Reference", {"lesson": self.чужой})
		for патч in prune_projection.ЖДЁТ:
			отметить_патч(патч, "выполнен")

	def есть(self) -> dict[str, bool]:
		return {
			**{к: bool(frappe.db.exists("Course Lesson", имя)) for к, имя in self.уроки.items()},
			**{к: bool(frappe.db.exists("Course Chapter", имя)) for к, имя in self.главы.items()},
		}

	def выполнить(self) -> str:
		with redirect_stdout(io.StringIO()) as вывод:
			prune_projection.execute()
		return вывод.getvalue()

	def test_удаляет_накопленное_без_ссылок(self):
		вывод = self.выполнить()

		self.assertEqual(self.есть(), {"l-3": False, "l-4": True, "l-5": False, "ch-2": False, "ch-3": True})
		self.assertIn(
			f"prune_projection: {self.курс} — удалено глав: 1, уроков: 2; "
			"со ссылками осталось глав: 1, уроков: 1",
			вывод,
		)
		префикс = f"prune_projection: {self.курс} — "
		for строка in (
			f"урок {self.уроки['l-3']} «Урок третий», ключ нет: удалена",
			f"урок {self.уроки['l-5']} «Урок l-5», ключ l-5: удалена",
			f"глава {self.главы['ch-2']} «Глава вторая», ключ ch-2: удалена",
			f"урок {self.уроки['l-4']} «Урок l-4», ключ l-4: оставлена, держат Agent Learning Session — 1",
			f"глава {self.главы['ch-3']} «Глава ch-3», ключ ch-3: оставлена, держат Course Lesson — 1",
		):
			with self.subTest(строка=строка):
				self.assertIn(префикс + строка, вывод)
		self.assertTrue(frappe.db.exists("Course Lesson", self.чужой))
		self.assertEqual(
			frappe.db.get_value("Agent Learning Session", self.занятие, "lesson"), self.уроки["l-4"]
		)

	def test_повтор_удаляет_то_что_ссылки_отпустили(self):
		self.выполнить()
		frappe.db.delete("Agent Learning Session", self.занятие)

		вывод = self.выполнить()

		self.assertEqual(self.есть(), dict.fromkeys(self.есть(), False))
		self.assertIn(
			f"prune_projection: {self.курс} — удалено глав: 1, уроков: 1; "
			"со ссылками осталось глав: 0, уроков: 0",
			вывод,
		)

	def test_ждёт_освобождения_и_его_патчи(self):
		for патч in prune_projection.ЖДЁТ:
			with self.subTest(патч=патч):
				отметить_патч(патч, "пропущен")
				вывод = self.выполнить()
				отметить_патч(патч, "выполнен")

				self.assertEqual(self.есть(), dict.fromkeys(self.есть(), True))
				self.assertIn(f"не выполнены патчи {патч}", вывод)

	def test_идёт_после_освобождения_и_его_патчей(self):
		self.assertEqual(prune_projection.ЖДЁТ[-1], "free_release_content")
		строки = (ПРИЛОЖЕНИЕ / "patches.txt").read_text(encoding="utf-8").splitlines()
		свой = строки.index(patch_log.полное_имя("prune_projection"))
		for раньше in prune_projection.ЖДЁТ:
			with self.subTest(раньше=раньше):
				self.assertLess(строки.index(patch_log.полное_имя(раньше)), свой)
