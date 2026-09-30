# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import homework as домашка
from lms_frappe_app.api import review
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	создать_домашку,
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_урок,
	создать_ученика,
)


class IntegrationTestReviewQueue(IntegrationTestCase):
	"""Очередь «Ждут проверки» и карточка куратора (learning-services#452)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		с = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"rq-{с}@example.com")
		frappe.db.set_value("User", self.ученик, {"first_name": "Анна", "full_name": "Анна Петрова"})
		frappe.clear_document_cache("User", self.ученик)
		self.урок = создать_урок(f"Урок {с}")
		self.курс = зачислить(self.ученик, self.урок)
		создать_домашку(self.урок)
		self.организация = создать_организацию(f"Орг {с}")
		добавить_в_организацию(self.ученик, self.организация)
		self.чужая_организация = создать_организацию(f"Чужая {с}")
		добавить_в_организацию(self.ученик, self.чужая_организация)
		self.менеджер = создать_менеджера(f"rqm-{с}@example.com", self.организация)
		self.методист = создать_куратора(f"rqc-{с}@example.com", роль="Course Creator")
		self.рабочая = домашка.сохранить(self.ученик, self.урок, self.организация, answer="в компании")
		self.личная = домашка.сохранить(self.ученик, self.урок, None, answer="лично")
		self.чужая = домашка.сохранить(self.ученик, self.урок, self.чужая_организация, answer="в другой")
		# Старые сверху: время сдачи задаётся явно, иначе порядок решала бы
		# секунда вставки.
		for номер, сдача in enumerate((self.личная, self.рабочая, self.чужая)):
			frappe.db.set_value(домашка.СДАЧА, сдача.name, "submitted_at", f"2026-10-0{номер + 1} 10:00:00")

	def очередь(self, user, **параметры) -> dict:
		frappe.set_user(user)
		ответ = review.queue(**параметры)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def вызвать(self, user, метод, **параметры) -> dict:
		frappe.set_user(user)
		return getattr(review, метод)(**параметры)

	def код(self, ответ) -> str:
		self.assertFalse(ответ["ok"], ответ)
		return ответ["error"]["code"]

	# --- очередь ---

	def test_руководитель_видит_только_свою_организацию(self):
		данные = self.очередь(self.менеджер)
		self.assertEqual([с["id"] for с in данные["items"]], [self.рабочая.name])
		self.assertEqual(данные["total"], 1)
		[строка] = данные["items"]
		self.assertEqual(строка["student"], {"name": "Анна Петрова"})
		self.assertEqual(
			(строка["course"], строка["lesson"], строка["organization"], строка["organization_title"]),
			(self.курс, self.урок, self.организация, self.организация),
		)
		self.assertEqual((строка["version"], строка["reviewed_version"], строка["overdue"]), (1, None, False))
		self.assertEqual(данные["organizations"], [{"id": self.организация, "title": self.организация}])
		self.assertEqual([к["id"] for к in данные["courses"]], [self.курс])

	def test_руководитель_не_видит_личное(self):
		self.assertEqual(self.очередь(self.менеджер, organization="personal")["items"], [])
		self.assertEqual(self.очередь(self.менеджер, organization=self.чужая_организация)["items"], [])

	def test_методист_видит_всё_старые_сверху(self):
		данные = self.очередь(self.методист, course=self.курс)
		self.assertEqual(
			[с["id"] for с in данные["items"]], [self.личная.name, self.рабочая.name, self.чужая.name]
		)
		личная = данные["items"][0]
		self.assertEqual((личная["organization"], личная["organization_title"]), ("personal", "Личное"))
		self.assertIn({"id": "personal", "title": "Личное"}, данные["organizations"])

	def test_фильтр_личного_и_организации(self):
		self.assertEqual(
			[
				с["id"]
				for с in self.очередь(self.методист, course=self.курс, organization="personal")["items"]
			],
			[self.личная.name],
		)
		self.assertEqual(
			[
				с["id"]
				for с in self.очередь(self.методист, course=self.курс, organization=self.организация)["items"]
			],
			[self.рабочая.name],
		)

	def test_фильтр_курса(self):
		другой = создать_урок(f"Урок {frappe.generate_hash(length=6)}")
		зачислить(self.ученик, другой)
		создать_домашку(другой)
		домашка.сохранить(self.ученик, другой, self.организация, answer="другой курс")
		self.assertEqual(self.очередь(self.менеджер)["total"], 2)
		self.assertEqual(
			[с["id"] for с in self.очередь(self.менеджер, course=self.курс)["items"]], [self.рабочая.name]
		)

	def test_фильтр_статуса(self):
		домашка.проверить(self.методист, self.рабочая.name, "send_back", 1, "Доделайте")
		self.assertEqual(self.очередь(self.менеджер)["items"], [])
		[строка] = self.очередь(self.менеджер, status="Returned")["items"]
		self.assertEqual((строка["status"], строка["reviewed_version"]), ("Returned", 1))

	def test_свою_сдачу_куратор_не_видит(self):
		зачислить(self.методист, self.урок)
		своя = домашка.сохранить(self.методист, self.урок, None, answer="моя")
		self.assertNotIn(своя.name, [с["id"] for с in self.очередь(self.методист, course=self.курс)["items"]])

	def test_архивные_не_видны(self):
		frappe.db.set_value(
			домашка.СДАЧА, self.рабочая.name, {"member": None, "archived_student": self.ученик}
		)
		данные = self.очередь(self.менеджер)
		self.assertEqual((данные["items"], данные["total"]), ([], 0))

	def test_limit_и_total(self):
		данные = self.очередь(self.методист, course=self.курс, limit=2)
		self.assertEqual((len(данные["items"]), данные["total"]), (2, 3))
		self.assertEqual(len(self.очередь(self.методист, course=self.курс, limit=500)["items"]), 3)

	def test_ученику_очередь_пуста(self):
		данные = self.очередь(self.ученик)
		self.assertEqual((данные["items"], данные["total"]), ([], 0))
		self.assertEqual(self.вызвать(self.ученик, "pending_count")["data"], {"count": 0})

	def test_счётчик_ожидающих(self):
		self.assertEqual(self.вызвать(self.менеджер, "pending_count")["data"], {"count": 1})
		домашка.проверить(self.методист, self.рабочая.name, "accept", 1)
		self.assertEqual(self.вызвать(self.менеджер, "pending_count")["data"], {"count": 0})

	# --- карточка ---

	def test_карточка_и_действия_по_статусу(self):
		карточка = self.вызвать(self.менеджер, "submission", submission=self.рабочая.name)["data"]
		self.assertEqual(карточка["actions"], ["accept", "send_back"])
		self.assertEqual(карточка["submission"]["id"], self.рабочая.name)
		self.assertIn("versions", карточка["submission"])
		self.assertEqual(карточка["homework"]["title"], "Встреча со спонсором")
		self.assertEqual(
			(карточка["student"], карточка["reviewed_version"]), ({"name": "Анна Петрова"}, None)
		)
		self.assertTrue(карточка["lesson_url"].startswith(f"/lms/courses/{self.курс}/learn/"))
		[событие] = карточка["submission"]["history"]
		self.assertEqual((событие["by"], событие["by_name"]), (None, "Анна Петрова"))

		принята = self.вызвать(self.менеджер, "accept", submission=self.рабочая.name, version=1)["data"]
		self.assertEqual((принята["submission"]["status"], принята["actions"]), ("Accepted", ["reopen"]))
		self.assertEqual(принята["reviewed_version"], 1)
		self.assertEqual(принята["submission"]["history"][-1]["by"], self.менеджер)
		возвращена = self.вызвать(
			self.менеджер, "reopen", submission=self.рабочая.name, version=1, comment="Не то"
		)["data"]
		self.assertEqual((возвращена["submission"]["status"], возвращена["actions"]), ("Returned", []))

	def test_вернуть_и_пересдать(self):
		ответ = self.вызвать(
			self.менеджер, "send_back", submission=self.рабочая.name, version="1", comment="Доделайте"
		)
		self.assertEqual(ответ["data"]["submission"]["status"], "Returned")
		домашка.сохранить(self.ученик, self.урок, self.организация, answer="доделал")
		карточка = self.вызвать(self.менеджер, "submission", submission=self.рабочая.name)["data"]
		self.assertEqual((карточка["submission"]["version"], карточка["reviewed_version"]), (2, 1))

	def test_своя_сдача_карточка_без_действий(self):
		карточка = self.вызвать(self.ученик, "submission", submission=self.рабочая.name)["data"]
		self.assertEqual(карточка["actions"], [])
		self.assertEqual(
			self.код(self.вызвать(self.ученик, "accept", submission=self.рабочая.name, version=1)),
			"not_allowed",
		)

	def test_чужую_карточку_не_открыть(self):
		self.assertEqual(
			self.код(self.вызвать(self.менеджер, "submission", submission=self.личная.name)), "not_allowed"
		)
		self.assertEqual(
			self.код(
				self.вызвать(self.менеджер, "send_back", submission=self.личная.name, version=1, comment="x")
			),
			"not_allowed",
		)

	def test_нет_сдачи(self):
		self.assertEqual(
			self.код(self.вызвать(self.менеджер, "submission", submission="нет-такой")),
			"submission_not_found",
		)
		self.assertEqual(
			self.код(self.вызвать(self.менеджер, "accept", submission="нет-такой", version=1)),
			"submission_not_found",
		)

	def test_отказы_переходов(self):
		домашка.сохранить(self.ученик, self.урок, self.организация, answer="вторая")
		ответ = self.вызвать(self.менеджер, "accept", submission=self.рабочая.name, version=1)
		self.assertEqual((self.код(ответ), ответ["error"]["version"]), ("stale_version", 2))
		self.assertEqual(
			self.код(
				self.вызвать(self.менеджер, "send_back", submission=self.рабочая.name, version=2, comment=" ")
			),
			"comment_required",
		)
		ответ = self.вызвать(
			self.менеджер, "reopen", submission=self.рабочая.name, version=2, comment="Не то"
		)
		self.assertEqual((self.код(ответ), ответ["error"]["status"]), ("wrong_status", "Submitted"))

	def test_гонка_даёт_busy(self):
		with (
			patch.object(домашка, "проверить", side_effect=frappe.QueryDeadlockError("1020")),
			patch.object(frappe.db, "rollback"),
		):
			ответ = self.вызвать(self.менеджер, "accept", submission=self.рабочая.name, version=1)
		self.assertEqual(self.код(ответ), "busy")

	def test_гонка_с_новой_версией_даёт_stale_version(self):
		"""Ученик сохранил, пока куратор ждал блокировку: снимок куратора старше."""

		def сохранил_и_заблокировал(*args, **kwargs):
			frappe.db.set_value(домашка.СДАЧА, self.рабочая.name, "version", 2)
			raise frappe.QueryDeadlockError("1020")

		with (
			patch.object(домашка, "проверить", side_effect=сохранил_и_заблокировал),
			patch.object(frappe.db, "rollback"),
		):
			ответ = self.вызвать(self.менеджер, "accept", submission=self.рабочая.name, version=1)
		self.assertEqual((self.код(ответ), ответ["error"]["version"]), ("stale_version", 2))
