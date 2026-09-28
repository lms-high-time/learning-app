# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Шаблон и правки курса без базы (learning-services#370)."""

import copy
import unittest

from lms_frappe_app.agent_learning.artifacts import codes
from lms_frappe_app.agent_learning.artifacts.overlay import без_уроков_в_правках, собрать
from lms_frappe_app.agent_learning.errors import Отказ

ШАБЛОН = {
	"title": "Журнал",
	"layout": "sections",
	"blocks": [
		{"key": "intro", "title": "Вступление", "hint": "Одной фразой", "span": 1},
		{
			"key": "items",
			"title": "Записи",
			"hint": "Что случилось",
			"spec": {
				"table": "items",
				"prefix": "I",
				"fields": [{"key": "limit", "title": "Предел", "type": "number"}],
				"columns": [
					{"key": "event", "title": "Событие", "type": "text"},
					{"key": "source", "title": "Источник", "type": "select", "options": ["a", "b"]},
					{"key": "kind", "title": "Вид", "type": "text"},
				],
				"views": [{"type": "columns", "title": "Кратко", "columns": ["event"]}],
			},
		},
		{"key": "outro", "title": "Итог"},
	],
	"canvas": {
		"grid": ["intro items", "outro outro"],
		"labels": {"intro": "Начало", "items": "Записи"},
	},
}


def ключи(блоки):
	return [б["key"] for б in блоки]


def колонки(схема):
	блок = next(б for б in схема["blocks"] if б["key"] == "items")
	return блок["spec"]["columns"]


class TestСборка(unittest.TestCase):
	def отказ(self, правки, код=codes.НЕВЕРНЫЕ_ПРАВКИ, шаблон=ШАБЛОН):
		with self.assertRaises(Отказ) as отказ:
			собрать(шаблон, правки)
		self.assertEqual(отказ.exception.код, код)
		return отказ.exception.подробности

	def test_без_правок_схема_шаблона(self):
		итог = собрать(ШАБЛОН, None)

		self.assertEqual(итог, ШАБЛОН)
		self.assertEqual(собрать(ШАБЛОН, {}), итог)

	def test_шаблон_не_меняется(self):
		до = copy.deepcopy(ШАБЛОН)
		собрать(ШАБЛОН, {"blocks": {"items": {"spec": {"columns": {"kind": None}}}, "intro": None}})

		self.assertEqual(ШАБЛОН, до)

	def test_объект_сливается_с_объектом(self):
		итог = собрать(
			ШАБЛОН,
			{
				"title": "Журнал площадки",
				"blocks": {
					"items": {
						"hint": "Что случилось на площадке",
						"spec": {
							"fields": {"limit": {"title": "Порог"}},
							"columns": {"event": {"hint": "Коротко"}},
						},
					}
				},
			},
		)

		self.assertEqual(итог["title"], "Журнал площадки")
		блок = итог["blocks"][1]
		self.assertEqual(блок["hint"], "Что случилось на площадке")
		self.assertEqual(блок["title"], "Записи", "не названное правкой остаётся от шаблона")
		self.assertEqual(блок["spec"]["fields"], [{"key": "limit", "title": "Порог", "type": "number"}])
		self.assertEqual(
			колонки(итог)[0], {"key": "event", "title": "Событие", "type": "text", "hint": "Коротко"}
		)
		self.assertEqual(блок["spec"]["prefix"], "I")

	def test_null_удаляет(self):
		итог = собрать(
			ШАБЛОН,
			{
				"blocks": {
					"outro": None,
					"intro": {"hint": None},
					"items": {"spec": {"columns": {"kind": None}, "fields": {"limit": None}}},
				},
				"canvas": {"labels": {"items": None}},
			},
		)

		self.assertEqual(ключи(итог["blocks"]), ["intro", "items"])
		self.assertNotIn("hint", итог["blocks"][0])
		self.assertEqual([к["key"] for к in колонки(итог)], ["event", "source"])
		self.assertEqual(итог["blocks"][1]["spec"]["fields"], [])
		self.assertEqual(итог["canvas"]["labels"], {"intro": "Начало"})

	def test_список_заменяется_целиком(self):
		виды = [{"type": "columns", "title": "Все", "columns": ["event", "source"]}]
		итог = собрать(
			ШАБЛОН,
			{
				"blocks": {
					"items": {
						"spec": {"columns": {"source": {"options": ["c"]}}, "views": виды},
					}
				},
				"canvas": {"grid": ["items intro", "outro outro"]},
			},
		)

		self.assertEqual(колонки(итог)[1]["options"], ["c"])
		self.assertEqual(итог["blocks"][1]["spec"]["views"], виды)
		self.assertEqual(итог["canvas"]["grid"], ["items intro", "outro outro"])

	def test_добавки_встают_после_названного(self):
		итог = собрать(
			ШАБЛОН,
			{
				"blocks": {
					"items": {
						"spec": {
							"add_columns": [
								{"key": "permit", "title": "Разрешение", "after": "event"},
								{"key": "owner", "title": "Кто", "after": "event"},
								{"key": "note", "title": "Заметка"},
							],
							"add_fields": [{"key": "floor", "title": "Нижний", "type": "number"}],
						}
					}
				},
				"add_blocks": [
					{"key": "Log", "title": "Журнал", "lesson": "L-2", "after": "intro"},
					{"key": "tail", "title": "Хвост"},
				],
			},
		)

		self.assertEqual(ключи(итог["blocks"]), ["intro", "log", "items", "outro", "tail"])
		self.assertEqual(итог["blocks"][1], {"key": "log", "title": "Журнал", "lesson": "L-2"})
		self.assertEqual(
			[к["key"] for к in колонки(итог)], ["event", "permit", "owner", "source", "kind", "note"]
		)
		self.assertNotIn("after", колонки(итог)[1])
		self.assertEqual([п["key"] for п in итог["blocks"][2]["spec"]["fields"]], ["limit", "floor"])

	def test_добавка_после_добавленного_блока(self):
		итог = собрать(
			ШАБЛОН,
			{"add_blocks": [{"key": "a", "after": "outro"}, {"key": "b", "after": "a"}]},
		)

		self.assertEqual(ключи(итог["blocks"])[-2:], ["a", "b"])

	def test_убранный_блок_можно_завести_заново(self):
		"""`null` и добавка с тем же ключом — блок целиком другой."""
		итог = собрать(
			ШАБЛОН, {"blocks": {"outro": None}, "add_blocks": [{"key": "outro", "title": "Иначе"}]}
		)

		self.assertEqual(итог["blocks"][-1], {"key": "outro", "title": "Иначе"})

	def test_чужой_ключ_отказ(self):
		self.assertEqual(self.отказ({"blocks": {"nope": {"hint": "…"}}}), {"key": "nope"})
		self.assertEqual(
			self.отказ({"blocks": {"items": {"spec": {"columns": {"evnt": {"title": "…"}}}}}}),
			{"key": "items", "column": "evnt"},
		)
		self.assertEqual(
			self.отказ({"blocks": {"items": {"spec": {"fields": {"lmit": None}}}}}),
			{"key": "items", "field": "lmit"},
		)
		подробности = self.отказ(
			{"blocks": {"items": {"spec": {"add_columns": [{"key": "x", "after": "evnt"}]}}}}
		)
		self.assertEqual(подробности["after"], "evnt")

	def test_опечатка_в_имени_свойства_отказ(self):
		self.assertEqual(self.отказ({"blocs": {}}), {"name": "blocs"})
		self.assertEqual(self.отказ({"blocks": {"intro": {"hnt": "…"}}}), {"name": "hnt", "key": "intro"})
		self.assertEqual(
			self.отказ({"blocks": {"items": {"spec": {"column": {}}}}}), {"name": "column", "key": "items"}
		)
		self.assertEqual(self.отказ({"canvas": {"grd": []}}), {"name": "grd", "key": "canvas"})

	def test_колонки_в_правках_по_ключам_а_не_списком(self):
		self.assertEqual(
			self.отказ({"blocks": {"items": {"spec": {"columns": [{"key": "event"}]}}}}), {"key": "items"}
		)

	def test_добавка_существующего_ключа_отказ(self):
		self.assertEqual(self.отказ({"add_blocks": [{"key": "Intro"}]}), {"key": "intro"})
		self.assertEqual(
			self.отказ({"blocks": {"items": {"spec": {"add_columns": [{"key": "source"}]}}}}),
			{"key": "items", "column": "source"},
		)
		self.assertEqual(self.отказ({"add_blocks": [{"key": "twin"}, {"key": "twin"}]}), {"key": "twin"})

	def test_ключ_правкой_не_меняется(self):
		self.assertEqual(
			self.отказ({"blocks": {"items": {"spec": {"columns": {"event": {"key": "what"}}}}}}),
			{"key": "items", "column": "event"},
		)

	def test_урок_только_в_правках(self):
		с_уроком = copy.deepcopy(ШАБЛОН)
		с_уроком["blocks"][0]["lesson"] = "L-1"
		self.assertEqual(self.отказ(None, codes.НЕВЕРНАЯ_СХЕМА, с_уроком), {"key": "intro"})

		итог = собрать(ШАБЛОН, {"blocks": {"intro": {"lesson": "L-1"}, "items": {"lesson": "L-2"}}})

		self.assertEqual([б.get("lesson") for б in итог["blocks"]], ["L-1", "L-2", None])

	def test_холст_сливается(self):
		итог = собрать(
			ШАБЛОН, {"canvas": {"labels": {"intro": "Старт", "outro": "Финиш"}, "sketch": "outro"}}
		)

		self.assertEqual(итог["canvas"]["grid"], ШАБЛОН["canvas"]["grid"])
		self.assertEqual(итог["canvas"]["labels"], {"intro": "Старт", "items": "Записи", "outro": "Финиш"})
		self.assertEqual(итог["canvas"]["sketch"], "outro")

	def test_холст_убирается_и_заводится(self):
		self.assertIsNone(собрать(ШАБЛОН, {"canvas": None})["canvas"])

		без_холста = {**ШАБЛОН, "canvas": None}
		итог = собрать(без_холста, {"canvas": {"grid": ["intro items outro"]}, "layout": "canvas"})

		self.assertEqual(итог["canvas"], {"grid": ["intro items outro"]})
		self.assertEqual(итог["layout"], "canvas")

	def test_правки_не_объект_отказ(self):
		self.отказ(["blocks"])
		self.отказ({"blocks": ["intro"]})
		self.отказ({"blocks": {"intro": "текст"}})
		self.отказ({"add_blocks": {"key": "x"}})
		self.отказ({"canvas": ["intro"]})


class TestПравкиНаследника(unittest.TestCase):
	"""Правки наследника шаблона — те же правки, но без уроков (#375)."""

	def test_урок_в_правке_блока_и_в_добавке_отказ(self):
		for правки, ключ in (
			({"blocks": {"intro": {"lesson": None}}}, "intro"),
			({"add_blocks": [{"key": "log", "lesson": "L-1"}]}, "log"),
		):
			with self.assertRaises(Отказ) as отказ:
				без_уроков_в_правках(правки)
			self.assertEqual(отказ.exception.код, codes.НЕВЕРНЫЕ_ПРАВКИ)
			self.assertEqual(отказ.exception.подробности, {"key": ключ})

	def test_правки_без_уроков_проходят(self):
		без_уроков_в_правках(
			{"blocks": {"intro": {"hint": "…"}, "outro": None}, "add_blocks": [{"key": "x"}]}
		)
		# Не та форма — не здесь: её отклонит `собрать`.
		без_уроков_в_правках({"blocks": ["intro"], "add_blocks": {"key": "x"}})


if __name__ == "__main__":
	unittest.main()
