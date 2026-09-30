# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import homework as домашка
from lms_frappe_app.agent_learning import permissions
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	создать_домашку,
	создать_занятие,
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_урок,
	создать_ученика,
)


class IntegrationTestHomeworkReview(IntegrationTestCase):
	"""Переходы куратора: принять, вернуть, отменить приём (learning-services#452)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		с = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"hwr-{с}@example.com")
		self.урок = создать_урок(f"Урок {с}")
		зачислить(self.ученик, self.урок)
		создать_домашку(self.урок)
		self.организация = создать_организацию(f"Орг {с}")
		добавить_в_организацию(self.ученик, self.организация)
		self.куратор = создать_менеджера(f"hwrm-{с}@example.com", self.организация)
		self.сдача = домашка.сохранить(self.ученик, self.урок, self.организация, answer="первая")

	def проверить(self, действие, comment=None, version=None):
		return домашка.проверить(
			self.куратор,
			self.сдача.name,
			действие,
			self.сдача.version if version is None else version,
			comment,
		)

	def отказ(self, действие, comment=None, version=None) -> Отказ:
		with self.assertRaises(Отказ) as отказ:
			self.проверить(действие, comment, version)
		return отказ.exception

	def test_принять(self):
		документ = self.проверить("accept")
		self.assertEqual(документ.status, "Accepted")
		последняя = документ.history[-1]
		self.assertEqual(
			(последняя.event, последняя.by_user, последняя.version, последняя.comment),
			("accepted", self.куратор, 1, None),
		)

	def test_вернуть_пишет_комментарий_и_версию(self):
		документ = self.проверить("send_back", "  Добавьте итог встречи  ")
		self.assertEqual(документ.status, "Returned")
		последняя = документ.history[-1]
		self.assertEqual(
			(последняя.event, последняя.by_user, последняя.version, последняя.comment),
			("returned", self.куратор, 1, "Добавьте итог встречи"),
		)

	def test_отменить_приём(self):
		self.проверить("accept")
		документ = self.проверить("reopen", "Принял по ошибке")
		self.assertEqual(документ.status, "Returned")
		self.assertEqual(
			(документ.history[-1].event, документ.history[-1].comment), ("reopened", "Принял по ошибке")
		)

	def test_после_возврата_ученик_сохраняет_новую_версию(self):
		self.проверить("send_back", "Доделайте")
		документ = домашка.сохранить(self.ученик, self.урок, self.организация, answer="вторая")
		self.assertEqual((документ.status, документ.version), ("Submitted", 2))

	def test_после_отмены_приёма_ученик_снова_правит(self):
		self.проверить("accept")
		self.проверить("reopen", "Не то")
		документ = домашка.сохранить(self.ученик, self.урок, self.организация, answer="исправил")
		self.assertEqual((документ.status, документ.version), ("Submitted", 2))

	def test_устаревшая_версия(self):
		домашка.сохранить(self.ученик, self.урок, self.организация, answer="вторая")
		отказ = self.отказ("accept", version=1)
		self.assertEqual((отказ.код, отказ.подробности["version"]), ("stale_version", 2))
		self.assertEqual(frappe.db.get_value(домашка.СДАЧА, self.сдача.name, "status"), "Submitted")

	def test_не_тот_статус(self):
		self.проверить("accept")
		отказ = self.отказ("send_back", "Доделайте")
		self.assertEqual((отказ.код, отказ.подробности["status"]), ("wrong_status", "Accepted"))
		self.проверить("reopen", "Не то")
		домашка.сохранить(self.ученик, self.урок, self.организация, answer="исправил")
		self.сдача.reload()
		отказ = self.отказ("reopen", "Не то")
		self.assertEqual((отказ.код, отказ.подробности["status"]), ("wrong_status", "Submitted"))

	def test_принять_выданную_нельзя(self):
		урок = создать_урок(f"Урок {frappe.generate_hash(length=6)}")
		зачислить(self.ученик, урок)
		создать_домашку(урок)
		домашка.выдать(frappe.get_doc("Agent Learning Session", создать_занятие(self.ученик, урок)))
		имя = frappe.db.get_value(домашка.СДАЧА, {"member": self.ученик, "lesson": урок}, "name")
		with self.assertRaises(Отказ) as отказ:
			домашка.проверить(self.куратор, имя, "accept", 0)
		self.assertEqual(
			(отказ.exception.код, отказ.exception.подробности["status"]), ("wrong_status", "Assigned")
		)

	def test_архивную_не_проверяют(self):
		frappe.db.set_value(домашка.СДАЧА, self.сдача.name, {"member": None, "archived_student": self.ученик})
		self.assertEqual(self.отказ("accept").код, "wrong_status")

	def test_комментарий_обязателен(self):
		for действие in ("send_back", "reopen"):
			for комментарий in (None, "", "   "):
				self.assertEqual(
					self.отказ(действие, комментарий).код, "comment_required", (действие, комментарий)
				)
		self.assertEqual(len(frappe.get_doc(домашка.СДАЧА, self.сдача.name).history), 1)

	def test_проверенная_версия_и_комментарий(self):
		self.assertIsNone(домашка.проверенная_версия(self.сдача))
		self.проверить("send_back", "Доделайте")
		домашка.сохранить(self.ученик, self.урок, self.организация, answer="вторая")
		self.сдача.reload()
		self.assertEqual(домашка.проверенная_версия(self.сдача), 1)
		self.проверить("accept")
		self.проверить("reopen", "Не то")
		self.сдача.reload()
		self.assertEqual(домашка.проверенная_версия(self.сдача), 2)
		self.assertEqual(домашка.последний_комментарий(self.сдача), "Не то")
		self.assertEqual(домашка.проверенные_версии([self.сдача.name, "нет-такой"]), {self.сдача.name: 2})
		self.assertEqual(домашка.последние_комментарии([self.сдача.name]), {self.сдача.name: "Не то"})

	def test_журнал_для_куратора_называет_ученика(self):
		frappe.db.set_value("User", self.ученик, "first_name", "Анна")
		frappe.db.set_value("User", self.ученик, "full_name", "Анна")
		frappe.clear_document_cache("User", self.ученик)
		self.проверить("send_back", "Доделайте")
		документ = frappe.get_doc(домашка.СДАЧА, self.сдача.name)
		другой = создать_куратора(f"hwrc-{frappe.generate_hash(length=6)}@example.com")
		журнал = домашка.описание_сдачи(документ, читатель=другой)["history"]
		self.assertEqual((журнал[0]["by"], журнал[0]["by_name"]), (None, "Анна"))
		self.assertIsNone(журнал[1]["by"])
		свой = домашка.описание_сдачи(документ, читатель=self.куратор)["history"]
		self.assertEqual(свой[1]["by"], self.куратор)


class IntegrationTestMayReview(IntegrationTestCase):
	"""Кто проверяет сдачу (learning-services#452)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		с = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"hwv-{с}@example.com")
		урок = создать_урок(f"Урок {с}")
		зачислить(self.ученик, урок)
		создать_домашку(урок)
		self.организация = создать_организацию(f"Орг {с}")
		добавить_в_организацию(self.ученик, self.организация)
		чужая = создать_организацию(f"Чужая {с}")
		self.менеджер = создать_менеджера(f"hwvm-{с}@example.com", self.организация)
		self.чужой_менеджер = создать_менеджера(f"hwvm2-{с}@example.com", чужая)
		self.методист = создать_куратора(f"hwvc-{с}@example.com", роль="Course Creator")
		self.модератор = создать_куратора(f"hwvmod-{с}@example.com", роль="Moderator")
		self.сосед = создать_ученика(f"hwvn-{с}@example.com")
		self.рабочая = домашка.сохранить(self.ученик, урок, self.организация, answer="в компании")
		self.личная = домашка.сохранить(self.ученик, урок, None, answer="лично")
		# Своя сдача куратора: у методиста тоже бывает учёба.
		зачислить(self.методист, урок)
		self.своя = домашка.сохранить(self.методист, урок, None, answer="моя")

	def test_матрица(self):
		for user, рабочая, личная in (
			(self.ученик, False, False),
			(self.сосед, False, False),
			(self.менеджер, True, False),
			(self.чужой_менеджер, False, False),
			(self.методист, True, True),
			(self.модератор, True, True),
			("Administrator", True, True),
		):
			self.assertEqual(permissions.может_проверять(self.рабочая, user), рабочая, user)
			self.assertEqual(permissions.может_проверять(self.личная, user), личная, user)

	def test_свою_не_проверяет(self):
		self.assertFalse(permissions.может_проверять(self.своя, self.методист))
		self.assertTrue(permissions.может_проверять(self.своя, self.модератор))

	def test_архивную_не_проверяет_никто(self):
		self.рабочая.member = None
		self.рабочая.archived_student = self.ученик
		for user in (self.менеджер, self.методист, "Administrator"):
			self.assertFalse(permissions.может_проверять(self.рабочая, user), user)
