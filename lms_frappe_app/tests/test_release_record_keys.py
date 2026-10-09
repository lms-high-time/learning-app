# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Патч `release_record_keys`: ключи глав и уроков — на записи Learning по
истории индексов релизов (learning-services#514).

Поля уже есть на тестовом сайте: заведение поля подменено — DDL фиксировал бы
транзакцию теста.
"""

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import call, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from lms_frappe_app import install
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases import index, projection, service
from lms_frappe_app.agent_learning.releases.course_guard import ИЗ_РЕЛИЗА
from lms_frappe_app.patches.v0_1 import release_record_keys
from lms_frappe_app.tests.release_sample import пример_релиза

ПОЛЯ = {projection.ГЛАВА: "chapter_key", projection.УРОК: "lesson_key"}


class IntegrationTestКлючиЗаписейПоИстории(IntegrationTestCase):
	"""Курс двух версий: в первой ключ `l-3` вёл к уроку X, во второй — к уроку Y.
	Так бывает, когда запись прежней версии проекция не нашла и завела новую."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		self.ключ = f"keys-{frappe.generate_hash(length=8)}"
		первый = service.опубликовать(пример_релиза(self.ключ), None, "Administrator")
		self.курс = первый["course"]
		известные = projection.известные(self.курс)
		self.главы, self.уроки = известные["chapters"], известные["lessons"]
		self.прежний_третий = self.уроки["l-3"]
		новый = frappe.get_doc(
			{
				"doctype": projection.УРОК,
				"title": "Урок третий, заново",
				"course": self.курс,
				"chapter": self.главы["ch-2"],
			}
		)
		новый.flags[ИЗ_РЕЛИЗА] = True
		self.уроки["l-3"] = новый.insert(ignore_permissions=True).name
		frappe.db.set_value("LMS Course", self.курс, "active_release", self.вставить_версию(2))
		frappe.clear_document_cache("LMS Course", self.курс)
		# Сайт до патча: ключей на записях нет.
		for doctype, поле in ПОЛЯ.items():
			frappe.db.set_value(doctype, {"course": self.курс}, поле, None, update_modified=False)

	def вставить_версию(self, версия: int) -> str:
		релиз = пример_релиза(self.ключ)
		return (
			frappe.get_doc(
				{
					"doctype": index.РЕЛИЗ,
					"course": self.курс,
					"course_key": self.ключ,
					"version": версия,
					"release_format": релиз["format"],
					"digest": frappe.generate_hash(length=64),
					"published_by": "Administrator",
					"published_at": now_datetime(),
					"snapshot": json.dumps(релиз, ensure_ascii=False),
					**index.строки(релиз, self.главы, self.уроки),
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def выполнить(self) -> str:
		вывод = io.StringIO()
		with patch.object(release_record_keys, "create_custom_field"), redirect_stdout(вывод):
			release_record_keys.execute()
		return next(
			с for с in вывод.getvalue().splitlines() if с.startswith(f"release_record_keys: {self.курс} ")
		)

	def ключи(self) -> dict[str, dict]:
		return {
			doctype: {
				з.name: (з[поле], з.modified)
				for з in frappe.get_all(
					doctype, filters={"course": self.курс}, fields=["name", поле, "modified"]
				)
			}
			for doctype, поле in ПОЛЯ.items()
		}

	def test_пишет_только_победившее_соответствие(self):
		итог = self.выполнить()

		self.assertTrue(итог.endswith("глав 2, уроков 3"), итог)
		self.assertEqual(projection.известные(self.курс), {"chapters": self.главы, "lessons": self.уроки})
		self.assertIsNone(frappe.db.get_value(projection.УРОК, self.прежний_третий, "lesson_key"))
		# Дублей «курс + ключ» нет — уникальный индекс `after_migrate` встанет.
		for doctype, поле in ПОЛЯ.items():
			with self.subTest(doctype=doctype):
				self.assertFalse(
					frappe.db.sql(
						f"""select `{поле}` from `tab{doctype}` where course = %s and `{поле}` is not null
						group by `{поле}` having count(*) > 1""",
						self.курс,
					)
				)

	def test_повторный_запуск_ничего_не_меняет(self):
		self.выполнить()
		было = self.ключи()

		итог = self.выполнить()

		self.assertTrue(итог.endswith("глав 0, уроков 0"), итог)
		self.assertEqual(self.ключи(), было)

	def test_заполненное_и_занятый_ключ_не_трогает(self):
		"""Пишет только в пустое поле и только ключ, которого нет ни на одной записи курса."""
		frappe.db.set_value(projection.УРОК, self.прежний_третий, "lesson_key", "l-3", update_modified=False)

		итог = self.выполнить()

		self.assertTrue(итог.endswith("глав 2, уроков 2"), итог)
		self.assertEqual(frappe.db.get_value(projection.УРОК, self.прежний_третий, "lesson_key"), "l-3")
		self.assertIsNone(frappe.db.get_value(projection.УРОК, self.уроки["l-3"], "lesson_key"))

	def test_до_патча_публикация_отказывает(self):
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, исправленный"

		with self.assertRaises(Отказ) as пойман:
			service.опубликовать(релиз, None, "Administrator")

		self.assertEqual(пойман.exception.код, service.КЛЮЧЕЙ_НЕТ)
		self.assertEqual(пойман.exception.подробности, {"course": self.курс, "chapters": 2, "lessons": 3})

	def test_после_патча_публикация_находит_прежние_записи(self):
		self.выполнить()
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, исправленный"

		ответ = service.опубликовать(релиз, None, "Administrator")

		self.assertEqual(ответ["lessons"]["created"], [])
		self.assertEqual(index.урок(ответ["release"], "l-3")["lesson"], self.уроки["l-3"])

	def test_заводит_поля_как_в_фикстуре(self):
		"""Патчи идут раньше синхронизации фикстур: поля патч заводит сам — теми же,
		что в фикстуре. Вызов подменён: создание колонки фиксирует транзакцию теста."""
		with (
			patch.object(release_record_keys, "create_custom_field") as создать,
			redirect_stdout(io.StringIO()),
		):
			release_record_keys.execute()

		self.assertEqual(
			создать.call_args_list,
			[call(doctype, поле) for doctype, поле in release_record_keys.ПОЛЯ.items()],
		)
		фикстуры = json.loads(
			(Path(release_record_keys.__file__).parents[2] / "fixtures" / "custom_field.json").read_text(
				encoding="utf-8"
			)
		)
		for doctype, поле in release_record_keys.ПОЛЯ.items():
			фикстура = next(п for п in фикстуры if п["name"] == f"{doctype}-{поле['fieldname']}")
			self.assertEqual(
				{
					ключ: значение
					for ключ, значение in фикстура.items()
					if ключ not in ("doctype", "name", "dt")
				},
				поле,
			)


class IntegrationTestИндексКлючейПроекции(IntegrationTestCase):
	"""`install.обеспечить_индекс_ключей_проекции`: DDL подменён и записывается."""

	def test_индекс_курс_и_ключ(self):
		with patch.object(frappe.db, "sql_ddl") as ddl:
			install.обеспечить_индекс_ключей_проекции()

		запросы = [" ".join(вызов.args[0].split()) for вызов in ddl.call_args_list]
		self.assertEqual(
			запросы,
			[
				"CREATE UNIQUE INDEX IF NOT EXISTS `course_chapter_key` "
				"ON `tabCourse Chapter` (`course`, `chapter_key`)",
				"CREATE UNIQUE INDEX IF NOT EXISTS `course_lesson_key` "
				"ON `tabCourse Lesson` (`course`, `lesson_key`)",
			],
		)

	def test_индекс_есть_на_сайте(self):
		"""Индекс в базе сайта: в CI приложение ставится `install-app`, и
		индекс заводит `after_sync`; на мигрированном сайте — `after_migrate`."""
		for doctype, поле in projection.ПОЛЕ_КЛЮЧА.items():
			with self.subTest(doctype=doctype):
				строки = frappe.db.sql(
					f"show index from `tab{doctype}` where Key_name = %s", f"course_{поле}", as_dict=True
				)
				self.assertEqual(
					[(с.Column_name, с.Non_unique) for с in sorted(строки, key=lambda с: с.Seq_in_index)],
					[("course", 0), (поле, 0)],
				)

	def test_при_установке_индекс_заводит_after_sync(self):
		"""`install_app` синхронизирует фикстуры после `after_install`, а
		`after_sync` зовёт следом: поля уже есть."""
		self.assertIn(
			"lms_frappe_app.install.after_sync", frappe.get_hooks("after_sync", app_name="lms_frappe_app")
		)
		with patch.object(install, "обеспечить_индекс_ключей_проекции") as индекс:
			install.after_sync()

		индекс.assert_called_once_with()

	def test_без_поля_индекс_не_заводится(self):
		with (
			patch.object(frappe.db, "has_column", return_value=False),
			patch.object(frappe.db, "sql_ddl") as ddl,
		):
			install.обеспечить_индекс_ключей_проекции()

		ddl.assert_not_called()

	def test_патч_идёт_до_индекса_в_той_же_миграции(self):
		"""Патчи `post_model_sync` идут раньше `after_migrate`: индекс встаёт на
		заполненные патчем ключи. Патч — в `post_model_sync`, до ключей заметок:
		`note_lesson_keys` берёт ключ с записи урока."""
		строки = (
			(Path(release_record_keys.__file__).parents[2] / "patches.txt")
			.read_text(encoding="utf-8")
			.splitlines()
		)
		патч = "lms_frappe_app.patches.v0_1.release_record_keys"
		self.assertGreater(строки.index(патч), строки.index("[post_model_sync]"))
		self.assertLess(строки.index(патч), строки.index("lms_frappe_app.patches.v0_1.note_lesson_keys"))
		self.assertIn(
			"lms_frappe_app.install.after_migrate",
			frappe.get_hooks("after_migrate", app_name="lms_frappe_app"),
		)
