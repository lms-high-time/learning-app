# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Переименования версий шаблона и переход курса без базы (learning-services#376)."""

import unittest

from lms_frappe_app.agent_learning.artifacts import codes, upgrade
from lms_frappe_app.agent_learning.errors import Отказ

ПРЕЖНИЕ = [
	{"key": "intro", "title": "Вступление"},
	{
		"key": "items",
		"title": "Записи",
		"spec": {
			"table": "items",
			"fields": [{"key": "limit", "title": "Предел", "type": "number"}],
			"columns": [
				{"key": "event", "title": "Событие", "type": "text"},
				{"key": "source", "title": "Источник", "type": "text"},
			],
		},
	},
	{"key": "outro", "title": "Итог"},
]
НОВЫЕ = [
	{"key": "intro", "title": "Вступление"},
	{
		"key": "entries",
		"title": "Записи",
		"spec": {
			"table": "log",
			"fields": [{"key": "threshold", "title": "Предел", "type": "number"}],
			"columns": [
				{"key": "what", "title": "Событие", "type": "text"},
				{"key": "source", "title": "Источник", "type": "text"},
				{"key": "owner", "title": "Кто", "type": "text"},
			],
		},
	},
	{"key": "summary", "title": "Итог"},
]
ПЕРЕИМЕНОВАНИЯ = {
	"blocks": {"items": "entries"},
	"fields": {"limit": "threshold"},
	"tables": {"items": "log"},
	"columns": {"log": {"event": "what"}},
}


class TestПроверка(unittest.TestCase):
	def отказ(self, сырые, прежние=ПРЕЖНИЕ, новые=НОВЫЕ) -> dict:
		with self.assertRaises(Отказ) as отказ:
			upgrade.проверить(upgrade.разобрать(сырые), прежние, новые)
		self.assertEqual(отказ.exception.код, codes.НЕВЕРНЫЙ_ШАБЛОН)
		return отказ.exception.подробности

	def test_верные_проходят_и_ключи_блоков_в_нижнем_регистре(self):
		переименования = upgrade.разобрать({**ПЕРЕИМЕНОВАНИЯ, "blocks": {"Items": "Entries"}})

		upgrade.проверить(переименования, ПРЕЖНИЕ, НОВЫЕ)

		self.assertEqual(переименования, ПЕРЕИМЕНОВАНИЯ)
		self.assertEqual(upgrade.разобрать(None), {})
		self.assertEqual(upgrade.разобрать({"blocks": {}, "columns": {"log": {}}}), {})

	def test_путь_к_неверному(self):
		self.assertEqual(self.отказ({"blocks": {"nope": "entries"}})["path"], "renamed.blocks.nope")
		self.assertEqual(self.отказ({"fields": {"limit": "nope"}})["path"], "renamed.fields.limit")
		self.assertEqual(self.отказ({"tables": {"items": "items"}})["path"], "renamed.tables.items")
		self.assertEqual(self.отказ({"columns": {"nope": {"a": "b"}}})["path"], "renamed.columns.nope")
		self.assertEqual(
			self.отказ({"tables": {"items": "log"}, "columns": {"log": {"evnt": "what"}}})["path"],
			"renamed.columns.log.evnt",
		)
		self.assertEqual(self.отказ({"rows": {}})["path"], "renamed.rows")
		self.assertEqual(self.отказ("не json")["path"], "renamed")
		self.assertEqual(self.отказ({"blocks": {"intro": ""}})["path"], "renamed.blocks.intro")

	def test_у_первой_версии_переименований_нет(self):
		self.assertEqual(self.отказ(ПЕРЕИМЕНОВАНИЯ, прежние=None)["path"], "renamed")

	def test_новое_имя_не_занято_в_прошлой_версии(self):
		"""`intro → outro`, а `outro` остаётся: данные двух блоков встретились бы."""
		новые = [{"key": "outro"}, {"key": "items", "spec": ПРЕЖНИЕ[1]["spec"]}]
		self.assertEqual(
			self.отказ({"blocks": {"intro": "outro"}}, новые=новые)["path"], "renamed.blocks.intro"
		)

	def test_обмен_ключей_законен(self):
		upgrade.проверить({"blocks": {"intro": "outro", "outro": "intro"}}, ПРЕЖНИЕ, ПРЕЖНИЕ)

	def test_два_старых_в_одно_новое_отказ(self):
		новые = [{"key": "intro"}, {"key": "items", "spec": ПРЕЖНИЕ[1]["spec"]}, {"key": "tail"}]
		self.assertEqual(
			self.отказ({"blocks": {"intro": "tail", "outro": "tail"}}, новые=новые)["path"],
			"renamed.blocks.intro",
		)


class TestСложение(unittest.TestCase):
	def test_цепочка_складывается_в_одно(self):
		итог = upgrade.сложить(
			[
				{"blocks": {"a": "b"}, "tables": {"t": "u"}, "columns": {"u": {"x": "y"}}},
				None,
				{"blocks": {"b": "c"}, "tables": {"u": "v"}, "columns": {"v": {"y": "z", "p": "q"}}},
			]
		)

		self.assertEqual(
			итог,
			{"blocks": {"a": "c"}, "tables": {"t": "v"}, "columns": {"v": {"x": "z", "p": "q"}}},
		)

	def test_туда_и_обратно_не_оставляет_ничего(self):
		self.assertEqual(upgrade.сложить([{"fields": {"a": "b"}}, {"fields": {"b": "a"}}]), {})

	def test_ключ_заведённый_заново_не_входит(self):
		"""v1 переименовала `a → b` и завела новый `a`; v2 переименовала новый
		`a → c`. У курса на v0 под `a` — то, что теперь `b`."""
		итог = upgrade.сложить([{"blocks": {"a": "b"}}, {"blocks": {"a": "c"}}])

		self.assertEqual(итог, {"blocks": {"a": "b"}})

	def test_колонки_таблицы_заведённой_заново_не_входят(self):
		итог = upgrade.сложить([{"tables": {"t": "u"}}, {"tables": {"t": "w"}, "columns": {"w": {"x": "y"}}}])

		self.assertEqual(итог, {"tables": {"t": "u"}})


class TestПравки(unittest.TestCase):
	def test_ключи_правок_под_новую_версию(self):
		правки = {
			"title": "Журнал",
			"blocks": {
				"Items": {
					"lesson": "L-1",
					"spec": {
						"fields": {"limit": {"title": "Порог"}},
						"columns": {"event": {"hint": "Коротко"}, "source": None},
						"add_columns": [{"key": "permit", "after": "event"}],
						"add_fields": [{"key": "floor", "after": "limit"}],
					},
				},
				"intro": None,
			},
			"add_blocks": [{"key": "log2", "after": "items", "spec": {"table": "items", "columns": []}}],
			"canvas": {
				"grid": ["intro items", "outro outro"],
				"labels": {"items": "Записи"},
				"summary": {"items": ["limit", "event", "other"]},
				"sketch": "items",
			},
		}

		итог = upgrade.переименовать_правки(правки, ПЕРЕИМЕНОВАНИЯ, upgrade.таблицы_блоков(ПРЕЖНИЕ))

		self.assertEqual(
			итог,
			{
				"title": "Журнал",
				"blocks": {
					"entries": {
						"lesson": "L-1",
						"spec": {
							"fields": {"threshold": {"title": "Порог"}},
							"columns": {"what": {"hint": "Коротко"}, "source": None},
							"add_columns": [{"key": "permit", "after": "what"}],
							"add_fields": [{"key": "floor", "after": "threshold"}],
						},
					},
					"intro": None,
				},
				"add_blocks": [{"key": "log2", "after": "entries", "spec": {"table": "log", "columns": []}}],
				"canvas": {
					"grid": ["intro entries", "outro outro"],
					"labels": {"entries": "Записи"},
					"summary": {"entries": ["threshold", "what", "other"]},
					"sketch": "entries",
				},
			},
		)
		self.assertIn("Items", правки["blocks"], "исходные правки не меняются")

	def test_без_переименований_правки_те_же(self):
		правки = {"blocks": {"items": {"hint": "…"}}}

		self.assertEqual(upgrade.переименовать_правки(правки, {}, {}), правки)


class TestДанныеИРазница(unittest.TestCase):
	def test_данные_ученика_под_новыми_ключами(self):
		данные = {
			"tables": {"items": [{"id": "I1", "event": "Пожар", "source": "люди"}], "other": [{"id": "O1"}]},
			"fields": {"limit": 3, "threshold": "старое из убранного блока", "kept": 1},
			"seq": {"items": 1},
		}

		итог = upgrade.переименовать_данные(данные, ПЕРЕИМЕНОВАНИЯ)

		self.assertEqual(
			итог,
			{
				"tables": {"log": [{"id": "I1", "what": "Пожар", "source": "люди"}], "other": [{"id": "O1"}]},
				"fields": {"threshold": 3, "kept": 1},
				"seq": {"log": 1},
			},
		)

	def test_разница_после_переименований(self):
		было = upgrade.переименовать_схему(ПРЕЖНИЕ, ПЕРЕИМЕНОВАНИЯ)

		итог = upgrade.разница(было, НОВЫЕ)

		self.assertEqual(
			итог,
			{
				"blocks": {"added": ["summary"], "removed": ["outro"], "changed": ["entries"]},
				"fields": {"added": [], "removed": []},
				"columns": {"added": ["log.owner"], "removed": []},
			},
		)
		self.assertEqual(ПРЕЖНИЕ[1]["key"], "items", "исходная схема не меняется")

	def test_без_изменений_разница_пустая(self):
		итог = upgrade.разница(ПРЕЖНИЕ, ПРЕЖНИЕ)

		self.assertEqual(итог["blocks"], {"added": [], "removed": [], "changed": []})
		self.assertEqual(итог["columns"], {"added": [], "removed": []})


if __name__ == "__main__":
	unittest.main()
