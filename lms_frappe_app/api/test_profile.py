# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Профиль ученика — вид на факты агента (learning-services#463).

Профиль не хранит своего: он раскладывает факты `Agent Student Note` по
блокам. Здесь проверяется раскладка, заполненность и то, кому чужой профиль
виден — руководителю и автору курса нет, как и заметки агента.
"""

import frappe
from frappe import _
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
		self.суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"profile-{self.суффикс}@example.com")
		frappe.set_user(self.ученик)

	def факт(self, ключ: str, текст: str = "да") -> None:
		self.assertTrue(student.remember(kind="fact", key=ключ, text=текст)["ok"])

	def факты_по_ключам(self, данные: dict) -> dict:
		return {факт["key"]: факт for блок in данные["blocks"] for факт in блок["facts"]}

	# --- раскладка ---

	def test_раскладка_по_блокам(self):
		"""Пустой профиль — все ключи без текста; факт профиля — в своём блоке,
		прочий — отдельно, курсовой — мимо профиля."""
		with self.subTest("пустой профиль"):
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
		with self.subTest("факт профиля в блоке, прочий отдельно, курсовой мимо"):
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

	def test_подписи_переводятся_только_в_профиле(self):
		"""`ru.csv` действует на всю платформу: без контекста «Experience»
		переименовал бы любую строку upstream с тем же текстом."""
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.local.lang = "ru"

		with self.subTest("подписи профиля"):
			данные = student.my_profile()["data"]

			self.assertEqual(данные["blocks"][0]["title"], "Контекст работы")
			self.assertEqual(данные["blocks"][0]["facts"][0]["label"], "Роль")
			уровень = next(блок for блок in данные["blocks"] if блок["id"] == "level")
			self.assertEqual(уровень["facts"][0]["label"], "Опыт в теме")

		with self.subTest("та же строка вне профиля"):
			self.assertNotEqual(_("Experience", lang="ru"), "Опыт в теме")
			self.assertNotEqual(_("Learning style", lang="ru"), "Подача")

	# --- сводка ---

	def test_сводка_и_заполненность(self):
		"""Прочие факты заполненность не двигают; сводка — только заполненность;
		профиль заполнен с порога."""
		self.факт("language")
		with self.subTest("прочие факты заполненность не двигают"):
			self.assertEqual(student.my_profile(summary=1)["data"]["filled"], 0)

		self.факт("role")
		for summary in (1, "1", True):
			with self.subTest("сводка только заполненность", summary=summary):
				данные = student.my_profile(summary=summary)["data"]
				self.assertEqual(set(данные), ПОЛЯ_СВОДКИ, summary)
				self.assertEqual((данные["filled"], данные["total"]), (1, len(КЛЮЧИ_ПРОФИЛЯ)))

		ключи = [ключ for _блок, _имя, поля in ПРОФИЛЬ for ключ, _подпись in поля]
		for ключ in ключи[: ПОРОГ_ЗАПОЛНЕННОСТИ - 1]:
			self.факт(ключ)
		with self.subTest("профиль заполнен с порога"):
			self.assertFalse(student.my_profile(summary=1)["data"]["complete"])

			self.факт(ключи[ПОРОГ_ЗАПОЛНЕННОСТИ - 1])

			self.assertTrue(student.my_profile(summary=1)["data"]["complete"])
			self.assertTrue(student.my_profile()["data"]["complete"])

	# --- адрес интервью ---

	def test_адрес_интервью_у_своего_профиля(self):
		"""Без сервиса агента адреса интервью нет."""
		with self.subTest("сервис агента задан"):
			for данные in (student.my_profile()["data"], student.my_profile(summary=1)["data"]):
				self.assertTrue(данные["interview_url"].endswith("/chat?mode=profile"), данные)

		frappe.set_user("Administrator")
		frappe.db.set_single_value("Agent Learning Settings", "agent_service_url", "")
		frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")
		frappe.set_user(self.ученик)
		with self.subTest("без сервиса агента"):
			self.assertIsNone(student.my_profile()["data"]["interview_url"])
			self.assertIsNone(student.my_profile(summary=1)["data"]["interview_url"])

	# --- чужой профиль ---

	def test_чужой_профиль(self):
		"""Другому ученику, руководителю организации ученика и автору курсов —
		отказ: заметки агента им не видны, и профиль эту границу не сдвигает.
		Несуществующий пользователь неотличим от чужого — иначе отказ
		подтверждал бы, есть ли такой логин на платформе. Платформа видит чужой
		профиль без адреса интервью, а о несуществующем пользователе отвечает
		прямо."""
		self.факт("role", "Руководитель отдела продаж")
		frappe.set_user("Administrator")
		другой = создать_ученика(f"profile-other-{self.суффикс}@example.com")
		организация = создать_организацию(f"Профиль {self.суффикс}")
		добавить_в_организацию(self.ученик, организация)
		руководитель = создать_менеджера(f"profile-boss-{self.суффикс}@example.com", организация)
		модератор = создать_куратора(f"profile-mod-{self.суффикс}@example.com", роль="Moderator")
		админ = создать_куратора(f"profile-admin-{self.суффикс}@example.com", роль="System Manager")

		for кто in (другой, руководитель, модератор):
			frappe.set_user(кто)
			for summary in (None, 1):
				with self.subTest("чужой профиль закрыт", кто=кто, summary=summary):
					ответ = student.my_profile(user=self.ученик, summary=summary)
					self.assertFalse(ответ["ok"], кто)
					self.assertEqual(ответ["error"]["code"], student.ЧУЖОЙ_ПРОФИЛЬ, кто)
					self.assertNotIn("Руководитель отдела продаж", frappe.as_json(ответ))

		with self.subTest("несуществующий пользователь неотличим от чужого"):
			frappe.set_user(self.ученик)
			ответ = student.my_profile(user="nobody-here@example.com")

			self.assertEqual(ответ["error"]["code"], student.ЧУЖОЙ_ПРОФИЛЬ)

		frappe.set_user(админ)
		with self.subTest("платформа видит чужой профиль без адреса интервью"):
			данные = student.my_profile(user=self.ученик)["data"]

			self.assertEqual(данные["user"], self.ученик)
			self.assertEqual(self.факты_по_ключам(данные)["role"]["text"], "Руководитель отдела продаж")
			self.assertIsNone(данные["interview_url"])
			self.assertIsNone(student.my_profile(user=self.ученик, summary=1)["data"]["interview_url"])

		with self.subTest("платформе несуществующий пользователь отвечает прямо"):
			ответ = student.my_profile(user="nobody-here@example.com")

			self.assertEqual(ответ["error"]["code"], student.ПОЛЬЗОВАТЕЛЬ_НЕ_НАЙДЕН)
