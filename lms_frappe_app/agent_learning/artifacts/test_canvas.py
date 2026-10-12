# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Холст документа без базы (learning-services#351)."""

import unittest

from lms_frappe_app.agent_learning.artifacts import canvas, codes, schema
from lms_frappe_app.agent_learning.errors import Отказ


def холст_документа():
	блоки = [
		{"block_key": ключ, "spec": None}
		for ключ in ("problem", "solution", "uvp", "unfair", "segments", "metrics", "channels", "costs")
	]
	блоки.append(
		{
			"block_key": "revenue",
			"spec": schema.проверить_спек(
				{
					"columns": [{"key": "source", "title": "Источник"}],
					"fields": [{"key": "price", "type": "number"}],
				},
				"revenue",
			),
		}
	)
	блоки.append(
		{
			"block_key": "first_sketch",
			"spec": schema.проверить_спек({"fields": [{"key": "problem"}, {"key": "uvp"}]}, "first_sketch"),
		}
	)
	return блоки


СЕТКА = [
	"problem solution uvp unfair segments",
	"problem metrics uvp channels segments",
	"costs costs revenue revenue revenue",
]


class TestХолст(unittest.TestCase):
	def проверить(self, **холст):
		return canvas.проверить_холст({"grid": СЕТКА, **холст}, холст_документа())

	def отказ(self, **холст):
		with self.assertRaises(Отказ) as отказ:
			self.проверить(**холст)
		self.assertEqual(отказ.exception.код, codes.НЕВЕРНАЯ_СХЕМА)
		return отказ.exception

	def test_холст_в_каноническом_виде(self):
		self.assertEqual(
			self.проверить(
				grid=["Problem  solution uvp unfair segments", *СЕТКА[1:]],
				labels={"uvp": " Обещание "},
				sketch="first_sketch",
				summary={"revenue": ["source", "price"]},
			),
			{
				"grid": СЕТКА,
				"labels": {"uvp": "Обещание"},
				"sketch": "first_sketch",
				"summary": {"revenue": ["source", "price"]},
			},
		)

	def test_пустой_холст_нет_холста(self):
		self.assertIsNone(canvas.проверить_холст(None, холст_документа()))
		self.assertIsNone(canvas.проверить_холст("", холст_документа()))

	def test_неверный_холст_отказ(self):
		for случай, холст, ключ in (
			("строки разной длины", {"grid": [*СЕТКА[:2], "costs revenue"]}, None),
			("неизвестный блок", {"grid": [СЕТКА[0], СЕТКА[1], "costs costs revenue revenue nope"]}, "nope"),
			(
				"область не прямоугольник: ступенька",
				{"grid": ["problem solution uvp", "problem problem uvp"]},
				None,
			),
			(
				"область не прямоугольник: разрыв",
				{"grid": ["problem solution problem", "metrics metrics uvp"]},
				None,
			),
			("подпись не ячейки сетки", {"labels": {"first_sketch": "Набросок"}}, None),
			("пустая подпись", {"labels": {"uvp": ""}}, None),
			("набросок не блок документа", {"sketch": "nope"}, None),
			("набросок без полей: блок сетки", {"sketch": "problem"}, None),
			# У наброска нет полей — ему нечего держать.
			(
				"набросок без полей: блок вне сетки",
				{"grid": ["problem solution", "problem solution"], "sketch": "uvp"},
				None,
			),
			("сводка: неизвестное поле", {"summary": {"revenue": ["nope"]}}, None),
			("сводка: колонка чужой таблицы", {"summary": {"problem": ["source"]}}, None),
			("сводка не ячейки сетки", {"summary": {"nope": ["price"]}}, None),
			(
				"набросок — ячейка сетки",
				{"grid": ["problem first_sketch", "problem first_sketch"], "sketch": "first_sketch"},
				None,
			),
		):
			with self.subTest(случай):
				отказ = self.отказ(**холст)
				if ключ:
					self.assertEqual(отказ.подробности["key"], ключ)


if __name__ == "__main__":
	unittest.main()
