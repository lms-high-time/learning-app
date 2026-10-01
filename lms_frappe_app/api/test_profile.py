# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Профиль ученика — вид на факты агента (learning-services#463).

Профиль не хранит своего: он раскладывает факты `Agent Student Note` по
блокам. Здесь проверяется раскладка, заполненность и то, кому чужой профиль
виден — руководителю и автору курса нет, как и заметки агента.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.profile import КЛЮЧИ_ПРОФИЛЯ, ПОРОГ_ЗАПОЛНЕННОСТИ, ПРОФИЛЬ
from lms_frappe_app.api import student
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	политика_по_умолчанию,
	создать_куратора,
	создать_курс,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
)

ПОЛЯ_СВОДКИ = {"filled", "total", "complete", "interview_url"}


class IntegrationTestProfile(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		политика_по_умолчанию()
		self.addCleanup(политика_по_умолчанию)
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"profile-{суффикс}@example.com")
		self.другой = создать_ученика(f"profile-other-{суффикс}@example.com")
		организация = создать_организацию(f"Профиль {суффикс}")
		добавить_в_организацию(self.ученик, организация)
		self.руководитель = создать_менеджера(f"profile-boss-{суффикс}@example.com", организация)
		self.модератор = создать_куратора(f"profile-mod-{суффикс}@example.com", роль="Moderator")
		self.админ = создать_куратора(f"profile-admin-{суффикс}@example.com", роль="System Manager")
		frappe.set_user(self.ученик)

	def факт(self, ключ: str, текст: str = "да") -> None:
		self.assertTrue(student.remember(kind="fact", key=ключ, text=текст)["ok"])

	def факты_по_ключам(self, данные: dict) -> dict:
		return {факт["key"]: факт for блок in данные["blocks"] for факт in блок["facts"]}

	# --- раскладка ---

	def test_пустой_профиль(self):
		данные = student.my_profile()["data"]

		self.assertEqual([блок["id"] for блок in данные["blocks"]], [блок for блок, *_ in ПРОФИЛЬ])
		факты = self.факты_по_ключам(данные)
		self.assertEqual(set(факты), КЛЮЧИ_ПРОФИЛЯ)
		self.assertTrue(all(ф["text"] is None and ф["updated"] is None for ф in факты.values()))
		self.assertEqual(данные["other_facts"], [])
		self.assertEqual(
			(данные["filled"], данные["total"], данные["complete"]),
			(0, len(КЛЮЧИ_ПРОФИЛЯ), False),
		)
		self.assertEqual(данные["user"], self.ученик)
		self.assertIn("full_name", данные)
		self.assertIn("user_image", данные)

	def test_факт_профиля_в_блоке_прочий_отдельно_курсовой_мимо(self):
		self.факт("role", "Руководитель отдела продаж")
		self.факт("language", "Python")
		frappe.set_user("Administrator")
		курс = создать_курс(f"Курс профиля {frappe.generate_hash(length=6)}")
		frappe.get_doc(
			{
				"doctype": "Agent Student Note",
				"student": self.ученик,
				"course": курс,
				"kind": "Observation",
				"note_key": "pace",
				"text": "Торопится",
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)

		данные = student.my_profile()["data"]

		работа = next(блок for блок in данные["blocks"] if блок["id"] == "work")
		роль = next(факт for факт in работа["facts"] if факт["key"] == "role")
		self.assertEqual(роль["text"], "Руководитель отдела продаж")
		self.assertTrue(роль["updated"])
		self.assertTrue(роль["label"])
		self.assertEqual([факт["key"] for факт in данные["other_facts"]], ["language"])
		self.assertNotIn("label", данные["other_facts"][0])
		self.assertEqual(данные["filled"], 1)
		self.assertNotIn("Торопится", frappe.as_json(данные))

	def test_подписи_переводятся(self):
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.local.lang = "ru"

		данные = student.my_profile()["data"]

		self.assertEqual(данные["blocks"][0]["title"], "Контекст работы")
		self.assertEqual(данные["blocks"][0]["facts"][0]["label"], "Роль")

	# --- сводка ---

	def test_сводка_только_заполненность(self):
		self.факт("role")

		for summary in (1, "1", True):
			данные = student.my_profile(summary=summary)["data"]
			self.assertEqual(set(данные), ПОЛЯ_СВОДКИ, summary)
			self.assertEqual((данные["filled"], данные["total"]), (1, len(КЛЮЧИ_ПРОФИЛЯ)))

	def test_профиль_заполнен_с_порога(self):
		ключи = [ключ for _блок, _имя, поля in ПРОФИЛЬ for ключ, _подпись in поля]
		for ключ in ключи[: ПОРОГ_ЗАПОЛНЕННОСТИ - 1]:
			self.факт(ключ)
		self.assertFalse(student.my_profile(summary=1)["data"]["complete"])

		self.факт(ключи[ПОРОГ_ЗАПОЛНЕННОСТИ - 1])

		self.assertTrue(student.my_profile(summary=1)["data"]["complete"])
		self.assertTrue(student.my_profile()["data"]["complete"])

	def test_прочие_факты_заполненность_не_двигают(self):
		self.факт("language")

		self.assertEqual(student.my_profile(summary=1)["data"]["filled"], 0)

	# --- адрес интервью ---

	def test_адрес_интервью_у_своего_профиля(self):
		for данные in (student.my_profile()["data"], student.my_profile(summary=1)["data"]):
			self.assertTrue(данные["interview_url"].endswith("/chat?mode=profile"), данные)

	def test_без_сервиса_агента_адреса_интервью_нет(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Agent Learning Settings", "agent_service_url", "")
		frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")
		frappe.set_user(self.ученик)

		self.assertIsNone(student.my_profile()["data"]["interview_url"])
		self.assertIsNone(student.my_profile(summary=1)["data"]["interview_url"])

	# --- чужой профиль ---

	def test_чужой_профиль_закрыт(self):
		"""Другому ученику, руководителю организации ученика и автору курсов —
		отказ: заметки агента им не видны, и профиль эту границу не сдвигает."""
		self.факт("role", "Руководитель отдела продаж")
		for кто in (self.другой, self.руководитель, self.модератор):
			frappe.set_user(кто)
			for summary in (None, 1):
				ответ = student.my_profile(user=self.ученик, summary=summary)
				self.assertFalse(ответ["ok"], кто)
				self.assertEqual(ответ["error"]["code"], student.ЧУЖОЙ_ПРОФИЛЬ, кто)
				self.assertNotIn("Руководитель отдела продаж", frappe.as_json(ответ))

	def test_несуществующий_пользователь_неотличим_от_чужого(self):
		"""Иначе отказ подтверждал бы, есть ли такой логин на платформе."""
		ответ = student.my_profile(user="nobody-here@example.com")

		self.assertEqual(ответ["error"]["code"], student.ЧУЖОЙ_ПРОФИЛЬ)

	def test_платформа_видит_чужой_профиль_без_адреса_интервью(self):
		self.факт("role", "Руководитель отдела продаж")
		frappe.set_user(self.админ)

		данные = student.my_profile(user=self.ученик)["data"]

		self.assertEqual(данные["user"], self.ученик)
		self.assertEqual(self.факты_по_ключам(данные)["role"]["text"], "Руководитель отдела продаж")
		self.assertIsNone(данные["interview_url"])
		self.assertIsNone(student.my_profile(user=self.ученик, summary=1)["data"]["interview_url"])

	def test_платформе_несуществующий_пользователь_отвечает_прямо(self):
		frappe.set_user(self.админ)

		ответ = student.my_profile(user="nobody-here@example.com")

		self.assertEqual(ответ["error"]["code"], student.ПОЛЬЗОВАТЕЛЬ_НЕ_НАЙДЕН)
