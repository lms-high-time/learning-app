# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Патч `free_release_content`: содержимое прежних версий — освобождено
(learning-services#514)."""

import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.releases import index, retention
from lms_frappe_app.agent_learning.releases import service as релизы
from lms_frappe_app.patches.v0_1 import free_release_content, patch_log
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.test_old_model_patches import отметить_патч

ПРИЛОЖЕНИЕ = Path(__file__).resolve().parents[1]


class IntegrationTestПатчОсвобождения(IntegrationTestCase):
	"""Два курса по три версии, опубликованные до выкатки: содержимое у всех."""

	def setUp(self):
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.версии: dict[str, list[str]] = {}
		# До выкатки публикация прежних версий не освобождала.
		with patch.object(retention, "освободить_прежние"):
			for номер in range(2):
				релиз = пример_релиза(f"free-{номер}-{суффикс}")
				имена = []
				for издание in range(3):
					релиз["course"]["title"] = f"Пример курса, издание {издание}"
					имена.append(релизы.опубликовать(релиз, None, "Administrator")["release"])
				self.версии[frappe.db.get_value(index.РЕЛИЗ, имена[0], "course")] = имена
		for патч in free_release_content.ЖДЁТ:
			отметить_патч(патч, "выполнен")

	def со_снимком(self) -> dict[str, list[str]]:
		return {
			курс: [
				имя for имя in имена if frappe.db.exists(index.РЕЛИЗ, {"name": имя, "snapshot": ("is", "set")})
			]
			for курс, имена in self.версии.items()
		}

	def строк_индекса(self, релиз: str) -> int:
		return sum(
			frappe.db.count(таблица, {"parenttype": index.РЕЛИЗ, "parent": релиз})
			for таблица in retention.ТАБЛИЦЫ_ИНДЕКСА
		)

	def выполнить(self) -> str:
		with redirect_stdout(io.StringIO()) as вывод:
			free_release_content.execute()
		return вывод.getvalue()

	def test_освобождает_прежние_версии_один_раз(self):
		вывод = self.выполнить()

		self.assertEqual(self.со_снимком(), {курс: имена[-1:] for курс, имена in self.версии.items()})
		for курс, имена in self.версии.items():
			with self.subTest(курс=курс):
				self.assertEqual([self.строк_индекса(имя) for имя in имена[:2]], [0, 0])
				self.assertTrue(self.строк_индекса(имена[-1]))
				self.assertIn(
					f"free_release_content: {курс} — освобождено версий: 2 ({', '.join(sorted(имена[:2]))})",
					вывод,
				)

		with patch.object(retention, "_освободить") as освободить:
			повтор = self.выполнить()
		освободить.assert_not_called()
		for курс in self.версии:
			self.assertNotIn(курс, повтор)

	def test_ждёт_заполняющие_патчи(self):
		for патч in free_release_content.ЖДЁТ:
			with self.subTest(патч=патч):
				отметить_патч(патч, "пропущен")
				вывод = self.выполнить()
				отметить_патч(патч, "выполнен")

				self.assertEqual(self.со_снимком(), self.версии)
				self.assertIn(f"не выполнены патчи {патч}", вывод)

	def test_идёт_последним_после_заполняющих_патчей(self):
		строки = (ПРИЛОЖЕНИЕ / "patches.txt").read_text(encoding="utf-8").splitlines()
		свой = строки.index(patch_log.полное_имя("free_release_content"))
		for раньше in free_release_content.ЖДЁТ:
			with self.subTest(раньше=раньше):
				self.assertLess(строки.index(patch_log.полное_имя(раньше)), свой)
		self.assertEqual(строки[-1], patch_log.полное_имя("free_release_content"))
