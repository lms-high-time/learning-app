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

	def test_строки_разной_длины_отказ(self):
		self.отказ(grid=[*СЕТКА[:2], "costs revenue"])

	def test_неизвестный_блок_отказ(self):
		self.assertEqual(
			self.отказ(grid=[СЕТКА[0], СЕТКА[1], "costs costs revenue revenue nope"]).подробности["key"],
			"nope",
		)

	def test_область_не_прямоугольник_отказ(self):
		self.отказ(grid=["problem solution uvp", "problem problem uvp"])
		self.отказ(grid=["problem solution problem", "metrics metrics uvp"])

	def test_подписи_наброска_и_сводки_проверяются(self):
		self.отказ(labels={"first_sketch": "Набросок"})
		self.отказ(labels={"uvp": ""})
		self.отказ(sketch="nope")
		self.отказ(sketch="problem")
		# У наброска нет полей — ему нечего держать.
		self.отказ(grid=["problem solution", "problem solution"], sketch="uvp")
		self.отказ(summary={"revenue": ["nope"]})
		self.отказ(summary={"problem": ["source"]})
		self.отказ(summary={"nope": ["price"]})

	def test_набросок_не_ячейка(self):
		self.отказ(grid=["problem first_sketch", "problem first_sketch"], sketch="first_sketch")


if __name__ == "__main__":
	unittest.main()
