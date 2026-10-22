# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Чтения Learning: состав курса и каталог — по правилу витрины, статистика —
персоналу (learning-services#692)."""

import sys

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

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
	"""Тем путём, каким идёт запрос страницы: функция по HTTP-имени после подмены.
	`имя` без модуля — из `lms.lms.utils`."""
	полное = имя if "." in имя else f"lms.lms.utils.{имя}"
	return frappe.get_attr(frappe.override_whitelisted_method(полное))(*args)


class TestПодменыЧтенийLearning(UnitTestCase):
	"""Без базы: каждое HTTP-имя подменённой функции Learning уходит в подмену.

	`Why:` подмена сверяет строку вызова, а не функцию: имя, под которым
	Learning импортировал функцию в другой модуль, было бы входом мимо неё.
	"""

	def test_каждое_имя_подменено(self):
		# Модули с методами Learning загружены и тогда, когда тест идёт первым.
		import lms.lms.api

		for исходное, подмена in подмены().items():
			функция = frappe.get_attr(исходное)
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
				"lms.lms.api.get_chart_details",
				*(
					f"lms.lms.utils.{имя}"
					for имя in (
						"get_course_outline",
						"get_lesson",
						"get_courses",
						"get_course_count",
						"get_reviews",
						"get_batches",
						"get_batch_count",
						"get_chart_data",
						"get_course_completion_data",
					)
				),
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
		frappe.get_doc(
			{
				"doctype": "LMS Course Review",
				"name": frappe.generate_hash(length=10),
				"course": курс,
				"review": "Отзыв",
				"rating": 1,
			}
		).db_insert()
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
						self.assertEqual(bool(вызвать("get_reviews", курс)), открыт)
						каталог = {к.name for к in вызвать("get_courses", {"name": курс})}
						self.assertEqual(курс in каталог, в_каталоге or кто == модератор)
						self.assertEqual(вызвать("get_course_count", {"name": курс}), len(каталог))
						if кто == записанный:
							свои = {к.name for к in вызвать("get_courses", {"enrolled": 1})}
							self.assertIn(курс, свои)
					finally:
						frappe.set_user("Administrator")

	def test_группы_по_витрине(self):
		"""Неопубликованную группу видят только её участник — во вкладке своих — и персонал."""
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		ученик = создать_ученика(f"lb-{суффикс}@example.com")
		участник = создать_ученика(f"lb-in-{суффикс}@example.com")
		модератор = создать_куратора(f"lb-mod-{суффикс}@example.com", "Moderator")
		группа = frappe.get_doc(
			{
				"doctype": "LMS Batch",
				"title": f"Группа {суффикс}",
				"start_date": "2099-01-01",
				"end_date": "2099-12-31",
				"start_time": "10:00:00",
				"end_time": "11:00:00",
			}
		)
		группа.flags.ignore_validate = True
		группа.flags.ignore_mandatory = True
		группа = группа.insert(ignore_permissions=True).name
		frappe.get_doc(
			{
				"doctype": "LMS Batch Enrollment",
				"name": frappe.generate_hash(length=10),
				"batch": группа,
				"member": участник,
			}
		).db_insert()
		for опубликована in (0, 1):
			frappe.db.set_value("LMS Batch", группа, "published", опубликована)
			for кто, видит in (
				(ученик, bool(опубликована)),
				(участник, bool(опубликована)),
				(модератор, True),
			):
				with self.subTest(опубликована=опубликована, кто=кто):
					frappe.set_user(кто)
					try:
						видимые = {г.name for г in вызвать("get_batches", {"name": группа})}
						self.assertEqual(группа in видимые, видит)
						self.assertEqual(вызвать("get_batch_count", {"name": группа}), len(видимые))
						if кто == участник:
							свои = {г.name for г in вызвать("get_batches", {"enrolled": 1})}
							self.assertIn(группа, свои)
					finally:
						frappe.set_user("Administrator")

	def test_статистика_только_персоналу(self):
		"""Страница «Статистика» — для персонала; методы под ней — тоже."""
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		ученик = создать_ученика(f"ls-{суффикс}@example.com")
		модератор = создать_куратора(f"ls-mod-{суффикс}@example.com", "Moderator")
		вызовы = {
			"lms.lms.api.get_chart_details": (),
			"get_course_completion_data": (),
			"get_chart_data": ("Нет такого графика",),
		}
		for имя, аргументы in вызовы.items():
			with self.subTest(имя):
				frappe.set_user(ученик)
				try:
					with self.assertRaises(frappe.PermissionError):
						вызвать(имя, *аргументы)
				finally:
					frappe.set_user("Administrator")
		frappe.set_user(модератор)
		self.assertIn("enrollments", вызвать("lms.lms.api.get_chart_details"))
		self.assertEqual(len(вызвать("get_course_completion_data")), 2)
