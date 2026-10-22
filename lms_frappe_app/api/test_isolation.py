# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Изоляция на том же пути, по которому идут настоящие запросы.

`Why:` прежние тесты изоляции ходили через `frappe.get_list`, а код —
через `frappe.get_all`, то есть `get_list(ignore_permissions=True)`. Права к
нему не применяются никогда, и десять зелёных тестов соседствовали с
работающей утечкой. Здесь всё проверяется вызовом методов API и прямым
обращением к записям, как это делает настоящий агент.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	раздел,
	схема_документа,
	добавить_в_организацию,
	занятие_релиза,
	зачислить_на_курс,
	курс_из_релиза,
	урок_релиза,
	отметить_все_пункты,
	создать_занятие,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
	создать_урок,
	зачислить,
)
from lms_frappe_app.api import manager, student


class IntegrationTestApiIsolation(IntegrationTestCase):
	"""Чужое недоступно ни методом, ни прямой записью."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)

		self.компания_а = создать_организацию(f"Компания А {суффикс}")
		self.компания_б = создать_организацию(f"Компания Б {суффикс}")
		self.ученик = создать_ученика(f"i-a-{суффикс}@example.com")
		self.чужой = создать_ученика(f"i-b-{суффикс}@example.com")
		добавить_в_организацию(self.ученик, self.компания_а)
		добавить_в_организацию(self.чужой, self.компания_б)

		self.курс, _ = курс_из_релиза()
		self.урок = урок_релиза(self.курс, "l-1")
		# Оба зачислены: методы занятия проверяют доступ к курсу, и без
		# зачисления тесты изоляции падали бы по другой причине, чем проверяют.
		зачислить_на_курс(self.ученик, self.курс)
		зачислить_на_курс(self.чужой, self.курс)
		self.чужое_занятие = занятие_релиза(self.чужой, self.курс, "l-1")

	# --- отчётность ---

	def test_подробности_ученика_только_своей_компании(self):
		"""Рядовой ученик чужих подробностей не получает; менеджер получает своего
		ученика, а ученика чужой компании — нет."""
		менеджер = создать_менеджера(f"i-m-{frappe.generate_hash(length=6)}@example.com", self.компания_а)
		случаи = {
			"рядовой ученик → чужой": (self.ученик, self.чужой, manager.ЧУЖОЙ_УЧЕНИК),
			"менеджер → свой": (менеджер, self.ученик, None),
			"менеджер → чужой компании": (менеджер, self.чужой, manager.ЧУЖОЙ_УЧЕНИК),
		}
		for что, (кто, чей, код) in случаи.items():
			with self.subTest(что):
				frappe.set_user(кто)
				ответ = manager.student_detail(чей)

				if код is None:
					self.assertTrue(ответ["ok"])
					self.assertEqual(ответ["data"]["user"], self.ученик)
				else:
					self.assertFalse(ответ["ok"])
					self.assertEqual(ответ["error"]["code"], код)

	# --- чужое занятие и чужая попытка ---

	def test_действия_в_чужом_занятии_отклоняются(self):
		"""Каждый метод занятия проверяет хозяина сам.

		Отказ, а не исключение прав: агенту нужен код, по которому он
		объяснит ученику происходящее. Проверка идёт по принадлежности
		занятия, а не по праву чтения — читать чужое занятие вправе ещё и
		руководитель, но действовать в нём он не должен.
		"""
		frappe.set_user(self.ученик)
		действия = {
			"отметка пункта": lambda: student.mark_goal(self.чужое_занятие, "term:T1", "done", "не моё занятие"),
			# Пункты урока — инструмент агента чужого ученика.
			"подробности пункта": lambda: student.lesson_item(self.чужое_занятие, "term:T1"),
			# Иначе можно сжечь чужую попытку — они лимитированы.
			"квиз": lambda: student.request_quiz(self.чужое_занятие),
			"закрытие урока": lambda: student.complete_lesson(self.чужое_занятие),
			# Репорт берёт курс и урок из занятия: пропущенный сюда, он
			# записал бы чужой урок словами не того ученика.
			"репорт": lambda: student.report_issue(
				session=self.чужое_занятие, kind="stuck", text="не моё занятие"
			),
		}

		for имя, действие in действия.items():
			with self.subTest(имя):
				ответ = действие()

				self.assertFalse(ответ["ok"])
				self.assertEqual(ответ["error"]["code"], student.ЧУЖОЕ_ЗАНЯТИЕ)

	def test_чужая_попытка_недоступна(self):
		"""Открытую методом попытку не правит напрямую даже хозяин, а чужой
		не читает и не отвечает в неё.

		Проверки не мешают друг другу: права только читаются, а ответ в чужую
		попытку отклоняется раньше, чем что-либо запишется.
		"""
		занятие = self.чужое_занятие
		отметить_все_пункты(frappe.db.get_value("Agent Learning Session", занятие, "run"))
		frappe.set_user(self.чужой)
		попытка = student.request_quiz(занятие)["data"]["attempt"]

		# Хозяин не правит свою попытку напрямую. Главное: иначе зачёт ставится
		# без единого ответа. Проверено эксплуатацией до починки — PUT со
		# `score` проходил.
		self.assertFalse(
			frappe.has_permission("Agent Quiz Attempt", "write", doc=попытка, user=self.чужой)
		)

		frappe.set_user(self.ученик)

		# Чужая попытка не читается даже по имени.
		self.assertFalse(frappe.has_permission("Agent Quiz Attempt", "read", doc=попытка))

		ответ = student.submit_answer(попытка, "S1/l-1-D1", "Первый", "слова ученика")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ЧУЖОЕ_ЗАНЯТИЕ)

	# --- прямая запись мимо методов ---

	def test_прямая_запись_мимо_методов_закрыта_а_чтение_нет(self):
		"""Ученик не создаёт себе членство — иначе вписывается в любую компанию и
		получает её курсы; не правит назначения и своё занятие. Урезание прав не
		должно сломать обычную работу: чтение остаётся."""
		своё = создать_занятие(self.ученик, self.урок)
		frappe.set_user(self.ученик)
		членство = frappe.get_doc(
			{
				"doctype": "Organization Membership",
				"user": self.ученик,
				"organization": self.компания_б,
				"role": "Manager",
			}
		)
		права = {
			"создать себе членство": (
				lambda: frappe.has_permission("Organization Membership", "create", doc=членство),
				False,
			),
			"править назначения": (lambda: frappe.has_permission("Course Allocation", "write"), False),
			"создать назначение": (lambda: frappe.has_permission("Course Allocation", "create"), False),
			"править своё занятие": (
				lambda: frappe.has_permission("Agent Learning Session", "write", doc=своё),
				False,
			),
			"читать занятия": (lambda: frappe.has_permission("Agent Learning Session", "read"), True),
			"мои курсы": (lambda: student.list_my_courses()["ok"], True),
		}
		for что, (проверка, можно) in права.items():
			with self.subTest(что):
				self.assertEqual(bool(проверка()), можно)


class IntegrationTestQuizAnswerLeak(IntegrationTestCase):
	"""Ответы квиза — только свои, даже руководителю.

	`Why:` в записи ответа лежит текст ответа рядом с признаком верности —
	готовый эталон. Руководитель нередко проходит тот же курс, что и его
	сотрудники: чтение чужих ответов дало бы ему ответы на собственный квиз.
	"""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.компания = создать_организацию(f"Компания {суффикс}")
		курс, _ = курс_из_релиза(релиз=пример_релиза(f"answers-{суффикс}"))

		self.сотрудник = создать_ученика(f"emp-{суффикс}@example.com")
		добавить_в_организацию(self.сотрудник, self.компания)
		# Курс даёт компания: руководителю видна работа в её пространстве (#344).
		frappe.get_doc(
			{"doctype": "Course Allocation", "organization": self.компания, "course": зачислить_на_курс(self.сотрудник, курс)}
		).insert(ignore_permissions=True)
		self.руководитель = создать_менеджера(f"boss-{суффикс}@example.com", self.компания)
		self.курс = курс

	def test_записи_ответов_не_читает_никто_кроме_служебных_ролей(self):
		"""Ни руководитель, ни сам ученик: вердикт приходит из метода.

		Записи ответов вообще не выставлены наружу — так утечка невозможна не
		по фильтру, а по отсутствию права. Руководителю агрегаты доступны
		методом отчётности, где текстов ответов нет.
		"""
		занятие = занятие_релиза(self.сотрудник, self.курс, "l-1")
		отметить_все_пункты(frappe.db.get_value("Agent Learning Session", занятие, "run"))
		frappe.set_user(self.сотрудник)
		попытка = student.request_quiz(занятие)["data"]
		student.submit_answer(попытка["attempt"], попытка["question"]["id"], "Первый", "слова ученика")
		frappe.set_user("Administrator")

		for кто in (self.руководитель, self.сотрудник):
			frappe.set_user(кто)
			self.assertFalse(
				frappe.has_permission("Agent Quiz Answer", "read"),
				f"{кто} получил доступ к записям ответов",
			)

	def test_заметки_ученика_руководителю_недоступны(self):
		"""Заметки — про разговор, а не про результат.

		Граница проходит по сущности целиком: руководителю не отдаётся ни
		методом, ни прямым чтением записи; чужую заметку он не прочитает и не
		забудет своими методами ученика.
		"""
		frappe.set_user(self.сотрудник)
		student.remember(kind="fact", key="role", text="Директор по маркетингу")

		frappe.set_user(self.руководитель)
		with self.subTest("подробности ученика"):
			выдано = json.dumps(manager.student_detail(self.сотрудник), ensure_ascii=False, default=str)
			self.assertNotIn("Директор по маркетингу", выдано)
		with self.subTest("прямое чтение записи"):
			# get_list, а не get_all: права применяет только он, и проверять
			# нужно именно их — фильтр в самом методе к делу не относится.
			self.assertFalse(
				frappe.get_list("Agent Student Note", filters={"student": self.сотрудник}, limit=1),
				"руководитель не должен видеть заметок своих людей",
			)
		with self.subTest("my_notes"):
			self.assertEqual(student.my_notes()["data"]["facts"], [])
		with self.subTest("forget"):
			self.assertEqual(student.forget(key="role")["error"]["code"], student.ЗАМЕТКА_НЕ_НАЙДЕНА)


class IntegrationTestArtifactIsolation(IntegrationTestCase):
	"""Артефакт — рабочий документ ученика: свой и только свой."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.компания = создать_организацию(f"Компания {суффикс}")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.сотрудник = создать_ученика(f"art-a-{суффикс}@example.com")
		self.коллега = создать_ученика(f"art-b-{суффикс}@example.com")
		добавить_в_организацию(self.сотрудник, self.компания)
		добавить_в_организацию(self.коллега, self.компания)
		self.курс = зачислить(self.сотрудник, self.урок)
		зачислить(self.коллега, self.урок)
		self.руководитель = создать_менеджера(f"art-m-{суффикс}@example.com", self.компания)
		схема_документа(self.курс, "summary", "Резюме проекта", [раздел("goal", "Цель")])

		frappe.set_user(self.сотрудник)
		student.update_artifact(self.курс, "summary", "goal", fields={"goal": "Открыть седьмую кофейню"})
		self.документ = frappe.db.get_value(
			"Agent Student Artifact", {"student": self.сотрудник, "artifact": "summary"}
		)
		frappe.set_user("Administrator")

	def test_чужой_документ_не_виден_коллеге_и_руководителю(self):
		"""Коллега видит свой пустой документ, а не чужой. Менеджеру идёт покрытие
		целей; черновик резюме — не отчётность."""
		with self.subTest("коллега"):
			frappe.set_user(self.коллега)

			блоки = student.artifact(self.курс, "summary")["data"]["blocks"]

			self.assertEqual(блоки[0]["values"], {})
			self.assertFalse(frappe.has_permission("Agent Student Artifact", "read", doc=self.документ))

		with self.subTest("руководитель"):
			frappe.set_user(self.руководитель)

			self.assertFalse(
				frappe.get_list("Agent Student Artifact", filters={"student": self.сотрудник}, limit=1)
			)
			self.assertFalse(frappe.has_permission("Agent Student Artifact", "read", doc=self.документ))
			выдано = json.dumps(manager.student_detail(self.сотрудник), ensure_ascii=False, default=str)
			self.assertNotIn("седьмую кофейню", выдано)
