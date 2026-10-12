# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Правило срока домашки без базы (learning-services#536).

Проводку — что `validate` задания и строки назначения зовут правило —
проверяют `agent_learning/test_homework.py` и `api/test_team_assignments.py`.
"""

from datetime import date

from frappe.tests import UnitTestCase

from lms_frappe_app.agent_learning.doctype.agent_lesson_homework.agent_lesson_homework import (
	НЕВЕРНЫЙ_СРОК,
	проверить_срок,
)
from lms_frappe_app.agent_learning.doctype.course_allocation.course_allocation import РЕЖИМЫ_СРОКА_НАЗНАЧЕНИЯ
from lms_frappe_app.agent_learning.errors import Отказ

ЗАДАНИЕ = None  # режимы по умолчанию — `ВИДЫ_СРОКА`
НАЗНАЧЕНИЕ = РЕЖИМЫ_СРОКА_НАЗНАЧЕНИЯ


class TestПравилоСрока(UnitTestCase):
	@staticmethod
	def проверить(режимы, due_mode, due_days, due_date):
		if режимы is None:
			return проверить_срок(due_mode, due_days, due_date)
		return проверить_срок(due_mode, due_days, due_date, режимы=режимы)

	def test_срок_нормализуется_под_режим(self):
		"""Поле другого режима отбрасывается."""
		случаи = (
			("без срока", ЗАДАНИЕ, "none", 5, "2030-01-01", (None, None)),
			("относительный", ЗАДАНИЕ, "relative", 5, "2030-01-01", (5, None)),
			("дни строкой", ЗАДАНИЕ, "relative", "3", None, (3, None)),
			("абсолютный", ЗАДАНИЕ, "absolute", 9, "2030-02-01", (None, "2030-02-01")),
			("дата объектом", ЗАДАНИЕ, "absolute", None, date(2030, 2, 1), (None, "2030-02-01")),
			("29 февраля високосного", ЗАДАНИЕ, "absolute", None, "2028-02-29", (None, "2028-02-29")),
			("назначение: относительный", НАЗНАЧЕНИЕ, "relative", 1, None, (1, None)),
			("назначение: абсолютный", НАЗНАЧЕНИЕ, "absolute", 9, "2030-02-01", (None, "2030-02-01")),
		)
		for что, режимы, due_mode, due_days, due_date, ожидание in случаи:
			with self.subTest(что):
				self.assertEqual(self.проверить(режимы, due_mode, due_days, due_date), ожидание)

	def test_отказы(self):
		"""Отказ — `invalid_due`; подробности называют неверное поле."""
		случаи = (
			("неизвестный режим", ЗАДАНИЕ, "audio", None, None, {"due_mode": "audio"}),
			("режим не задан", ЗАДАНИЕ, None, None, None, {"due_mode": None}),
			("у назначения нет режима none", НАЗНАЧЕНИЕ, "none", None, None, {"due_mode": "none"}),
			("относительный без дней", ЗАДАНИЕ, "relative", None, None, {"due_mode": "relative"}),
			("относительный: 0 дней", НАЗНАЧЕНИЕ, "relative", 0, None, {"due_mode": "relative"}),
			("относительный: дней меньше нуля", ЗАДАНИЕ, "relative", -1, None, {"due_mode": "relative"}),
			("относительный: дни не числом", ЗАДАНИЕ, "relative", "три", None, {"due_mode": "relative"}),
			("абсолютный без даты", ЗАДАНИЕ, "absolute", 5, None, {"due_mode": "absolute"}),
			("абсолютный: пустая дата", НАЗНАЧЕНИЕ, "absolute", None, "", {"due_mode": "absolute"}),
			*(
				(f"#452 абсолютный: {дата}", ЗАДАНИЕ, "absolute", None, дата, {"due_date": дата})
				for дата in (
					"завтра",
					"2030-02-30",
					"2029-02-29",
					"15.01.2030",
					"20300501",
					"2030-W01-1",
					"2030-1-01",
					"2030-02-01 10:00:00",
				)
			),
		)
		for что, режимы, due_mode, due_days, due_date, подробности in случаи:
			with self.subTest(что):
				with self.assertRaises(Отказ) as пойман:
					self.проверить(режимы, due_mode, due_days, due_date)
				self.assertEqual((пойман.exception.код, пойман.exception.подробности), (НЕВЕРНЫЙ_СРОК, подробности))
