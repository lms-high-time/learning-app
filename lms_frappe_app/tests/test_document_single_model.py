# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Патчи `document_single_model_check` и `document_single_model`: документ
курса — одна модель (learning-services#515).

DDL и старые колонки подменены, как в `test_old_model_patches`: колонок
`spec` и таблицы заметок на мигрированном сайте нет, их строки отдаёт
подменённый запрос. Записи — схемы, документы учеников, файлы — настоящие и
откатываются вместе с тестом.
"""

import json
import os
from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.agent_learning.uploads import новый_файл
from lms_frappe_app.patches.v0_1 import document_single_model as патч
from lms_frappe_app.patches.v0_1 import document_single_model_check as проверка
from lms_frappe_app.tests.sample_data import раздел, создать_курс, создать_ученика, схема_документа
from lms_frappe_app.tests.test_old_model_patches import ПРИЛОЖЕНИЕ, Схема, выполнить

#: Признак запроса старых блоков и запроса заметок — по ним подменённый `sql` отдаёт строки.
СТАРЫЕ_БЛОКИ = "b.spec"
ЗАМЕТКИ = "FROM `tabAgent Artifact Content`"


class TestПеревод(UnitTestCase):
	"""Блок движка — раздел релиза; что не ложится, называется."""

	def test_переводится(self):
		self.assertEqual(
			патч.перевести(
				"text",
				{
					"table": "log",
					"prefix": "X",
					"views": [{"type": "report"}],
					"rows": [{"topic": "заготовка"}],
					"columns": [
						{"key": "topic", "title": "Тема", "type": "longtext", "required": True, "hint": "…"},
						{"key": "owner", "title": "Кто", "type": "text", "required": "topic"},
						{"key": "note", "type": "text"},
					],
				},
			),
			(
				"many",
				[
					{"key": "topic", "title": "Тема", "required": True},
					{"key": "owner", "title": "Кто", "required": {"if_column": "topic"}},
					{"key": "note", "title": "note", "required": False},
				],
			),
		)
		self.assertEqual(
			патч.перевести(
				None, json.dumps({"fields": [{"key": "scope", "title": "Что", "required": True}]})
			),
			("one", [{"key": "scope", "title": "Что", "required": True}]),
		)

	def test_не_переводится(self):
		for имя, (вид, спек, причина) in {
			"файл": ("file", {}, "блок вида file"),
			"ссылка": ("link", {"fields": [{"key": "a"}]}, "блок вида link"),
			"текст без полей": ("text", None, "ни полей, ни колонок"),
			"поля и колонки": (
				"text",
				{"fields": [{"key": "a"}], "columns": [{"key": "b"}]},
				"и поля, и колонки",
			),
			"число": ("text", {"columns": [{"key": "a", "type": "number"}]}, "колонка a типа number"),
			"формула поля": ("text", {"fields": [{"key": "a", "type": "formula"}]}, "колонка a типа formula"),
			"spec не объект": ("text", "[1]", "spec не объект"),
			"колонки не списком": ("text", {"columns": {"a": {}}}, "не списком"),
			"колонка без ключа": ("text", {"columns": [{"title": "Без ключа"}]}, "колонка без ключа"),
			"колонка не объект": ("text", {"columns": ["a"]}, "колонка без ключа"),
		}.items():
			with self.subTest(имя):
				with self.assertRaisesRegex(патч.Непереводимо, причина):
					патч.перевести(вид, спек)

	def test_выборка_заметок_по_колонкам(self):
		"""Колонки, которой нет в таблице заметок, запрос не называет."""
		полная = патч.выборка_заметок({"content", "url", "file", "preview"})
		for колонка in ("c.`content`", "c.`url`", "c.`file`", "c.`preview`", "f.file_name"):
			self.assertIn(колонка, полная)
		ранняя = патч.выборка_заметок({"content"})
		self.assertIn("c.`content`", ранняя)
		for колонка in ("c.`url`", "c.`file`", "c.`preview`", "tabFile"):
			self.assertNotIn(колонка, ранняя)
		self.assertIn("AND (FALSE)", патч.выборка_заметок(set()))

	def test_холст(self):
		"""Пустой JSON холста — не холст; раскладка `canvas` — холст."""
		for canvas, layout, ожидание in (
			(None, "sections", False),
			("", "sections", False),
			("  ", None, False),
			("{}", "sections", False),
			("[]", "sections", False),
			("null", "sections", False),
			('{"grid": ["a b"]}', "sections", True),
			("не JSON", "sections", True),
			(None, "canvas", True),
		):
			with self.subTest(canvas=canvas, layout=layout):
				self.assertIs(патч.есть_холст(layout, canvas), ожидание)

	def test_проверка_идёт_до_синхронизации_схемы(self):
		"""Проверка — в `pre_model_sync`, перевод — в `post_model_sync`."""
		строки = (ПРИЛОЖЕНИЕ / "patches.txt").read_text(encoding="utf-8").splitlines()
		до, после = строки.index("[pre_model_sync]"), строки.index("[post_model_sync]")
		self.assertTrue(до < строки.index("lms_frappe_app.patches.v0_1.document_single_model_check") < после)
		self.assertGreater(строки.index("lms_frappe_app.patches.v0_1.document_single_model"), после)

	def test_снятых_полей_нет_в_схемах(self):
		"""Страховка от удаления живой колонки: поле, вернувшееся в схему, патч снёс бы с данными."""
		for доктайп, поля in {**патч.КОЛОНКИ, патч.НАСТРОЙКИ: патч.ПОЛЯ_НАСТРОЕК}.items():
			имя = frappe.scrub(доктайп)
			схема = json.loads(
				(ПРИЛОЖЕНИЕ / "agent_learning" / "doctype" / имя / f"{имя}.json").read_text(encoding="utf-8")
			)
			self.assertEqual({п["fieldname"] for п in схема["fields"]} & set(поля), set(), доктайп)
		self.assertFalse((ПРИЛОЖЕНИЕ / "agent_learning" / "doctype" / "agent_artifact_content").exists())


class IntegrationTestDocumentSingleModel(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		# Тест удаляет сирот всего сайта: данные соседнего теста класса — тоже.
		# Точка сохранения возвращает их после каждого теста, а не после класса.
		frappe.db.savepoint("document_single_model")
		self.addCleanup(frappe.db.rollback, save_point="document_single_model")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"dsm-{суффикс}@example.com")
		self.курс = создать_курс(f"Курс документа {суффикс}")
		схема = схема_документа(
			self.курс,
			"notebook",
			"Тетрадь",
			[раздел("log", "Журнал", "many", ["topic"]), раздел("rules", "Правила", "one", ["scope"])],
		)["data"]["id"]
		self.блоки = {б.block_key: б.name for б in frappe.get_doc("Agent Course Artifact", схема).blocks}
		self.схема = схема
		# До патча `rows` и `columns` у блока нет — их пишет перевод.
		for имя in self.блоки.values():
			frappe.db.set_value("Agent Artifact Block", имя, {"rows": None, "columns": None})

		self.свой = self.документ(self.курс)
		self.сирота = self.документ(f"нет-курса-{суффикс}")
		self.схема_сирота = f"ACA-dsm-{суффикс}"
		frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"name": self.схема_сирота,
				"course": f"нет-курса-{суффикс}",
				"slug": "plan",
				"title": "План",
				"version": 1,
			}
		).db_insert()
		self.файлы = [
			новый_файл(frappe.get_doc("Agent Student Artifact", имя), f"{имя}.csv", f"a\n{имя}\n".encode())
			.insert(ignore_permissions=True)
			.name
			for имя in (self.свой, self.сирота)
		]
		frappe.db.delete("Singles", {"doctype": патч.НАСТРОЙКИ, "field": ("in", патч.ПОЛЯ_НАСТРОЕК)})
		frappe.db.sql(
			"INSERT INTO `tabSingles` (doctype, field, value) VALUES (%s, %s, %s)",
			(патч.НАСТРОЙКИ, "artifact_preview_rows", "20"),
		)

	def документ(self, курс: str) -> str:
		документ = frappe.get_doc(
			{
				"doctype": "Agent Student Artifact",
				"name": frappe.generate_hash(length=10),
				"student": self.ученик,
				"course": курс,
				"artifact": "notebook",
				"data": json.dumps({"tables": {"log": [{"id": "L1", "topic": "Встреча"}]}}),
			}
		)
		документ.db_insert()
		return документ.name

	def старый_сайт(self, заметки: bool = True) -> Схема:
		"""Схема до патча; без `заметки` — таблицы заметок уже нет, как на сайте,
		поставленном после её снятия. Запросы к таблице заметок в тестах всегда
		идут в подмену (`ЗАМЕТКИ`), а не в базу: на локальном сайте она может
		быть, на свежем — нет."""
		таблицы = {
			"Agent Artifact Block": {"name", "rows", "columns", *патч.КОЛОНКИ["Agent Artifact Block"]},
			"Agent Course Artifact": {"name", *патч.КОЛОНКИ["Agent Course Artifact"]},
		}
		if заметки:
			таблицы[патч.ЗАМЕТКИ] = {"name", "parent", "block_key", *патч.ПОЛЯ_ЗАМЕТКИ}
		return Схема(таблицы, свои=[патч.ЗАМЕТКИ])

	@contextmanager
	def подменить(self, схема: Схема, строки: dict):
		"""`схема.подменить`, а запрос к таблице заметок мимо подмены — падение
		теста. `Why:` на локальном сайте таблица заметок есть, на свежем — нет:
		такой запрос проходил локально и падал в CI."""
		исходный = frappe.db.sql

		def sql(запрос, *args, **kwargs):
			if f"`tab{патч.ЗАМЕТКИ}`" in str(запрос):
				raise AssertionError(f"запрос к таблице заметок мимо подмены: {запрос}")
			return исходный(запрос, *args, **kwargs)

		with patch.object(frappe.db, "sql", side_effect=sql), схема.подменить(строки):
			yield схема

	def старые_блоки(self, журнал: dict) -> list:
		общее = {
			"schema_name": self.схема,
			"course": self.курс,
			"slug": "notebook",
			"version": 1,
			"is_active": 1,
			"layout": "sections",
			"canvas": None,
		}
		правила = {"fields": [{"key": "scope", "title": "Что решаем", "type": "longtext", "required": True}]}
		return [
			frappe._dict(
				name=self.блоки["log"], block_key="log", kind="text", spec=json.dumps(журнал), **общее
			),
			frappe._dict(
				name=self.блоки["rules"], block_key="rules", kind="text", spec=json.dumps(правила), **общее
			),
			# Блок схемы курса, которого нет, не переводится: схема — сирота.
			frappe._dict(
				name="нет-блока", block_key="x", kind="file", spec=None, **{**общее, "course": "нет-курса"}
			),
		]

	def заметки(self) -> list:
		return [
			frappe._dict(
				document=self.свой,
				student=self.ученик,
				course=self.курс,
				artifact="notebook",
				block="log",
				content="Заметка к журналу",
				url=None,
				file=self.файлы[0],
				file_name="plan.xlsx",
			)
		]

	def бэкапы(self) -> list[str]:
		return frappe.get_all(
			"File", filters={"file_name": ("like", "document-model-515-backup%")}, pluck="name"
		)

	def test_переводит_удаляет_и_второй_раз_ничего(self):
		журнал = {
			"table": "log",
			"columns": [
				{"key": "topic", "title": "Тема", "type": "longtext", "required": True},
				{"key": "owner", "title": "Кто", "type": "longtext", "required": "topic"},
			],
		}
		было_бэкапов = set(self.бэкапы())
		схема = self.старый_сайт()
		with self.подменить(схема, {СТАРЫЕ_БЛОКИ: self.старые_блоки(журнал), ЗАМЕТКИ: self.заметки()}):
			первый = выполнить(патч)
			ddl = list(схема.ddl)
			второй = выполнить(патч)

		[бэкап] = set(self.бэкапы()) - было_бэкапов
		файл = frappe.get_doc("File", бэкап)
		self.addCleanup(lambda: os.path.exists(путь := файл.get_full_path()) and os.remove(путь))
		with self.subTest("бэкап"):
			self.assertTrue(файл.is_private)
			содержимое = json.loads(файл.get_content())
			self.assertEqual(
				[(з["document"], з["block"], з["content"], з["file_name"]) for з in содержимое["content"]],
				[(self.свой, "log", "Заметка к журналу", "plan.xlsx")],
			)
			self.assertEqual([з["name"] for з in содержимое["orphans"]["student_artifacts"]], [self.сирота])
			self.assertEqual(
				json.loads(содержимое["orphans"]["student_artifacts"][0]["data"])["tables"]["log"][0][
					"topic"
				],
				"Встреча",
			)
			self.assertEqual(
				[з["name"] for з in содержимое["orphans"]["course_artifacts"]], [self.схема_сирота]
			)
			self.assertEqual({ф["name"] for ф in содержимое["files"]}, set(self.файлы))

		with self.subTest("сироты и файлы удалены, свой документ на месте"):
			self.assertFalse(frappe.db.exists("Agent Student Artifact", self.сирота))
			self.assertFalse(frappe.db.exists("Agent Course Artifact", self.схема_сирота))
			self.assertFalse(frappe.db.exists("File", {"name": ("in", self.файлы)}))
			self.assertTrue(frappe.db.exists("Agent Student Artifact", self.свой))

		with self.subTest("блоки переведены"):
			блоки = {
				б.block_key: б
				for б in frappe.get_all(
					"Agent Artifact Block",
					filters={"parent": self.схема},
					fields=["block_key", "rows", "columns"],
				)
			}
			self.assertEqual(блоки["log"].rows, "many")
			self.assertEqual(
				json.loads(блоки["log"].columns),
				[
					{"key": "topic", "title": "Тема", "required": True},
					{"key": "owner", "title": "Кто", "required": {"if_column": "topic"}},
				],
			)
			self.assertEqual(
				(блоки["rules"].rows, json.loads(блоки["rules"].columns)),
				("one", [{"key": "scope", "title": "Что решаем", "required": True}]),
			)

		with self.subTest("колонки, таблица и настройка"):
			self.assertEqual(
				ddl,
				[
					"ALTER TABLE `tabAgent Artifact Block` DROP COLUMN IF EXISTS `kind`, "
					"DROP COLUMN IF EXISTS `accept`, DROP COLUMN IF EXISTS `span`, "
					"DROP COLUMN IF EXISTS `hint`, DROP COLUMN IF EXISTS `spec`",
					"ALTER TABLE `tabAgent Course Artifact` DROP COLUMN IF EXISTS `layout`, "
					"DROP COLUMN IF EXISTS `canvas`",
					"DROP TABLE IF EXISTS `tabAgent Artifact Content`",
				],
			)
			self.assertFalse(
				frappe.db.exists("Singles", {"doctype": патч.НАСТРОЙКИ, "field": "artifact_preview_rows"})
			)
			self.assertIn(
				"заметок блоков 1, сирот: документов учеников 1, схем 1; файлов документов удалено 2; "
				"блоков переведено 2, непереводимых в прежних версиях 0; значений настроек удалено 1",
				первый,
			)

		with self.subTest("второй запуск ничего не меняет"):
			self.assertEqual(схема.ddl, ddl)
			self.assertEqual(set(self.бэкапы()) - было_бэкапов, {бэкап})
			self.assertIn(
				"бэкап не нужен; заметок блоков 0, сирот: документов учеников 0, схем 0; "
				"файлов документов удалено 0; блоков переведено 0, непереводимых в прежних версиях 0; "
				"значений настроек удалено 0",
				второй,
			)

	def test_непереводимая_действующая_схема_ничего_не_трогает(self):
		"""Действующая схема существующего курса с числом, холстом или чужой
		таблицей — отказ со всеми бедами сразу; ни бэкапа, ни удалений, ни DDL.
		Проверка до синхронизации схемы находит то же."""
		было_бэкапов = set(self.бэкапы())
		журнал = {
			"table": "register",
			"columns": [{"key": "topic", "type": "number"}],
		}
		блоки = self.старые_блоки(журнал)
		блоки[1].canvas = '{"grid": ["rules"]}'
		схема = self.старый_сайт()
		with self.подменить(схема, {СТАРЫЕ_БЛОКИ: блоки, ЗАМЕТКИ: self.заметки()}):
			with self.assertRaises(frappe.ValidationError) as отказ:
				выполнить(патч)

		текст = str(отказ.exception)
		for беда in ("холст", "блок log — таблица register не своя"):
			self.assertIn(беда, текст)
		self.assertIn(f"{self.схема} (курс {self.курс}, документ notebook, v1, действующая)", текст)
		self.assertNotIn("нет-курса", текст, "схема курса, которого нет, — сирота, а не беда")
		self.assertEqual(схема.ddl, [])
		self.assertEqual(set(self.бэкапы()), было_бэкапов)
		self.assertTrue(frappe.db.exists("Agent Student Artifact", self.сирота))
		self.assertEqual(frappe.db.count("File", {"name": ("in", self.файлы)}), 2)
		self.assertIsNone(frappe.db.get_value("Agent Artifact Block", self.блоки["rules"], "rows"))

		with self.подменить(схема, {СТАРЫЕ_БЛОКИ: блоки, ЗАМЕТКИ: []}):
			with self.assertRaises(frappe.ValidationError) as до_синхронизации:
				выполнить(проверка)
		self.assertIn("схема доктайпов и данные не тронуты", str(до_синхронизации.exception))
		self.assertIn("блок log — таблица register не своя", str(до_синхронизации.exception))

	def test_прежняя_версия_не_валит_патч(self):
		"""Непереводимый блок и холст недействующей версии — в бэкап, у блока нет
		`rows` и `columns`; переводимый блок той же версии переводится. Проверка
		до синхронизации их не считает бедой. Таблицы заметок на этом сайте уже
		нет: патч её не читает и не удаляет."""
		было_бэкапов = set(self.бэкапы())
		блоки = self.старые_блоки({"table": "register", "columns": [{"key": "topic", "type": "number"}]})
		for блок in блоки:
			блок.is_active = 0
		блоки[0].canvas = '{"grid": ["log"]}'
		схема = self.старый_сайт(заметки=False)
		with self.подменить(схема, {СТАРЫЕ_БЛОКИ: блоки, ЗАМЕТКИ: []}):
			выполнить(проверка)
			вывод = выполнить(патч)

		self.assertIn("заметок блоков 0,", вывод)
		self.assertIn("блоков переведено 1, непереводимых в прежних версиях 2", вывод)
		self.assertNotIn("DROP TABLE IF EXISTS `tabAgent Artifact Content`", схема.ddl, "таблицы заметок нет")
		self.assertEqual(
			frappe.db.get_value("Agent Artifact Block", self.блоки["log"], ["rows", "columns"]), (None, None)
		)
		self.assertEqual(frappe.db.get_value("Agent Artifact Block", self.блоки["rules"], "rows"), "one")
		[бэкап] = set(self.бэкапы()) - было_бэкапов
		файл = frappe.get_doc("File", бэкап)
		self.addCleanup(lambda: os.path.exists(путь := файл.get_full_path()) and os.remove(путь))
		прежние = json.loads(файл.get_content())["inactive_blocks"]
		self.assertEqual(
			[
				(п["block"], п.get("canvas"), json.loads(п["spec"])["table"] if п.get("spec") else None)
				for п in прежние
			],
			[(None, '{"grid": ["log"]}', None), (self.блоки["log"], None, "register")],
		)
