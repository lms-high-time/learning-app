# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Домашка урока из релиза — проекцией в шаблон `Agent Lesson Homework` (learning-services#504)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import homework as домашка
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases import service
from lms_frappe_app.api import authoring, student
from lms_frappe_app.patches.v0_1 import release_homework
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	зачислить,
	создать_домашку,
	создать_занятие,
	создать_куратора,
	создать_организацию,
	создать_ученика,
)

ДОМАШКА = {"title": "Задание", "description": "Сделайте пример.", "answer_mode": "text", "due_days": 3}


class IntegrationTestДомашкаИзРелиза(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.суффикс = суффикс
		self.куратор = создать_куратора(f"rel-hw-{суффикс}@example.com")
		self.ключ = f"hw-{суффикс}"
		frappe.set_user(self.куратор)

	# --- подготовка ---

	def опубликовать(self, релиз: dict | None = None) -> dict:
		return service.опубликовать(релиз or пример_релиза(self.ключ), None, self.куратор)

	def релиз(self, домашка_урока: dict | None = None) -> dict:
		"""Пример релиза, у третьего урока — данная домашка (`None` — без неё)."""
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][2]["homework"] = домашка_урока
		return релиз

	def без_третьего_урока(self) -> dict:
		релиз = пример_релиза(self.ключ)
		релиз["chapters"] = релиз["chapters"][:1]
		релиз["lessons"] = релиз["lessons"][:2]
		return релиз

	def урок(self, курс: str, ключ: str = "l-3") -> str:
		return frappe.get_all(
			"Agent Release Lesson",
			filters={
				"parent": frappe.db.get_value("LMS Course", курс, "active_release"),
				"lesson_key": ключ,
			},
			pluck="lesson",
		)[0]

	def шаблон(self, урок: str):
		имя = frappe.db.get_value(домашка.ЗАДАНИЕ, {"lesson": урок})
		return frappe.get_doc(домашка.ЗАДАНИЕ, имя) if имя else None

	def ученик(self, курс: str, урок: str, метка: str = "pupil") -> str:
		frappe.set_user("Administrator")
		ученик = создать_ученика(f"rel-hw-{метка}-{self.суффикс}@example.com")
		зачислить(ученик, урок)
		frappe.set_user(self.куратор)
		return ученик

	def закрыть_урок(self, ученик: str, урок: str) -> None:
		домашка.выдать(frappe.get_doc("Agent Learning Session", создать_занятие(ученик, урок)))

	def сдачи(self, ученик: str) -> list[str]:
		return frappe.get_all(домашка.СДАЧА, filters={"member": ученик}, pluck="name")

	# --- публикация ---

	def test_публикация_создаёт_шаблон_из_релиза(self):
		курс = self.опубликовать()["course"]

		шаблон = self.шаблон(self.урок(курс))
		self.assertEqual(
			(шаблон.title, шаблон.description, шаблон.answer_mode, шаблон.due_mode, шаблон.due_days),
			("Задание", "Сделайте пример.", "text", "relative", 3),
		)
		self.assertFalse(шаблон.due_date)
		self.assertEqual(шаблон.retired, 0)
		for ключ in ("l-1", "l-2"):
			self.assertIsNone(self.шаблон(self.урок(курс, ключ)), ключ)

	def test_срок_ноль_и_без_срока_шаблон_без_срока(self):
		for дней in (0, None):
			with self.subTest(due_days=дней):
				self.ключ = f"hw-{frappe.generate_hash(length=6)}"
				курс = self.опубликовать(self.релиз({**ДОМАШКА, "due_days": дней}))["course"]

				шаблон = self.шаблон(self.урок(курс))
				self.assertEqual(шаблон.due_mode, "none")
				self.assertFalse(шаблон.due_days)

	def test_второй_релиз_обновляет_тот_же_шаблон(self):
		курс = self.опубликовать()["course"]
		урок = self.урок(курс)
		было = self.шаблон(урок).name

		self.опубликовать(
			self.релиз(
				{"title": "Задание 2", "description": "Новый текст.", "answer_mode": "files", "due_days": 0}
			)
		)

		шаблон = self.шаблон(урок)
		self.assertEqual(шаблон.name, было)
		self.assertEqual(
			(шаблон.title, шаблон.description, шаблон.answer_mode, шаблон.due_mode),
			("Задание 2", "Новый текст.", "files", "none"),
		)
		self.assertFalse(шаблон.due_days)

	def test_тот_же_шаблон_без_изменений_не_сохраняется(self):
		курс = self.опубликовать()["course"]
		урок = self.урок(курс)
		было = self.шаблон(урок).modified
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, исправленный"

		self.опубликовать(релиз)

		self.assertEqual(self.шаблон(урок).modified, было)

	# --- домашка ушла из релиза ---

	def test_убранная_домашка_без_сдач_удаляется_вместе_со_сроками_назначений(self):
		курс = self.опубликовать()["course"]
		урок = self.урок(курс)
		шаблон = self.шаблон(урок).name
		frappe.set_user("Administrator")
		назначение = frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": создать_организацию(f"Орг {self.суффикс}"),
				"course": курс,
				"homework_due": [{"homework": шаблон, "due_mode": "relative", "due_days": 7}],
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.куратор)

		self.опубликовать(self.релиз(None))

		self.assertIsNone(self.шаблон(урок))
		self.assertFalse(frappe.db.exists("Course Allocation Homework Due", {"homework": шаблон}))
		self.assertTrue(frappe.db.exists("Course Allocation", назначение.name))

	def test_убранная_домашка_со_сдачей_остаётся_снятой(self):
		курс = self.опубликовать()["course"]
		урок = self.урок(курс)
		шаблон = self.шаблон(урок).name
		первый = self.ученик(курс, урок)
		второй = self.ученик(курс, урок, "second")
		self.закрыть_урок(первый, урок)
		[сдача] = self.сдачи(первый)

		self.опубликовать(self.релиз(None))

		self.assertEqual(self.шаблон(урок).name, шаблон)
		self.assertEqual(self.шаблон(урок).retired, 1)
		# Снятое задание закрытие урока больше не выдаёт.
		self.закрыть_урок(второй, урок)
		self.assertEqual(self.сдачи(второй), [])
		# Выданную сдачу ученик сохраняет и видит.
		документ = домашка.сохранить(первый, урок, None, answer="Мой ответ")
		self.assertEqual((документ.name, документ.version), (сдача, 1))
		frappe.set_user(первый)
		ответ = student.homework(lesson=урок)
		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(ответ["data"]["homework"]["title"], "Задание")
		self.assertEqual(ответ["data"]["submission"]["answer"], "Мой ответ")
		мои = student.my_homework(lesson=урок)
		self.assertTrue(мои["ok"], мои)
		self.assertEqual([с["title"] for с in мои["data"]["items"]], ["Задание"])

	def test_снятую_домашку_новый_ученик_не_видит_и_не_сдаёт(self):
		курс = self.опубликовать()["course"]
		урок = self.урок(курс)
		первый = self.ученик(курс, урок)
		второй = self.ученик(курс, урок, "second")
		self.закрыть_урок(первый, урок)

		self.опубликовать(self.релиз(None))

		старт = домашка.для_старта(второй, урок, курс, None)
		self.assertIsNone(старт["homework"])
		with self.assertRaises(Отказ) as пойман:
			домашка.сохранить(второй, урок, None, answer="Ответ без выдачи")
		self.assertEqual(пойман.exception.код, домашка.ЗАДАНИЯ_НЕТ)
		self.assertEqual(self.сдачи(второй), [])
		# Ученик с выданной сдачей видит задание на старте и сдаёт его.
		старт = домашка.для_старта(первый, урок, курс, None)
		self.assertEqual(старт["homework"]["title"], "Задание")
		self.assertEqual(домашка.сохранить(первый, урок, None, answer="Мой ответ").version, 1)
		# Страница урока — по тому же правилу.
		for ученик, видит in ((второй, False), (первый, True)):
			frappe.set_user(ученик)
			ответ = student.homework(lesson=урок)
			self.assertTrue(ответ["ok"], ответ)
			self.assertEqual(ответ["data"]["homework"] is not None, видит, ученик)
		# Куратору урок показывает только действующее задание.
		frappe.set_user(self.куратор)
		урок_автора = authoring.get_lesson(lesson=урок)
		self.assertTrue(урок_автора["ok"], урок_автора)
		self.assertIsNone(урок_автора["data"]["homework"])

	def test_снятое_параллельно_задание_новой_сдачи_не_получает(self):
		"""Выдача и сохранение читали задание до того, как публикация его сняла:
		блокирующее чтение перед вставкой видит снятое — сдачи нет."""
		курс = self.опубликовать()["course"]
		урок = self.урок(курс)
		self.закрыть_урок(self.ученик(курс, урок), урок)
		прежнее = домашка.задание_урока(урок)
		self.опубликовать(self.релиз(None))
		второй = self.ученик(курс, урок, "second")

		with patch.object(домашка, "задание_урока", return_value=прежнее):
			self.закрыть_урок(второй, урок)
			with self.assertRaises(Отказ) as пойман:
				домашка.сохранить(второй, урок, None, answer="Ответ")

		self.assertEqual(пойман.exception.код, домашка.ЗАДАНИЯ_НЕТ)
		self.assertEqual(self.сдачи(второй), [])

	def test_снятый_шаблон_со_сдачами_публикация_не_трогает(self):
		курс = self.опубликовать()["course"]
		урок = self.урок(курс)
		self.закрыть_урок(self.ученик(курс, урок), урок)
		self.опубликовать(self.релиз(None))
		было = self.шаблон(урок).modified
		релиз = self.релиз(None)
		релиз["lessons"][0]["title"] = "Урок первый, исправленный"

		self.опубликовать(релиз)

		self.assertEqual((self.шаблон(урок).retired, self.шаблон(урок).modified), (1, было))

	def test_снятая_домашка_прошлого_урока_только_со_сдачей(self):
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][1]["homework"] = ДОМАШКА
		курс = self.опубликовать(релиз)["course"]
		прошлый, урок = self.урок(курс, "l-2"), self.урок(курс, "l-3")
		первый = self.ученик(курс, прошлый)
		второй = self.ученик(курс, прошлый, "second")
		self.закрыть_урок(первый, прошлый)

		self.опубликовать()

		self.assertIsNone(домашка.для_старта(второй, урок, курс, None)["previous_homework"])
		прошлое = домашка.для_старта(первый, урок, курс, None)["previous_homework"]
		self.assertEqual((прошлое["lesson"], прошлое["title"]), (прошлый, "Задание"))
		self.assertIsNotNone(прошлое["submission"])

	def test_урок_ушёл_из_релиза_как_убранная_домашка(self):
		курс = self.опубликовать()["course"]
		урок = self.урок(курс)

		self.опубликовать(self.без_третьего_урока())
		self.assertIsNone(self.шаблон(урок))

		self.опубликовать()
		шаблон = self.шаблон(урок)
		ученик = self.ученик(курс, урок)
		self.закрыть_урок(ученик, урок)

		self.опубликовать(self.без_третьего_урока())
		self.assertEqual(self.шаблон(урок).name, шаблон.name)
		self.assertEqual(self.шаблон(урок).retired, 1)

	def test_вернувшаяся_домашка_тот_же_шаблон(self):
		курс = self.опубликовать()["course"]
		урок = self.урок(курс)
		шаблон = self.шаблон(урок).name
		self.закрыть_урок(self.ученик(курс, урок), урок)
		self.опубликовать(self.релиз(None))

		self.опубликовать(self.релиз({**ДОМАШКА, "description": "Вернулось.", "due_days": 5}))

		вернулся = self.шаблон(урок)
		self.assertEqual(вернулся.name, шаблон)
		self.assertEqual(вернулся.retired, 0)
		self.assertEqual(
			(вернулся.description, вернулся.due_mode, вернулся.due_days), ("Вернулось.", "relative", 5)
		)
		второй = self.ученик(курс, урок, "second")
		self.закрыть_урок(второй, урок)
		self.assertEqual(len(self.сдачи(второй)), 1)

	# --- патч ---

	def test_патч_создаёт_шаблоны_по_действующим_релизам(self):
		ключ = self.ключ
		курс = self.опубликовать()["course"]
		урок = self.урок(курс)
		self.ключ = f"hw-none-{self.суффикс}"
		без_домашки = self.опубликовать(self.релиз(None))["course"]
		frappe.db.delete(домашка.ЗАДАНИЕ, {"lesson": урок})
		frappe.set_user("Administrator")

		release_homework.execute()
		шаблон = self.шаблон(урок)
		release_homework.execute()

		self.assertEqual(
			(шаблон.title, шаблон.answer_mode, шаблон.due_mode, шаблон.due_days, шаблон.retired),
			("Задание", "text", "relative", 3, 0),
		)
		self.assertEqual(self.шаблон(урок).modified, шаблон.modified)
		уроки = frappe.get_all("Course Lesson", filters={"course": без_домашки}, pluck="name")
		self.assertFalse(frappe.db.exists(домашка.ЗАДАНИЕ, {"lesson": ("in", уроки)}))
		# Повторная публикация того же файла ничего не пишет.
		frappe.set_user(self.куратор)
		self.ключ = ключ
		self.assertTrue(self.опубликовать()["unchanged"])

	def test_патч_снимает_и_удаляет_шаблоны_вне_релиза(self):
		"""Шаблоны, которых в действующем релизе нет: со сдачей — снят, без — удалён."""
		курс = self.опубликовать(self.релиз(None))["course"]
		урок, лишний = self.урок(курс), self.урок(курс, "l-1")
		frappe.set_user("Administrator")
		со_сдачей = создать_домашку(урок).name
		создать_домашку(лишний)
		self.закрыть_урок(self.ученик(курс, урок), урок)
		frappe.set_user("Administrator")

		release_homework.execute()

		self.assertEqual(self.шаблон(урок).name, со_сдачей)
		self.assertEqual(self.шаблон(урок).retired, 1)
		self.assertIsNone(self.шаблон(лишний))

	# --- удаление курса ---

	def test_курс_с_домашкой_удаляется_целиком(self):
		курс = self.опубликовать()["course"]
		урок = self.урок(курс)
		frappe.set_user("Administrator")

		service.удалить_курс(курс)

		self.assertFalse(frappe.db.exists("LMS Course", курс))
		self.assertFalse(frappe.db.exists(домашка.ЗАДАНИЕ, {"lesson": урок}))
