# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Выгрузка и удаление данных ученика и реестр данных пользователя
(lms-high-time/learning-services#689).

Ворота — `UnitTestPersonalDataRegistry`: новое поле со ссылкой на
пользователя или на удаляемую запись без строки в реестре валит прогон.
Процедура проверяется на записях каждого доктайпа реестра: они заводятся
прямой вставкой по реестру, а не через методы — так тест покрывает и
доктайп, добавленный в реестр завтра.
"""

import json
import os
from collections import defaultdict

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app import personal_data
from lms_frappe_app.agent_learning.consent import РЕГИСТРАЦИЯ, УДАЛЁН
from lms_frappe_app.personal_data import (
	ВНЕ_РЕЕСТРА,
	ЗАВИСИМЫЕ,
	ОБЕЗЛИЧИТЬ,
	ОТВЯЗАТЬ,
	УДАЛИТЬ,
	ХРАНИТЬ,
	реестр,
)
from lms_frappe_app.tests.sample_data import создать_пользователя, создать_ученика

РЕЖИМЫ = frozenset({УДАЛИТЬ, ОБЕЗЛИЧИТЬ, ХРАНИТЬ})


def поля_реестра() -> set[tuple[str, str]]:
	return {(запись["doctype"], запись["filter_by"]) for запись in реестр()}


def удаляемые_доктайпы() -> set[str]:
	доктайпы = {
		запись["doctype"]
		for запись in реестр()
		if запись["mode"] == УДАЛИТЬ and not frappe.get_meta(запись["doctype"]).istable
	}
	return доктайпы | {доктайп for (доктайп, _), действие in ЗАВИСИМЫЕ.items() if действие == УДАЛИТЬ}


class UnitTestPersonalDataRegistry(UnitTestCase):
	def test_каждое_поле_пользователя_в_доктайпах_приложения_учтено(self):
		доктайпы = frappe.get_all(
			"DocType", filters={"module": ("in", frappe.get_module_list("lms_frappe_app"))}, pluck="name"
		)
		поля = {
			(поле.parent, поле.fieldname)
			for поле in frappe.get_all(
				"DocField",
				filters={"parent": ("in", доктайпы), "fieldtype": "Link", "options": "User"},
				fields=["parent", "fieldname"],
			)
		}
		учтено = поля_реестра() | set(ВНЕ_РЕЕСТРА)
		self.assertEqual(поля - учтено, set(), "поле пользователя вне реестра")
		свои = {(доктайп, поле) for доктайп, поле in учтено if доктайп in доктайпы}
		self.assertEqual(свои - поля, set(), "строка реестра без поля")

	def test_строки_реестра_ведут_на_пользователя(self):
		for запись in реестр():
			with self.subTest(запись=запись):
				поле = frappe.get_meta(запись["doctype"]).get_field(запись["filter_by"])
				self.assertIsNotNone(поле)
				self.assertEqual((поле.fieldtype, поле.options), ("Link", "User"))
				self.assertIn(запись["mode"], РЕЖИМЫ)
				if запись["mode"] == ОБЕЗЛИЧИТЬ:
					self.assertFalse(поле.reqd, "обезличенное поле очищается")
		for (доктайп, имя), (действие, причина) in ВНЕ_РЕЕСТРА.items():
			with self.subTest(поле=(доктайп, имя)):
				self.assertTrue(причина)
				self.assertIn(действие, (None, ОБЕЗЛИЧИТЬ))
				if действие:
					self.assertFalse(frappe.get_meta(доктайп).get_field(имя).reqd)

	def test_каждая_ссылка_на_удаляемую_запись_учтена(self):
		удаляемые = sorted(удаляемые_доктайпы())
		ссылки = {
			(поле.parent, поле.fieldname)
			for поле in frappe.get_all(
				"DocField",
				filters={"fieldtype": "Link", "options": ("in", удаляемые), "parenttype": "DocType"},
				fields=["parent", "fieldname"],
			)
		} | {
			(поле.dt, поле.fieldname)
			for поле in frappe.get_all(
				"Custom Field",
				filters={"fieldtype": "Link", "options": ("in", удаляемые)},
				fields=["dt", "fieldname"],
			)
		}
		self.assertEqual(ссылки, set(ЗАВИСИМЫЕ))
		for (доктайп, имя), действие in ЗАВИСИМЫЕ.items():
			with self.subTest(поле=(доктайп, имя)):
				мета = frappe.get_meta(доктайп)
				if мета.istable:
					self.assertEqual(действие, ОТВЯЗАТЬ, "строку чужой таблицы только отвязать")
				if действие == ОТВЯЗАТЬ:
					self.assertFalse(мета.get_field(имя).reqd)


def вставить(доктайп: str, **поля) -> str:
	"""Запись без проверок: процедура работает с базой, а не с документами."""
	документ = frappe.new_doc(доктайп)
	документ.update(поля)
	документ.name = поля.get("name") or frappe.generate_hash(length=12)
	документ.db_insert()
	return документ.name


def строка_таблицы(доктайп: str, **поля) -> tuple[str, str, str]:
	"""Строка дочерней таблицы в новом родителе: `(родитель, его доктайп, строка)`."""
	таблица = frappe.get_all(
		"DocField",
		filters={"fieldtype": ("in", ["Table", "Table MultiSelect"]), "options": доктайп},
		fields=["parent", "fieldname"],
		limit=1,
	)[0]
	родитель = вставить(таблица.parent)
	строка = вставить(
		доктайп, parent=родитель, parenttype=таблица.parent, parentfield=таблица.fieldname, **поля
	)
	return родитель, таблица.parent, строка


def наполнить(пользователь: str) -> dict[str, set[str]]:
	"""По записи на каждую строку реестра и каждую удаляемую зависимость."""
	записи: dict[str, set[str]] = defaultdict(set)
	for запись in реестр():
		доктайп, поле = запись["doctype"], запись["filter_by"]
		if frappe.get_meta(доктайп).istable:
			записи[доктайп].add(строка_таблицы(доктайп, **{поле: пользователь})[2])
		else:
			записи[доктайп].add(вставить(доктайп, **{поле: пользователь}))
	for (доктайп, поле), действие in ЗАВИСИМЫЕ.items():
		if действие == УДАЛИТЬ:
			цель = frappe.get_meta(доктайп).get_field(поле).options
			записи[доктайп].add(вставить(доктайп, **{поле: sorted(записи[цель])[0]}))
	return записи


def есть(доктайп: str, имя: str) -> bool:
	return bool(frappe.db.exists(доктайп, имя))


class IntegrationTestPersonalData(IntegrationTestCase):
	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"pd-{суффикс}@example.com")
		self.другой = создать_ученика(f"pd-other-{суффикс}@example.com")

	def файл(self, сдача: str):
		"""Файл сдачи на диске. Содержимое своё: файл с тем же содержимым у
		другой записи Frappe с диска не удаляет."""
		метка = frappe.generate_hash(length=12)
		файл = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"answer-{метка}.txt",
				"content": f"homework answer {метка}".encode(),
				"is_private": 1,
				"attached_to_doctype": "Agent Homework Submission",
				"attached_to_name": сдача,
			}
		).insert(ignore_permissions=True)
		путь = файл.get_full_path()
		# Откат транзакции теста файл с диска не убирает.
		self.addCleanup(lambda: os.path.exists(путь) and os.remove(путь))
		return файл

	def test_удаление_не_оставляет_записей_ученика_а_выгрузка_их_отдаёт(self):
		мои, чужие = наполнить(self.ученик), наполнить(self.другой)
		заметка = sorted(мои["Agent Student Note"])[0]
		занятие = sorted(мои["Agent Learning Session"])[0]
		сдача = sorted(мои["Agent Homework Submission"])[0]
		файл = self.файл(сдача)
		путь = файл.get_full_path()
		версия = вставить("Version", ref_doctype="Agent Student Note", docname=заметка, data="{}")
		комментарий = вставить(
			"Comment", comment_type="Comment", reference_doctype="Agent Student Note", reference_name=заметка
		)

		# Копии в корзине: удалённая раньше заметка ученика — с историей правок — и чужая.
		def копия(ученик: str) -> tuple[str, str]:
			имя = frappe.generate_hash(length=12)
			данные = json.dumps(
				{"doctype": "Agent Student Note", "name": имя, "student": ученик, "text": "x"}
			)
			return имя, вставить(
				"Deleted Document", deleted_doctype="Agent Student Note", deleted_name=имя, data=данные
			)

		удалённая, моя_копия = копия(self.ученик)
		версия_удалённой = вставить("Version", ref_doctype="Agent Student Note", docname=удалённая, data="{}")
		_, чужая_копия = копия(self.другой)
		# Чужие записи со ссылкой на записи ученика — отвязываются.
		мой_репорт, чужой_репорт = (
			sorted(мои["Agent Course Report"])[0],
			sorted(чужие["Agent Course Report"])[0],
		)
		frappe.db.set_value("Agent Course Report", мой_репорт, "resolution", "Исправили")
		frappe.db.set_value("Agent Course Report", чужой_репорт, "duplicate_of", мой_репорт)
		чужая_заметка = вставить("Agent Student Note", student=self.другой, source_session=занятие)
		_, _, чужая_цель = строка_таблицы("Agent Lesson Run Goal", session=занятие)
		# Записи команды и чужая сдача с ним как куратором — остаются без него.
		команда = вставить(
			"Learning Organization", organization_name=frappe.generate_hash(), created_by=self.ученик
		)
		frappe.db.set_value("Learning Organization", команда, "owner", self.ученик)
		приглашение = вставить("Organization Invite", organization=команда, created_by=self.ученик)
		_, _, проверка = строка_таблицы("Agent Homework Event", by_user=self.ученик)
		согласие = вставить(
			"Agent Consent", user=self.ученик, method=РЕГИСТРАЦИЯ, accepted_at=frappe.utils.now_datetime()
		)

		выгрузка = personal_data.выгрузить(self.ученик)

		self.assertEqual(выгрузка["account"]["email"], self.ученик)
		for доктайп, имена in мои.items():
			with self.subTest(выгрузка=доктайп):
				self.assertLessEqual(имена, {з["name"] for з in выгрузка["records"].get(доктайп, [])})
		self.assertEqual([ф["name"] for ф in выгрузка["files"]], [файл.name])

		итог = personal_data.удалить(self.ученик)

		self.assertEqual(итог["files"], 1)
		for доктайп, имена in мои.items():
			режим = {з["mode"] for з in реестр() if з["doctype"] == доктайп}
			with self.subTest(удаление=доктайп):
				if ХРАНИТЬ in режим:
					self.assertTrue(all(есть(доктайп, имя) for имя in имена))
				elif ОБЕЗЛИЧИТЬ in режим:
					поле = next(з["filter_by"] for з in реестр() if з["doctype"] == доктайп)
					self.assertFalse(frappe.db.exists(доктайп, {поле: self.ученик}))
				else:
					self.assertEqual([имя for имя in имена if есть(доктайп, имя)], [])
		for доктайп, имена in чужие.items():
			with self.subTest(чужие=доктайп):
				self.assertTrue(all(есть(доктайп, имя) for имя in имена))
		self.assertFalse(есть("User", self.ученик))
		self.assertFalse(frappe.db.exists("Contact", {"email_id": self.ученик}))
		for доктайп, имя in (
			("Version", версия),
			("Comment", комментарий),
			("Deleted Document", моя_копия),
			("Version", версия_удалённой),
			("File", файл.name),
		):
			self.assertFalse(есть(доктайп, имя), доктайп)
		self.assertFalse(os.path.exists(путь))
		self.assertTrue(есть("Deleted Document", чужая_копия))
		self.assertEqual(
			frappe.db.get_value("Agent Course Report", чужой_репорт, ["duplicate_of", "resolution"]),
			(None, "Исправили"),
		)
		self.assertIsNone(frappe.db.get_value("Agent Student Note", чужая_заметка, "source_session"))
		self.assertIsNone(frappe.db.get_value("Agent Lesson Run Goal", чужая_цель, "session"))
		self.assertEqual(
			frappe.db.get_value("Learning Organization", команда, ["created_by", "owner"]),
			(None, personal_data.АНОНИМ),
		)
		self.assertIsNone(frappe.db.get_value("Organization Invite", приглашение, "created_by"))
		self.assertIsNone(frappe.db.get_value("Agent Homework Event", проверка, "by_user"))
		self.assertTrue(есть("Agent Consent", согласие))
		self.assertTrue(frappe.db.exists("Agent Consent", {"user": self.ученик, "method": УДАЛЁН}))

	def test_ссылка_вне_реестра_отменяет_удаление_целиком(self):
		заметка = вставить("Agent Student Note", student=self.ученик)
		сдача = вставить("Agent Homework Submission", member=self.ученик)
		путь = self.файл(сдача).get_full_path()
		вставить("Agent Course Release", published_by=self.ученик)

		with self.assertRaises(frappe.LinkExistsError):
			personal_data.удалить(self.ученик)

		self.assertTrue(есть("User", self.ученик))
		self.assertTrue(есть("Agent Student Note", заметка))
		self.assertTrue(os.path.exists(путь))
		self.assertFalse(frappe.db.exists("Agent Consent", {"user": self.ученик, "method": УДАЛЁН}))

	def test_администратора_и_несуществующего_процедура_не_удаляет(self):
		администратор = создать_пользователя(
			f"pd-admin-{frappe.generate_hash(length=6)}@example.com", "System Manager"
		)
		for пользователь, ошибка in (
			(администратор, frappe.ValidationError),
			("Administrator", frappe.DoesNotExistError),
			("nobody-pd@example.com", frappe.DoesNotExistError),
		):
			with self.subTest(пользователь=пользователь), self.assertRaises(ошибка):
				personal_data.удалить(пользователь)
		self.assertTrue(есть("User", администратор))

