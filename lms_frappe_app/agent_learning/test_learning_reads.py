# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Оглавление, урок и каталог Learning — по правилу витрины (learning-services#692)."""

import sys

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from lms.lms import utils as learning

from lms_frappe_app.tests.sample_data import зачислить, создать_куратора, создать_урок, создать_ученика

МОДУЛЬ = "lms_frappe_app.agent_learning.learning_reads"


def подмены() -> dict[str, str]:
	"""HTTP-имя метода Learning → подмена этого модуля, из хуков."""
	return {
		имя: подмены[-1]
		for имя, подмены in frappe.get_hooks("override_whitelisted_methods").items()
		if подмены[-1].startswith(f"{МОДУЛЬ}.")
	}


def вызвать(имя: str, *args):
	"""Тем путём, каким идёт запрос страницы: функция по HTTP-имени после подмены."""
	return frappe.get_attr(frappe.override_whitelisted_method(f"lms.lms.utils.{имя}"))(*args)


class TestПодменыЧтенийLearning(UnitTestCase):
	"""Без базы: каждое HTTP-имя подменённой функции Learning уходит в подмену.

	`Why:` подмена сверяет строку вызова, а не функцию: имя, под которым
	Learning импортировал функцию в другой модуль, было бы входом мимо неё.
	"""

	def test_каждое_имя_подменено(self):
		# Модули с методами Learning загружены и тогда, когда тест идёт первым.
		import lms.lms.api

		for исходное, подмена in подмены().items():
			функция = getattr(learning, исходное.rsplit(".", 1)[1])
			имена = {
				f"{имя_модуля}.{имя}"
				for имя_модуля, модуль in list(sys.modules.items())
				if имя_модуля.startswith("lms.")
				for имя, значение in list(getattr(модуль, "__dict__", {}).items())
				if значение is функция
			}
			for имя in имена:
				with self.subTest(имя):
					self.assertEqual(frappe.override_whitelisted_method(имя), подмена)
		self.assertEqual(
			set(подмены()),
			{
				f"lms.lms.utils.{имя}"
				for имя in ("get_course_outline", "get_lesson", "get_courses", "get_course_count")
			},
		)


class IntegrationTestЧтенияLearning(IntegrationTestCase):
	"""Состав курса вне витрины — записанному и персоналу; каталог — опубликованные."""

	def test_оглавление_урок_и_каталог_по_витрине(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		ученик = создать_ученика(f"lr-{суффикс}@example.com")
		записанный = создать_ученика(f"lr-in-{суффикс}@example.com")
		модератор = создать_куратора(f"lr-mod-{суффикс}@example.com", "Moderator")
		урок = создать_урок(f"Чтения {суффикс}")
		зачислить(записанный, урок)
		курс = frappe.db.get_value("Course Lesson", урок, "course")
		витрина = {
			"неопубликованный": ({"published": 0, "upcoming": 0}, False, False),
			"анонс": ({"published": 1, "upcoming": 1}, False, True),
			"опубликованный": ({"published": 1, "upcoming": 0}, True, True),
		}
		for что, (поля, состав_открыт, в_каталоге) in витрина.items():
			frappe.db.set_value("LMS Course", курс, поля)
			for кто, свой in ((ученик, False), (записанный, True), (модератор, True)):
				with self.subTest(что, кто=кто):
					frappe.set_user(кто)
					try:
						открыт = состав_открыт or свой
						self.assertEqual(bool(вызвать("get_course_outline", курс)), открыт)
						self.assertEqual(bool(вызвать("get_lesson", курс, 1, 1)), открыт)
						каталог = {к.name for к in вызвать("get_courses", {"name": курс})}
						self.assertEqual(курс in каталог, в_каталоге or кто == модератор)
						self.assertEqual(вызвать("get_course_count", {"name": курс}), len(каталог))
						if кто == записанный:
							свои = {к.name for к in вызвать("get_courses", {"enrolled": 1})}
							self.assertIn(курс, свои)
					finally:
						frappe.set_user("Administrator")
