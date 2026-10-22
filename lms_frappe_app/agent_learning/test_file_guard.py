# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Записи `File`, которые меняет только System Manager (learning-services#683)."""

import io
import zipfile

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import homework as домашка
from lms_frappe_app.agent_learning import uploads
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	создать_домашку,
	создать_менеджера,
	создать_организацию,
	создать_урок,
	создать_ученика,
)


def _архив() -> bytes:
	буфер = io.BytesIO()
	with zipfile.ZipFile(буфер, "w") as архив:
		архив.writestr("внутри.txt", frappe.generate_hash())
	return буфер.getvalue()


class IntegrationTestFileGuard(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		с = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"fg-{с}@example.com")
		self.сосед = создать_ученика(f"fgn-{с}@example.com")
		self.урок = создать_урок(f"Урок {с}")
		зачислить(self.ученик, self.урок)
		создать_домашку(self.урок)
		организация = создать_организацию(f"Орг {с}")
		добавить_в_организацию(self.ученик, организация)
		куратор = создать_менеджера(f"fgm-{с}@example.com", организация)
		# Сохраняет сам ученик: владелец `File` — тот, чья сессия.
		frappe.set_user(self.ученик)
		self.сдача = домашка.сохранить(
			self.ученик, self.урок, организация, новые=[("отчёт.txt", frappe.generate_hash().encode())]
		)
		frappe.set_user("Administrator")
		домашка.проверить(куратор, self.сдача.name, "accept", self.сдача.version)
		self.файл = self.сдача.files[0].file
		self.имя = frappe.db.get_value("File", self.файл, "file_name")

	def test_файл_сдачи_правит_и_удаляет_только_system_manager(self):
		def удалить():
			frappe.delete_doc("File", self.файл)

		def переименовать():
			файл = frappe.get_doc("File", self.файл)
			файл.file_name = "другое.txt"
			файл.save()

		def отвязать():
			файл = frappe.get_doc("File", self.файл)
			файл.attached_to_doctype = None
			файл.attached_to_name = None
			файл.save()

		def вставить_мимо_сохранения():
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": "ещё.txt",
					"content": frappe.generate_hash().encode(),
					"is_private": 1,
					"attached_to_doctype": домашка.СДАЧА,
					"attached_to_name": self.сдача.name,
				}
			).insert()

		действия = {
			"удаление": удалить,
			"переименование": переименовать,
			"отвязка": отвязать,
			"вставка мимо сохранения сдачи": вставить_мимо_сохранения,
		}
		for что, действие in действия.items():
			with self.subTest(что):
				frappe.set_user(self.ученик)
				try:
					with self.assertRaises(frappe.PermissionError):
						действие()
				finally:
					frappe.set_user("Administrator")
				файл = frappe.get_doc("File", self.файл)
				self.assertEqual((файл.file_name, файл.attached_to_name), (self.имя, self.сдача.name))
				self.assertTrue(файл.exists_on_disk())
				self.assertEqual(frappe.db.count("File", {"attached_to_name": self.сдача.name}), 1)

	def test_архив_сдачи_не_распаковывается(self):
		from frappe.core.api.file import unzip_file

		frappe.set_user(self.ученик)
		try:
			архив = uploads.новый_файл(self.сдача, "архив.zip", _архив()).insert(ignore_permissions=True)
			with self.assertRaises(frappe.PermissionError):
				unzip_file(архив.name)
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("File", архив.name))
		self.assertEqual(frappe.db.count("File", {"attached_to_name": self.сдача.name}), 2)

	def test_адрес_файла_не_меняется_после_вставки(self):
		адрес = frappe.db.get_value("File", self.файл, "file_url")
		frappe.set_user(self.сосед)
		try:
			свой = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": "свой.txt",
					"content": frappe.generate_hash().encode(),
					"is_private": 1,
				}
			).insert()
			свой.file_url = адрес
			with self.assertRaises(frappe.PermissionError):
				свой.save()
		finally:
			frappe.set_user("Administrator")
		self.assertNotEqual(frappe.db.get_value("File", свой.name, "file_url"), адрес)

	def test_system_manager_правит_файл_сдачи(self):
		файл = frappe.get_doc("File", self.файл)
		файл.file_name = "переименован.txt"
		файл.save()
		self.assertEqual(frappe.db.get_value("File", self.файл, "file_name"), "переименован.txt")
