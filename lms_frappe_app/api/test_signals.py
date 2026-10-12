# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Сигналы агенту — выводы сервера из истории ученика (learning-services#416)."""

from datetime import date, datetime, timedelta

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_days, nowdate

from lms_frappe_app.agent_learning import signals
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_занятие,
	создать_организацию,
	создать_ученика,
	курс_из_релиза,
	урок_релиза,
)
from lms_frappe_app.api import student


class TestSignalsPriority(UnitTestCase):
	"""Отбор сигналов — без базы: запись в занятие подменена."""

	def test_по_приоритету_и_один_раз_на_занятие(self):
		занятие = frappe.get_doc({"doctype": "Agent Learning Session", "signals_shown": ""})
		записано = {}
		занятие.db_set = lambda поле, значение, **_: записано.update({поле: значение})
		кандидаты = [{"code": "deadline_pace"}, {"code": "blocks_empty"}, {"code": "abandoned_in_row"}]

		отобраны = signals.отобрать(занятие, кандидаты)

		self.assertEqual([с["code"] for с in отобраны], ["abandoned_in_row", "blocks_empty", "deadline_pace"])
		занятие.signals_shown = записано["signals_shown"]
		self.assertEqual(signals.отобрать(занятие, кандидаты), [])


class TestПорогиСигналов(UnitTestCase):
	"""Без базы: пороги `документ_пуст` и `темп_к_сроку`."""

	def test_документ_пуст(self):
		"""Сигнал — когда `covered` не меньше половины целей и ни один блок не
		заполнен; без целей или блоков — нет."""

		def блок(ключ: str = "log", **поля) -> dict:
			return {"artifact": "notebook", "key": ключ, "title": "Журнал", "content": "", "file": None, "url": None, **поля}

		сигнал = [{"code": "blocks_empty", "blocks": [{"artifact": "notebook", "key": "log", "title": "Журнал"}]}]
		случаи = {
			"половина целей разобрана": (["covered", "touched"], [блок()], сигнал),
			"все цели разобраны": (["covered"], [блок()], сигнал),
			"больше половины": (["covered", "covered", "touched"], [блок()], сигнал),
			"меньше половины": (["covered", "touched", "not_started"], [блок()], []),
			"начатые не в счёт": (["touched", "touched"], [блок()], []),
			"без целей": ([], [блок()], []),
			"без блоков": (["covered"], [], []),
			"блок с текстом": (["covered"], [блок(content="Запись")], []),
			"текст из пробелов — пуст": (["covered"], [блок(content="  \n ")], сигнал),
			"блок с файлом": (["covered"], [блок(file="/files/a.txt")], []),
			"блок со ссылкой": (["covered"], [блок(url="https://example.com")], []),
			"заполнен хоть один из блоков": (["covered"], [блок(), блок("plan", content="План")], []),
			"признак filled важнее содержимого": (["covered"], [блок(content="Запись", filled=False)], сигнал),
			"таблица заполнена": (["covered"], [блок(filled=True)], []),
		}
		for что, (статусы, блоки, ожидаемо) in случаи.items():
			with self.subTest(что):
				self.assertEqual(signals.документ_пуст(статусы, блоки), ожидаемо)

	def test_темп_к_сроку(self):
		"""Темп — `⌈уроков × 7 / дней⌉`, дней не меньше одного; сигнал — от трёх
		уроков в неделю; просроченному — без темпа."""
		сегодня = date(2026, 10, 13)

		def темп(уроков: int, в_неделю: int) -> list[dict]:
			return [{"code": "deadline_pace", "lessons_left": уроков, "lessons_per_week": в_неделю, "overdue": False}]

		случаи = {
			"срок жмёт": (сегодня + timedelta(days=2), False, 3, темп(3, 11)),
			"ровно три в неделю": (сегодня + timedelta(days=7), False, 3, темп(3, 3)),
			"с округлением вверх до трёх": (сегодня + timedelta(days=10), False, 3, темп(3, 3)),
			"меньше трёх в неделю": (сегодня + timedelta(days=11), False, 3, []),
			"срок сегодня — один день": (сегодня, False, 1, темп(1, 7)),
			"срок прошёл без отметки просрочки — один день": (сегодня - timedelta(days=3), False, 1, темп(1, 7)),
			"срок строкой": ("2026-10-15", False, 3, темп(3, 11)),
			"срок с временем": (datetime(2026, 10, 15, 23, 59), False, 3, темп(3, 11)),
			"просрочен": (
				сегодня - timedelta(days=3),
				True,
				3,
				[{"code": "deadline_pace", "lessons_left": 3, "lessons_per_week": None, "overdue": True}],
			),
			"просрочен, а уроков не осталось": (сегодня - timedelta(days=3), True, 0, []),
			"без срока": (None, False, 3, []),
			"без срока и с просрочкой": (None, True, 3, []),
			"уроков не осталось": (сегодня + timedelta(days=2), False, 0, []),
		}
		for что, (срок, просрочен, уроков, ожидаемо) in случаи.items():
			with self.subTest(что):
				self.assertEqual(signals.темп_к_сроку(срок, просрочен, уроков, сегодня), ожидаемо)


class IntegrationTestStartSignals(IntegrationTestCase):
	"""Сигналы урока курса из релиза — по прохождению: брошенные попытки, пустой
	документ, темп к сроку; после отметки пункта — пустой документ."""

	@classmethod
	def setUpClass(cls):
		"""Курс тесты только читают; ученик, организация и назначение — у каждого
		теста свои."""
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.курс, _ = курс_из_релиза()
		cls.урок = урок_релиза(cls.курс, "l-1")

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"sig-start-{суффикс}@example.com")
		организация = создать_организацию(f"Компания {суффикс}")
		добавить_в_организацию(self.ученик, организация)
		self.назначение = frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": организация,
				"course": self.курс,
				"deadline": add_days(nowdate(), 90),
				"mandatory": 1,
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)

	def старт(self) -> dict:
		ответ = student.start_lesson(lesson=self.урок)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def брошенное(self, с_отметкой: bool = False) -> None:
		run = прохождения.прохождение(self.ученик, self.курс, "l-1")
		занятие = создать_занятие(self.ученик, self.урок, run=run.name)
		if с_отметкой:
			прохождения.отметить(
				run.name, "refute:M1", "done", "Ученик сам назвал заблуждение", занятие=занятие
			)
		frappe.db.set_value("Agent Learning Session", занятие, "status", "Abandoned")

	def цель_разобрана(self) -> None:
		"""Оба обязательных пункта единственной цели урока — `done`."""
		run = прохождения.прохождение(self.ученик, self.курс, "l-1").name
		прохождения.отметить(run, "term:T1", "done", "Назвал термин")
		прохождения.отметить(run, "l-1-D1/V1", "done", "Выбрал первый")

	def срок(self, срок: str) -> None:
		frappe.set_user("Administrator")
		self.назначение.db_set("deadline", срок)
		frappe.set_user(self.ученик)

	def test_без_истории_сигналов_нет(self):
		self.assertEqual(self.старт()["signals"], [])

	def test_брошенные_подряд_без_отметок_один_раз_на_занятие(self):
		self.брошенное()
		self.брошенное()

		self.assertEqual(self.старт()["signals"], [{"code": "abandoned_in_row", "attempts": 2}])
		# Второй вызов по тому же занятию — продолжение, а не новость.
		self.assertEqual(self.старт()["signals"], [])

	def test_брошенное_с_отметкой_пункта_рвёт_цепочку(self):
		self.брошенное()
		self.брошенное(с_отметкой=True)
		self.брошенное()

		self.assertEqual(self.старт()["signals"], [])

	def test_половина_целей_разобрана_а_документ_пуст(self):
		"""Проводка `документ_пуст` на старте: статусы целей прохождения и блоки
		документа урока. Пороги — `TestПорогиСигналов`."""
		self.цель_разобрана()

		self.assertEqual(
			self.старт()["signals"],
			[{"code": "blocks_empty", "blocks": [{"artifact": "notebook", "key": "log", "title": "Журнал"}]}],
		)

	def test_начатый_документ_не_сигнал(self):
		self.цель_разобрана()
		student.update_artifact(self.курс, "notebook", "log", rows=[{"topic": "Первая встреча"}])

		self.assertEqual(self.старт()["signals"], [])

	def test_отметка_разобравшая_цель_сигналит_один_раз(self):
		занятие = self.старт()["session"]

		первая = student.mark_goal(занятие, "term:T1", "done", "Назвал термин")["data"]
		вторая = student.mark_goal(занятие, "l-1-D1/V1", "done", "Выбрал первый")["data"]
		повтор = student.mark_goal(занятие, "l-1-D1/V1", "done", "Выбрал первый ещё раз")["data"]

		self.assertEqual(первая["signals"], [])
		self.assertEqual(
			вторая["signals"],
			[{"code": "blocks_empty", "blocks": [{"artifact": "notebook", "key": "log", "title": "Журнал"}]}],
		)
		self.assertEqual(повтор["signals"], [])

	def test_срок_жмёт_темп_в_уроках_без_дат(self):
		"""Проводка `темп_к_сроку`: срок назначения, непройденные уроки курса и
		дата сайта. Пороги темпа и просрочка — `TestПорогиСигналов`."""
		self.срок(add_days(nowdate(), 2))

		self.assertEqual(
			self.старт()["signals"],
			[{"code": "deadline_pace", "lessons_left": 3, "lessons_per_week": 11, "overdue": False}],
		)

	def test_срок_прошёл(self):
		"""Проводка флага просрочки: `_сигналы_старта` передаёт `overdue` из
		доступа к курсу — темп без числа уроков в неделю."""
		self.срок(add_days(nowdate(), -3))

		self.assertEqual(
			self.старт()["signals"],
			[{"code": "deadline_pace", "lessons_left": 3, "lessons_per_week": None, "overdue": True}],
		)
