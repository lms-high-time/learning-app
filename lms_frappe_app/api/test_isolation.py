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

	def test_рядовой_ученик_не_получает_чужие_подробности(self):
		frappe.set_user(self.ученик)
		ответ = manager.student_detail(self.чужой)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], manager.ЧУЖОЙ_УЧЕНИК)

	def test_менеджер_не_получает_ученика_чужой_компании(self):
		менеджер = создать_менеджера(
			f"i-m-{frappe.generate_hash(length=6)}@example.com", self.компания_а
		)
		frappe.set_user(менеджер)

		своё = manager.student_detail(self.ученик)
		self.assertTrue(своё["ok"])
		self.assertEqual(своё["data"]["user"], self.ученик)

		ответ = manager.student_detail(self.чужой)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], manager.ЧУЖОЙ_УЧЕНИК)

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

		ответ = student.submit_answer(попытка, "S1/l-1-D1", "V1", "слова ученика")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ЧУЖОЕ_ЗАНЯТИЕ)

	# --- прямая запись мимо методов ---

	def test_ученик_не_может_создать_себе_членство(self):
		# Иначе он вписывается в любую компанию и получает её курсы.
		frappe.set_user(self.ученик)
		членство = frappe.get_doc(
			{
				"doctype": "Organization Membership",
				"user": self.ученик,
				"organization": self.компания_б,
				"role": "Manager",
			}
		)
		self.assertFalse(
			frappe.has_permission("Organization Membership", "create", doc=членство)
		)

	def test_ученик_не_может_править_назначения(self):
		frappe.set_user(self.ученик)
		self.assertFalse(frappe.has_permission("Course Allocation", "write"))
		self.assertFalse(frappe.has_permission("Course Allocation", "create"))

	def test_ученик_не_может_править_своё_занятие(self):
		своё = создать_занятие(self.ученик, self.урок)
		frappe.set_user(self.ученик)
		self.assertFalse(
			frappe.has_permission("Agent Learning Session", "write", doc=своё)
		)

	def test_чтение_остаётся_доступным(self):
		# Урезание прав не должно сломать обычную работу.
		frappe.set_user(self.ученик)
		self.assertTrue(frappe.has_permission("Agent Learning Session", "read"))
		self.assertTrue(student.list_my_courses()["ok"])


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
		релиз = пример_релиза(f"answers-{суффикс}")
		релиз["lessons"][0]["quiz"]["questions"][0]["options"] = [
			{"key": "V1", "text": "Москва"},
			{"key": "V2", "text": "Тула"},
		]
		курс, _ = курс_из_релиза(релиз=релиз)

		self.сотрудник = создать_ученика(f"emp-{суффикс}@example.com")
		добавить_в_организацию(self.сотрудник, self.компания)
		# Курс даёт компания: руководителю видна работа в её пространстве (#344).
		frappe.get_doc(
			{"doctype": "Course Allocation", "organization": self.компания, "course": зачислить_на_курс(self.сотрудник, курс)}
		).insert(ignore_permissions=True)
		self.руководитель = создать_менеджера(f"boss-{суффикс}@example.com", self.компания)

		занятие = занятие_релиза(self.сотрудник, курс, "l-1")
		отметить_все_пункты(frappe.db.get_value("Agent Learning Session", занятие, "run"))
		frappe.set_user(self.сотрудник)
		попытка = student.request_quiz(занятие)["data"]
		student.submit_answer(попытка["attempt"], попытка["question"]["id"], "V1", "слова ученика")
		frappe.set_user("Administrator")

	def test_записи_ответов_не_читает_никто_кроме_служебных_ролей(self):
		"""Ни руководитель, ни сам ученик: вердикт приходит из метода.

		Записи ответов вообще не выставлены наружу — так утечка невозможна не
		по фильтру, а по отсутствию права. Руководителю агрегаты доступны
		методом отчётности, где текстов ответов нет.
		"""
		for кто in (self.руководитель, self.сотрудник):
			frappe.set_user(кто)
			self.assertFalse(
				frappe.has_permission("Agent Quiz Answer", "read"),
				f"{кто} получил доступ к записям ответов",
			)

	def test_руководитель_видит_результат_но_не_ответы(self):
		frappe.set_user(self.руководитель)
		данные = manager.student_detail(self.сотрудник)["data"]

		self.assertTrue(данные["quiz_attempts"], "итоги попыток руководителю нужны")
		выдано = json.dumps(данные, ensure_ascii=False, default=str)
		self.assertNotIn("Москва", выдано)
		self.assertNotIn("is_correct", выдано)


	def test_заметки_ученика_не_видит_руководитель(self):
		"""Заметки — про разговор, а не про результат.

		Граница проходит по сущности целиком: руководителю не отдаётся ни
		методом, ни прямым чтением записи.
		"""
		frappe.set_user(self.сотрудник)
		student.remember(kind="fact", key="role", text="Директор по маркетингу")

		frappe.set_user(self.руководитель)
		выдано = json.dumps(
			manager.student_detail(self.сотрудник), ensure_ascii=False, default=str
		)

		self.assertNotIn("Директор по маркетингу", выдано)
		# get_list, а не get_all: права применяет только он, и проверять
		# нужно именно их — фильтр в самом методе к делу не относится.
		self.assertFalse(
			frappe.get_list(
				"Agent Student Note", filters={"student": self.сотрудник}, limit=1
			),
			"руководитель не должен видеть заметок своих людей",
		)

	def test_чужую_заметку_не_прочитать_и_не_забыть(self):
		frappe.set_user(self.сотрудник)
		student.remember(kind="fact", key="role", text="Директор по маркетингу")

		frappe.set_user(self.руководитель)

		self.assertEqual(student.my_notes()["data"]["facts"], [])
		self.assertEqual(
			student.forget(key="role")["error"]["code"], student.ЗАМЕТКА_НЕ_НАЙДЕНА
		)


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
		frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"course": self.курс,
				"slug": "summary",
				"title": "Резюме проекта",
				"blocks": [{"block_key": "goal", "title": "Цель"}],
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.сотрудник)
		student.update_artifact(self.курс, "summary", "goal", "Открыть седьмую кофейню")
		self.документ = frappe.db.get_value(
			"Agent Student Artifact", {"student": self.сотрудник, "artifact": "summary"}
		)
		frappe.set_user("Administrator")

	def test_коллега_видит_свой_пустой_документ_а_не_чужой(self):
		frappe.set_user(self.коллега)

		блоки = student.artifact(self.курс, "summary")["data"]["blocks"]

		self.assertEqual(блоки[0]["content"], "")
		self.assertFalse(
			frappe.has_permission("Agent Student Artifact", "read", doc=self.документ)
		)

	def test_руководитель_не_видит_документов_своих_людей(self):
		"""Менеджеру идёт покрытие целей; черновик резюме — не отчётность."""
		frappe.set_user(self.руководитель)

		self.assertFalse(
			frappe.get_list("Agent Student Artifact", filters={"student": self.сотрудник}, limit=1)
		)
		self.assertFalse(
			frappe.has_permission("Agent Student Artifact", "read", doc=self.документ)
		)
		выдано = json.dumps(manager.student_detail(self.сотрудник), ensure_ascii=False, default=str)
		self.assertNotIn("седьмую кофейню", выдано)

	def test_ученик_не_правит_документ_напрямую(self):
		frappe.set_user(self.сотрудник)

		self.assertTrue(frappe.has_permission("Agent Student Artifact", "read", doc=self.документ))
		self.assertFalse(frappe.has_permission("Agent Student Artifact", "write", doc=self.документ))
