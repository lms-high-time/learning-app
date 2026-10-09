# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Патч `note_lesson_keys`: ключ урока — в заметке автора, колонка `lesson`
уходит (learning-services#514).

Колонки `lesson` схема не знает, и на свежем сайте её нет: схема заметок и
сырое чтение колонки подменены (`Схема`), DDL записывается, а не
выполняется. Записи ключей идут по-настоящему и откатываются с тестом.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.patches.v0_1 import note_lesson_keys
from lms_frappe_app.tests.sample_data import курс_из_релиза, урок_релиза
from lms_frappe_app.tests.test_old_model_patches import Схема, выполнить, отметить_патч

ЗАМЕТКА = note_lesson_keys.ЗАМЕТКА
ЧТЕНИЕ_УРОКОВ = f"SELECT name, lesson FROM `tab{ЗАМЕТКА}`"
DDL = f"ALTER TABLE `tab{ЗАМЕТКА}` DROP COLUMN IF EXISTS `lesson`"


class IntegrationTestКлючиУроковЗаметок(IntegrationTestCase):
	"""Курс из релиза и заметки, какими их оставила прежняя версия: урок — ссылкой."""

	def setUp(self):
		self.курс, self.релиз = курс_из_релиза()
		первый, третий = урок_релиза(self.курс, "l-1"), урок_релиза(self.курс, "l-3")
		frappe.db.set_value("Course Lesson", третий, "lesson_key", None)
		self.пустая = self.заметка("lesson.l-1", None)
		self.заполненная = self.заметка("lesson.l-2", "l-2")
		self.урок_без_ключа = self.заметка("lesson.l-3", None)
		self.без_урока = self.заметка("course", None)
		self.уроки = [(self.пустая, первый), (self.заполненная, первый), (self.урок_без_ключа, третий)]

	def заметка(self, target: str, ключ: str | None) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": ЗАМЕТКА,
					"course": self.курс,
					"release": self.релиз,
					"lesson_key": ключ,
					"target": target,
					"status": "open",
					"via": "author",
					"text": "Заметка",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def старый_сайт(self) -> Схема:
		return Схема({ЗАМЕТКА: set(frappe.db.get_table_columns(ЗАМЕТКА)) | {"lesson"}})

	def ключи(self) -> dict[str, str | None]:
		return dict(
			frappe.get_all(
				ЗАМЕТКА, filters={"course": self.курс}, fields=["name", "lesson_key"], as_list=True
			)
		)

	def test_поля_lesson_нет_в_схеме(self):
		"""Страховка от удаления живой колонки."""
		self.assertFalse(frappe.get_meta(ЗАМЕТКА).has_field("lesson"))

	def test_заполняет_пустые_ключи_и_удаляет_колонку(self):
		отметить_патч(note_lesson_keys.ЖДЁТ, "выполнен")
		схема = self.старый_сайт()

		with схема.подменить({ЧТЕНИЕ_УРОКОВ: self.уроки}):
			первый = выполнить(note_lesson_keys)
			второй = выполнить(note_lesson_keys)

		self.assertEqual(
			self.ключи(),
			{self.пустая: "l-1", self.заполненная: "l-2", self.урок_без_ключа: None, self.без_урока: None},
		)
		self.assertEqual(схема.ddl, [DDL])
		self.assertNotIn("lesson", схема.таблицы[ЗАМЕТКА])
		self.assertIn(f"note_lesson_keys: {self.курс} — ключей записано 1, урок без ключа у 1", первый)
		self.assertIn("note_lesson_keys: колонка lesson удалена", первый)
		self.assertEqual(второй, "note_lesson_keys: колонки lesson нет — заполнять нечего\n")

	def test_колонка_ждёт_ключей_на_уроках(self):
		"""`--skip-failing`: `release_record_keys` упал — ключи заметок
		пишутся, а колонка остаётся до его выполнения; повтор ключей не меняет."""
		отметить_патч(note_lesson_keys.ЖДЁТ, "пропущен")
		схема = self.старый_сайт()

		with схема.подменить({ЧТЕНИЕ_УРОКОВ: self.уроки}):
			первый = выполнить(note_lesson_keys)
			ключи = self.ключи()
			повтор = выполнить(note_lesson_keys)

		self.assertEqual(ключи[self.пустая], "l-1")
		self.assertEqual(self.ключи(), ключи)
		self.assertEqual(схема.ddl, [])
		self.assertIn(
			"note_lesson_keys: колонка lesson оставлена — патч release_record_keys ещё не выполнен", первый
		)
		self.assertIn(f"note_lesson_keys: {self.курс} — ключей записано 0, урок без ключа у 1", повтор)

		отметить_патч(note_lesson_keys.ЖДЁТ, "выполнен")
		with схема.подменить({ЧТЕНИЕ_УРОКОВ: self.уроки}):
			выполнить(note_lesson_keys)

		self.assertEqual(схема.ddl, [DDL])

	def test_свежий_сайт_без_колонки_и_таблицы(self):
		отметить_патч(note_lesson_keys.ЖДЁТ, "выполнен")
		for схема in (
			Схема({ЗАМЕТКА: set(frappe.db.get_table_columns(ЗАМЕТКА)) - {"lesson"}}),
			Схема({}, свои=[ЗАМЕТКА]),
		):
			with self.subTest(таблица=bool(схема.таблицы)), схема.подменить():
				вывод = выполнить(note_lesson_keys)

			self.assertEqual(схема.ddl, [])
			self.assertEqual(вывод, "note_lesson_keys: колонки lesson нет — заполнять нечего\n")
		self.assertEqual(self.ключи()[self.пустая], None)
