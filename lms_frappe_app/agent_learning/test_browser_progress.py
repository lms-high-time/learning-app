# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Просмотр урока в браузере не закрывает урок, а урок курса из релиза — и методы Learning.

`Why:` запись `LMS Course Progress` со статусом `Complete` — единственный
признак пройденного урока для агента и отчёта руководителя. Пока её ставил
таймер страницы урока, агент пропускал урок, который ученик только пролистал
(lms-platform#305).
"""

import sys
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.handler import execute_cmd
from frappe.tests import IntegrationTestCase, UnitTestCase
from lms.lms.api import mark_lesson_progress
from lms.lms.doctype.course_lesson import course_lesson

from lms_frappe_app.agent_learning import browser_progress, quiz
from lms_frappe_app.agent_learning.constants import ПРОЙДЕН
from lms_frappe_app.tests.sample_data import (
	занятие_релиза,
	зачислить,
	курс_из_релиза,
	создать_куратора,
	создать_урок,
	создать_ученика,
	урок_релиза,
)


class TestПодменаМетодаLearning(UnitTestCase):
	"""Без базы: подмена читается из хуков."""

	def test_подмена_метода_learning(self):
		"""Запрос страницы уходит в подмену: `Why:` без хука страница снова зовёт
		метод Learning, и урок закрывается просмотром — молча, тест на сам модуль
		этого не заметит. Подмена сверяет строку вызова, а не функцию: имя, под
		которым Learning импортировал `save_progress` в другой модуль, — вход мимо
		подмены (learning-services#525)."""
		with self.subTest("запрос страницы уходит в подмену"):
			self.assertEqual(
				frappe.override_whitelisted_method(browser_progress.ПОДМЕНЯЕМЫЙ),
				"lms_frappe_app.agent_learning.browser_progress.save_progress",
			)

		with self.subTest("learning-services#525: каждое имя метода Learning подменено"):
			# Модули Learning с методами урока — загружены и тогда, когда тест идёт первым.
			import lms.lms.api
			import lms.lms.doctype.lms_quiz.lms_quiz

			имена = {
				f"{имя_модуля}.{имя}"
				for имя_модуля, модуль in list(sys.modules.items())
				for имя, значение in list(getattr(модуль, "__dict__", {}).items())
				if значение is course_lesson.save_progress
			}

			self.assertIn(browser_progress.ПОДМЕНЯЕМЫЙ, имена)
			for имя in имена:
				self.assertEqual(
					frappe.override_whitelisted_method(имя),
					"lms_frappe_app.agent_learning.browser_progress.save_progress",
					f"{имя} зовёт метод Learning мимо подмены",
				)


class IntegrationTestBrowserProgress(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.урок = создать_урок("Урок в браузере")
		self.ученик = создать_ученика(f"browser-{frappe.generate_hash(length=6)}@example.com")
		self.курс = зачислить(self.ученик, self.урок)

	def вызвать(self, **параметры):
		"""Тем путём, которым идёт запрос страницы: через подмену хуком."""
		метод = frappe.override_whitelisted_method(browser_progress.ПОДМЕНЯЕМЫЙ)
		return frappe.call(метод, lesson=self.урок, course=self.курс, **параметры)

	def test_подмена_на_курсе_без_релиза(self):
		"""Просмотр урока урок не закрывает; SCORM-глава другого пути к прогрессу
		не имеет — отказ её заморозил бы, и её прогресс идёт в Learning."""
		frappe.set_user(self.ученик)
		with self.subTest("просмотр урока не закрывает урок"):
			with self.assertRaises(frappe.ValidationError):
				self.вызвать()

			self.assertFalse(
				frappe.db.exists("LMS Course Progress", {"member": self.ученик, "lesson": self.урок}),
				"урок закрыт без занятия — агент его пропустит",
			)

		with self.subTest("прогресс SCORM идёт в Learning"):
			сведения = {"is_complete": True}

			with patch.object(course_lesson, "save_progress", return_value=100) as learning:
				self.assertEqual(self.вызвать(scorm_details=сведения), 100)

			learning.assert_called_once_with(self.урок, self.курс, сведения)


class IntegrationTestУрокИзРелиза(IntegrationTestCase):
	"""Урок курса из релиза закрывает только занятие (learning-services#525).

	`Why:` Learning закрывает урок и прямыми вызовами Python, мимо подмены
	методов: ученик своим токеном закрыл бы весь курс без занятий и квиза — с
	сертификатом и следующим курсом программы.
	"""

	@classmethod
	def setUpClass(cls):
		"""Курс из релиза тесты только читают: отметки и занятия — у ученика своего теста."""
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.курс, _ = курс_из_релиза()
		cls.урок = урок_релиза(cls.курс, "l-1")

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.новый_ученик()

	def новый_ученик(self) -> None:
		frappe.set_user("Administrator")
		self.ученик = создать_ученика(f"release-progress-{frappe.generate_hash(length=6)}@example.com")
		зачислить(self.ученик, self.урок)

	def отметка(self, урок: str | None = None) -> str | None:
		return frappe.db.get_value(
			"LMS Course Progress", {"member": self.ученик, "lesson": урок or self.урок}, "status"
		)

	def завести(self, урок: str | None = None, статус: str = ПРОЙДЕН, **флаги):
		"""Отметка пройденного — вставкой записи, как её пишет любой путь Python."""
		return frappe.get_doc(
			{
				"doctype": "LMS Course Progress",
				"member": self.ученик,
				"lesson": урок or self.урок,
				"status": статус,
			}
		).insert(**флаги)

	def номер(self, урок: str) -> tuple[int, int]:
		"""Номер главы и урока в оглавлении — так урок называет `mark_lesson_progress`."""
		глава, номер_урока = frappe.db.get_value("Lesson Reference", {"lesson": урок}, ["parent", "idx"])
		return frappe.db.get_value("Chapter Reference", {"chapter": глава}, "idx"), номер_урока

	def test_отметку_не_завести_мимо_занятия(self):
		"""Ни один путь Learning не закрывает урок курса из релиза. Случаи — у
		одного ученика: отказ отметки не пишет; случай с заведённой заранее
		отметкой — последним.

		- `mark_lesson_progress`: браузерный квиз и задание Learning зовут его, а
		  он — `save_progress` напрямую;
		- SCORM-прогресс: пройденным Learning отмечает и урок без пакета;
		- любой путь Python — правило на самой отметке, так её пишет и `submit_quiz`;
		- пометка занятия — только для своего урока;
		- снятую отметку Learning правит SCORM-прогрессом через `db.set_value`,
		  мимо `validate`, — держит только подмена, под любым именем метода."""
		глава, номер_урока = self.номер(self.урок)
		второй = урок_релиза(self.курс, "l-2")
		frappe.set_user(self.ученик)
		with self.subTest("mark_lesson_progress"):
			with self.assertRaises(frappe.ValidationError):
				mark_lesson_progress(self.курс, глава, номер_урока)

			self.assertIsNone(self.отметка())

		with self.subTest("прогресс SCORM"):
			метод = frappe.override_whitelisted_method(browser_progress.ПОДМЕНЯЕМЫЙ)

			with self.assertRaises(frappe.ValidationError):
				frappe.call(метод, lesson=self.урок, course=self.курс, scorm_details={"is_complete": True})

			self.assertIsNone(self.отметка())

		with self.subTest("любой путь Python"):
			with self.assertRaises(frappe.ValidationError):
				self.завести(ignore_permissions=True)

			self.assertIsNone(self.отметка())

		with self.subTest("пометка занятия только для своего урока"):
			with browser_progress.закрытие_урока(self.ученик, self.урок):
				with self.assertRaises(frappe.ValidationError):
					self.завести(второй, ignore_permissions=True)

			self.assertIsNone(self.отметка(второй))
			self.assertIsNone(frappe.flags.get(browser_progress.ЗАКРЫВАЕТСЯ))

		frappe.set_user("Administrator")
		self.завести(статус="Partially Complete")
		frappe.set_user(self.ученик)
		with self.subTest("снятую отметку не закрыть SCORM под другим именем метода"):
			параметры = {"lesson": self.урок, "course": self.курс, "scorm_details": {"is_complete": 1}}

			with (
				patch.object(frappe.local, "form_dict", frappe._dict(параметры)),
				patch.object(frappe.local, "request", SimpleNamespace(method="POST"), create=True),
				self.assertRaises(frappe.ValidationError),
			):
				execute_cmd("lms.lms.api.save_progress")

			self.assertEqual(self.отметка(), "Partially Complete")

	def test_занятие_закрывает_урок(self):
		"""И поверх снятой отметки: отметку, снятую администратором, повторное
		прохождение возвращает. У каждого случая свой ученик."""
		for что, прежняя in (("без отметки", None), ("поверх снятой отметки", "Partially Complete")):
			with self.subTest(что):
				self.новый_ученик()
				if прежняя:
					self.завести(статус=прежняя)
				занятие = frappe.get_doc(
					"Agent Learning Session", занятие_релиза(self.ученик, self.курс, "l-1")
				)
				frappe.set_user(self.ученик)

				quiz.отметить_урок_пройденным(занятие)

				self.assertEqual(self.отметка(), ПРОЙДЕН)

	def test_администратор_правит_отметку_в_desk(self):
		"""Прогресс руками правит тот, кому Desk даёт права на отметки, — без занятия."""
		администратор = создать_куратора(
			f"release-progress-sm-{frappe.generate_hash(length=6)}@example.com", "System Manager"
		)
		frappe.set_user(администратор)

		отметка = self.завести()
		отметка.status = "Partially Complete"
		отметка.save()

		self.assertEqual(self.отметка(), "Partially Complete")

	def test_на_курсе_без_релиза_mark_lesson_progress_закрывает_урок(self):
		урок = создать_урок("Урок с квизом Learning")
		курс = зачислить(self.ученик, урок)
		глава, номер = self.номер(урок)
		frappe.set_user(self.ученик)

		mark_lesson_progress(курс, глава, номер)

		self.assertEqual(self.отметка(урок), ПРОЙДЕН)
