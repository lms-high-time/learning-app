# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Анонс курса: виден в каталоге, записи нет, есть «сообщить о выходе» (#389).

Запрет записи проверяется там же, где его обходят: методом контракта,
который ученик может позвать своим токеном мимо агента, и сохранением
назначения, которое создаёт зачисления само.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import announcements, notices
from lms_frappe_app.api import authoring, public, student, team
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_куратора,
	создать_курс,
	создать_менеджера,
	создать_организацию,
	создать_урок,
	создать_ученика,
)

ЦЕЛИ = "Описать процесс в пять колонок\nНайти стыки между отделами"


class IntegrationTestAnnouncements(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.письма = patch("frappe.sendmail").start()
		patch.object(notices, "почта_есть", return_value=True).start()
		self.addCleanup(patch.stopall)
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"an-c-{суффикс}@example.com")
		self.ученик = создать_ученика(f"an-s-{суффикс}@example.com")
		self.компания = создать_организацию(f"Анонс {суффикс}")
		добавить_в_организацию(self.ученик, self.компания)
		self.руководитель = создать_менеджера(f"an-m-{суффикс}@example.com", self.компания)
		self.курс = создать_курс(f"Анонс {суффикс}")

	def от_имени(self, кто: str, метод, **аргументы) -> dict:
		frappe.set_user(кто)
		try:
			return метод(**аргументы)
		finally:
			frappe.set_user("Administrator")

	def анонсировать(self) -> dict:
		ответ = self.от_имени(self.куратор, authoring.announce_course, course=self.курс, objectives=ЦЕЛИ)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def опубликовать_релиз(self, **аргументы) -> dict:
		ответ = self.от_имени(
			self.куратор,
			authoring.publish_release,
			release=аргументы.pop("release", None) or пример_релиза(f"an-{frappe.generate_hash(length=6)}"),
			**аргументы,
		)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def код_отказа(self, ответ: dict) -> str | None:
		return None if ответ["ok"] else ответ["error"]["code"]

	# --- куратор ---

	def test_анонс_без_целей_курса_отклоняется(self):
		for цели in (None, "", " \n ", []):
			аргументы = {} if цели is None else {"objectives": цели}
			ответ = self.от_имени(self.куратор, authoring.announce_course, course=self.курс, **аргументы)
			self.assertEqual(self.код_отказа(ответ), authoring.НЕТ_ЦЕЛЕЙ_КУРСА, цели)
		сведения = frappe.db.get_value(
			"LMS Course", self.курс, ["published", "announce_objectives"], as_dict=True
		)
		self.assertFalse(сведения.published)
		self.assertFalse(сведения.announce_objectives)

	def test_анонс_не_требует_уроков_и_отдаёт_цели(self):
		данные = self.анонсировать()
		self.assertEqual(
			данные["objectives"], ["Описать процесс в пять колонок", "Найти стыки между отделами"]
		)
		сведения = frappe.db.get_value(
			"LMS Course", self.курс, ["published", "upcoming", "announce_objectives"], as_dict=True
		)
		self.assertEqual((сведения.published, сведения.upcoming), (1, 1))
		self.assertEqual(сведения.announce_objectives, ЦЕЛИ)

	def test_цели_анонса_списком_и_повторный_анонс_без_целей(self):
		"""Список и строка JSON — те же цели; без `objectives` действуют записанные."""
		for цели in (["Первая", " ", "Вторая "], '["Первая", "Вторая"]'):
			ответ = self.от_имени(self.куратор, authoring.announce_course, course=self.курс, objectives=цели)
			self.assertEqual(ответ["data"]["objectives"], ["Первая", "Вторая"], цели)
		self.от_имени(self.куратор, authoring.unpublish_course, course=self.курс)

		ответ = self.от_имени(self.куратор, authoring.announce_course, course=self.курс)

		self.assertEqual(ответ["data"]["objectives"], ["Первая", "Вторая"])
		self.assertTrue(frappe.db.get_value("LMS Course", self.курс, "upcoming"))

	def test_новые_цели_заменяют_прежние(self):
		self.анонсировать()

		ответ = self.от_имени(
			self.куратор, authoring.announce_course, course=self.курс, objectives="Одна цель"
		)

		self.assertEqual(ответ["data"]["objectives"], ["Одна цель"])
		курсы = self.от_имени(self.ученик, student.list_catalog)["data"]["courses"]
		self.assertEqual(next(к for к in курсы if к["id"] == self.курс)["objectives"], ["Одна цель"])

	def test_курс_без_релиза_с_уроками_не_анонсируется(self):
		"""Программу курсу даёт релиз: курс без релиза анонсируется только без уроков."""
		курс = frappe.db.get_value("Course Lesson", создать_урок(f"Урок {frappe.generate_hash(length=6)}"), "course")

		ответ = self.от_имени(self.куратор, authoring.announce_course, course=курс, objectives=ЦЕЛИ)

		self.assertEqual(self.код_отказа(ответ), "course_has_content")
		self.assertEqual((ответ["error"]["course"], ответ["error"]["lessons"]), (курс, 1))
		сведения = frappe.db.get_value(
			"LMS Course", курс, ["published", "upcoming", "announce_objectives"], as_dict=True
		)
		self.assertEqual((сведения.published, сведения.upcoming), (0, 0))
		self.assertFalse(сведения.announce_objectives)

	def test_открытый_курс_анонсом_не_становится(self):
		frappe.db.set_value("LMS Course", self.курс, "published", 1)
		ответ = self.от_имени(self.куратор, authoring.announce_course, course=self.курс)
		self.assertEqual(self.код_отказа(ответ), authoring.КУРС_УЖЕ_ОТКРЫТ)

	def test_снятие_с_публикации_снимает_и_анонс(self):
		self.анонсировать()
		self.от_имени(self.куратор, authoring.unpublish_course, course=self.курс)
		сведения = frappe.db.get_value("LMS Course", self.курс, ["published", "upcoming"], as_dict=True)
		self.assertEqual((сведения.published, сведения.upcoming), (0, 0))

	def test_список_курсов_отмечает_анонс(self):
		self.анонсировать()
		курсы = self.от_имени(self.куратор, authoring.list_courses)["data"]["courses"]
		строка = next(к for к in курсы if к["id"] == self.курс)
		self.assertTrue(строка["upcoming"])

	# --- ученик ---

	def test_каталог_показывает_анонс_с_целями(self):
		self.анонсировать()
		курсы = self.от_имени(self.ученик, student.list_catalog)["data"]["courses"]
		строка = next(к for к in курсы if к["id"] == self.курс)
		self.assertTrue(строка["upcoming"])
		self.assertEqual(len(строка["objectives"]), 2)
		self.assertFalse(строка["notify"])

	def test_на_анонс_не_записаться_ни_лично_ни_в_организации(self):
		self.анонсировать()
		for space in (None, self.компания):
			ответ = self.от_имени(self.ученик, student.enroll, course=self.курс, space=space)
			self.assertEqual(self.код_отказа(ответ), "course_upcoming")
		self.assertFalse(frappe.db.exists("LMS Enrollment", {"member": self.ученик, "course": self.курс}))

	def test_подписка_одна_и_видна_в_каталоге(self):
		self.анонсировать()
		for _ in range(2):
			ответ = self.от_имени(self.ученик, student.notify_when_released, course=self.курс)
			self.assertTrue(ответ["data"]["notify"])
		self.assertEqual(
			frappe.db.count("LMS Course Interest", {"user": self.ученик, "course": self.курс}), 1
		)
		курсы = self.от_имени(self.ученик, student.list_catalog)["data"]["courses"]
		self.assertTrue(next(к for к in курсы if к["id"] == self.курс)["notify"])

	def test_отписка_снимает_подписку_и_повторяется_без_ошибки(self):
		self.анонсировать()
		self.от_имени(self.ученик, student.notify_when_released, course=self.курс)
		for _ in range(2):
			ответ = self.от_имени(
				self.ученик, student.notify_when_released, course=self.курс, notify="false"
			)
			self.assertFalse(ответ["data"]["notify"])
		self.assertFalse(
			frappe.db.exists("LMS Course Interest", {"user": self.ученик, "course": self.курс})
		)
		курсы = self.от_имени(self.ученик, student.list_catalog)["data"]["courses"]
		self.assertFalse(next(к for к in курсы if к["id"] == self.курс)["notify"])

	def test_подписка_только_на_анонс(self):
		frappe.db.set_value("LMS Course", self.курс, "published", 1)
		ответ = self.от_имени(self.ученик, student.notify_when_released, course=self.курс)
		self.assertEqual(self.код_отказа(ответ), student.КУРС_НЕ_АНОНС)

	# --- страница курса ---

	def test_карта_анонса_без_программы(self):
		self.анонсировать()
		данные = self.от_имени("Guest", public.course_map, course=self.курс)["data"]
		self.assertTrue(данные["upcoming"])
		self.assertEqual(данные["chapters"], [])
		self.assertEqual(данные["documents"], [])
		self.assertEqual(len(данные["objectives"]), 2)
		self.assertNotIn("notify", данные)

		self.от_имени(self.ученик, student.notify_when_released, course=self.курс)
		данные = self.от_имени(self.ученик, public.course_map, course=self.курс)["data"]
		self.assertTrue(данные["notify"])

	def test_карта_открытого_курса_без_признака_анонса(self):
		frappe.db.set_value("LMS Course", self.курс, "published", 1)
		данные = self.от_имени("Guest", public.course_map, course=self.курс)["data"]
		self.assertNotIn("upcoming", данные)

	# --- руководитель ---

	def test_анонс_не_назначить_команде(self):
		self.анонсировать()
		данные = self.от_имени(self.руководитель, team.allocations, organization=self.компания)["data"]
		self.assertNotIn(self.курс, [к["id"] for к in данные["courses"]])
		ответ = self.от_имени(
			self.руководитель, team.assign_course, organization=self.компания, course=self.курс
		)
		self.assertEqual(self.код_отказа(ответ), "course_upcoming")
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": "Course Allocation", "organization": self.компания, "course": self.курс}
			).insert(ignore_permissions=True)

	# --- выход ---

	def test_анонс_без_релиза_не_открывается(self):
		self.анонсировать()

		ответ = self.от_имени(self.куратор, authoring.publish_course, course=self.курс)

		self.assertEqual(self.код_отказа(ответ), authoring.КУРС_БЕЗ_РЕЛИЗА)
		self.assertTrue(frappe.db.get_value("LMS Course", self.курс, "upcoming"))
		self.письма.assert_not_called()

	def test_выход_анонса_пишет_подписавшимся_один_раз(self):
		self.анонсировать()
		self.от_имени(self.ученик, student.notify_when_released, course=self.курс)
		self.опубликовать_релиз(course=self.курс)

		первый = self.от_имени(self.куратор, authoring.publish_course, course=self.курс)
		self.assertEqual(первый["data"]["notified"], 1)
		self.assertFalse(frappe.db.get_value("LMS Course", self.курс, "upcoming"))
		self.assertEqual(self.письма.call_args.kwargs["recipients"], [self.ученик])

		self.от_имени(self.куратор, authoring.unpublish_course, course=self.курс)
		второй = self.от_имени(self.куратор, authoring.publish_course, course=self.курс)
		self.assertEqual(второй["data"]["notified"], 0)
		self.assertEqual(self.письма.call_count, 1)

		ответ = self.от_имени(self.ученик, student.enroll, course=self.курс)
		self.assertTrue(ответ["ok"], ответ)

	def test_курс_из_релиза_анонсируется_целями_глав(self):
		"""Цели курса из релиза — цели глав, их названия по порядку: директивы у
		такого курса нет, и анонс без неё не отказывает (learning-services#500)."""
		курс = self.опубликовать_релиз()["course"]

		ответ = self.от_имени(self.куратор, authoring.announce_course, course=курс)

		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(ответ["data"]["objectives"], ["Глава первая", "Глава вторая"])
		каталог = self.от_имени(self.ученик, student.list_catalog)["data"]
		анонс = next(к for к in каталог["courses"] if к["id"] == курс)
		self.assertEqual(анонс["objectives"], ["Глава первая", "Глава вторая"])

	def test_курсу_из_релиза_цели_не_передаются(self):
		курс = self.опубликовать_релиз()["course"]

		ответ = self.от_имени(
			self.куратор, authoring.announce_course, course=курс, objectives="Своя цель"
		)

		self.assertEqual(self.код_отказа(ответ), authoring.КУРС_ИЗ_РЕЛИЗА)
		сведения = frappe.db.get_value(
			"LMS Course", курс, ["published", "announce_objectives"], as_dict=True
		)
		self.assertFalse(сведения.published)
		self.assertFalse(сведения.announce_objectives)

	def test_анонс_с_первым_релизом_берёт_цели_глав(self):
		"""Цели анонса остаются в поле, но у курса с релизом наружу — главы релиза."""
		self.анонсировать()

		self.опубликовать_релиз(course=self.курс)

		каталог = self.от_имени(self.ученик, student.list_catalog)["data"]
		анонс = next(к for к in каталог["courses"] if к["id"] == self.курс)
		self.assertEqual(анонс["objectives"], ["Глава первая", "Глава вторая"])
		ответ = self.от_имени(self.куратор, authoring.announce_course, course=self.курс)
		self.assertEqual(ответ["data"]["objectives"], ["Глава первая", "Глава вторая"])


class IntegrationTestПереносЦелейАнонса(IntegrationTestCase):
	"""Патч `announce_objectives`: цели директивы курса — в поле курса (learning-services#512)."""

	ПОЛЕ = "LMS Course-announce_objectives"

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.анонс = создать_курс(f"Перенос {суффикс}")
		self.заполненный = создать_курс(f"Заполненный {суффикс}")
		frappe.db.set_value("LMS Course", self.заполненный, "announce_objectives", "Своя цель")
		self.из_релиза = authoring.publish_release(release=пример_релиза(f"mv-{суффикс}"))["data"]["course"]
		действующие = {}
		for курс in (self.анонс, self.заполненный, self.из_релиза):
			self.директива(курс, "Старая цель")
			действующие[курс] = self.директива(курс, "Первая\n\nВторая ")
		# Снятая с действия свежая версия не переносится.
		frappe.db.set_value(
			"Agent Course Directive", self.директива(self.анонс, "Снятая"), "is_active", 0
		)
		frappe.db.set_value("Agent Course Directive", действующие[self.анонс], "is_active", 1)

	def директива(self, курс: str, цели: str) -> str:
		return frappe.get_doc(
			{"doctype": "Agent Course Directive", "course": курс, "objectives": цели, "is_active": 1}
		).insert(ignore_permissions=True).name

	def цели(self, курс: str) -> str | None:
		return frappe.db.get_value("LMS Course", курс, "announce_objectives")

	def test_переносит_анонсу_без_релиза_и_только_в_пустое_поле(self):
		from lms_frappe_app.patches.v0_1 import announce_objectives

		# Поле уже есть на тестовом сайте; DDL заведения поля фиксировал бы транзакцию теста.
		with patch.object(announce_objectives, "create_custom_field"):
			announce_objectives.execute()
			announce_objectives.execute()  # повторный запуск ничего не меняет

		self.assertEqual(self.цели(self.анонс), "Первая\nВторая")
		self.assertEqual(self.цели(self.заполненный), "Своя цель")
		self.assertFalse(self.цели(self.из_релиза))
		self.assertEqual(announcements.цели_курса(self.анонс), ["Первая", "Вторая"])

	def test_заводит_поле_как_в_фикстуре(self):
		"""Патчи идут раньше синхронизации фикстур: поле патч заводит сам — тем же,
		что в фикстуре. Вызов подменён: создание колонки фиксирует транзакцию теста."""
		import json
		from pathlib import Path

		from lms_frappe_app.patches.v0_1 import announce_objectives

		with patch.object(announce_objectives, "create_custom_field") as создать:
			announce_objectives.execute()

		создать.assert_called_once_with("LMS Course", announce_objectives.ПОЛЕ)
		фикстуры = json.loads(
			(Path(announce_objectives.__file__).parents[2] / "fixtures" / "custom_field.json").read_text(
				encoding="utf-8"
			)
		)
		фикстура = next(п for п in фикстуры if п["name"] == self.ПОЛЕ)
		self.assertEqual(
			{ключ: значение for ключ, значение in фикстура.items() if ключ not in ("doctype", "name", "dt")},
			announce_objectives.ПОЛЕ,
		)

	def test_без_таблицы_директив_только_поле(self):
		from lms_frappe_app.patches.v0_1 import announce_objectives

		with (
			patch.object(announce_objectives, "create_custom_field") as создать,
			patch.object(frappe.db, "table_exists", return_value=False),
		):
			announce_objectives.execute()

		создать.assert_called_once_with("LMS Course", announce_objectives.ПОЛЕ)
		self.assertFalse(self.цели(self.анонс))
