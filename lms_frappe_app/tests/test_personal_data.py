# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Выгрузка и удаление данных ученика, реестр данных пользователя и сроки
журналов (lms-high-time/learning-services#689).

Ворота — `UnitTestPersonalDataRegistry`: новое поле со ссылкой на
пользователя или на удаляемую запись без строки в реестре валит прогон.
Процедура проверяется на записях каждого доктайпа реестра: они заводятся
прямой вставкой по реестру, а не через методы — так тест покрывает и
доктайп, добавленный в реестр завтра.
"""

import json
import os
from collections import defaultdict
from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app import personal_data
from lms_frappe_app.agent_learning.consent import РЕГИСТРАЦИЯ, УДАЛЁН
from lms_frappe_app.personal_data import (
	ВНЕ_РЕЕСТРА,
	ЗАВИСИМЫЕ,
	ОБЕЗЛИЧИТЬ,
	ОТВЯЗАТЬ,
	СРОКИ_ЖУРНАЛОВ,
	УДАЛИТЬ,
	ХРАНИТЬ,
	реестр,
)
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	создать_организацию,
	создать_пользователя,
	создать_урок,
	создать_ученика,
)

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
				self.assertIn(запись["mode"], РЕЖИМЫ)
				if запись["filter_by"] == "owner":
					self.assertEqual(запись["mode"], УДАЛИТЬ, "владельца не очищают")
					continue
				self.assertIsNotNone(поле)
				self.assertEqual((поле.fieldtype, поле.options), ("Link", "User"))
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
	if "owner" in поля:
		# Владельца `db_insert` ставит текущего пользователя.
		frappe.db.set_value(доктайп, документ.name, "owner", поля["owner"], update_modified=False)
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


def наполнить(пользователь: str) -> tuple[dict, dict[str, set[str]]]:
	"""По записи на каждую строку реестра и каждую удаляемую зависимость:
	`({(доктайп, поле, режим): имена}, {доктайп зависимой: имена})`."""
	по_реестру: dict[tuple[str, str, str], set[str]] = defaultdict(set)
	все: dict[str, set[str]] = defaultdict(set)
	for запись in реестр():
		доктайп, поле = запись["doctype"], запись["filter_by"]
		if frappe.get_meta(доктайп).istable:
			имя = строка_таблицы(доктайп, **{поле: пользователь})[2]
		else:
			имя = вставить(доктайп, **{поле: пользователь})
		по_реестру[(доктайп, поле, запись["mode"])].add(имя)
		все[доктайп].add(имя)
	зависимые: dict[str, set[str]] = defaultdict(set)
	for (доктайп, поле), действие in ЗАВИСИМЫЕ.items():
		if действие == УДАЛИТЬ:
			цель = frappe.get_meta(доктайп).get_field(поле).options
			зависимые[доктайп].add(вставить(доктайп, **{поле: sorted(все[цель])[0]}))
	return по_реестру, зависимые


def есть(доктайп: str, имя: str) -> bool:
	return bool(frappe.db.exists(доктайп, имя))


@contextmanager
def без_коммитов():
	"""Процедура коммитит — закрытие доступа и итог; в тесте откат класса
	должен убрать всё."""
	with patch.object(frappe.db, "commit") as коммит:
		yield коммит


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

	def сессия(self) -> str:
		sid = frappe.generate_hash()
		frappe.db.sql(
			"insert into `tabSessions` (user, sid, sessiondata, lastupdate, status) values (%s, %s, %s, now(), 'Active')",
			(self.ученик, sid, "{}"),
		)
		return sid

	def замечание(self, занятие: str, **поля) -> str:
		урок = создать_урок(f"Замечание {frappe.generate_hash(length=6)}")
		глава = frappe.db.get_value("Course Lesson", урок, "chapter")
		курс = frappe.db.get_value("Course Chapter", глава, "course")
		return вставить(
			"Agent Course Report",
			session=занятие,
			course=курс,
			lesson=урок,
			kind="Stuck",
			status="New",
			**поля,
		)

	def test_удаление_не_оставляет_записей_ученика_а_выгрузка_их_отдаёт(self):
		(мои, мои_зависимые), (чужие, чужие_зависимые) = наполнить(self.ученик), наполнить(self.другой)

		def одна(доктайп: str, поле: str) -> str:
			return next(iter(next(имена for (д, п, _), имена in мои.items() if (д, п) == (доктайп, поле))))

		заметка = одна("Agent Student Note", "student")
		занятие = одна("Agent Learning Session", "student")
		сдача = одна("Agent Homework Submission", "member")
		файл = self.файл(сдача)
		путь = файл.get_full_path()
		self.сессия()
		версия = вставить("Version", ref_doctype="Agent Student Note", docname=заметка, data="{}")
		комментарий = вставить(
			"Comment", comment_type="Comment", reference_doctype="Agent Student Note", reference_name=заметка
		)

		# Копии в корзине: удалённая раньше заметка ученика — с историей правок —,
		# ответ квиза, где он только владелец, и чужая заметка.
		def копия(доктайп: str, **поля) -> tuple[str, str]:
			имя = frappe.generate_hash(length=12)
			данные = json.dumps({"doctype": доктайп, "name": имя, **поля})
			return имя, вставить("Deleted Document", deleted_doctype=доктайп, deleted_name=имя, data=данные)

		удалённая, моя_копия = копия("Agent Student Note", student=self.ученик, text="x")
		версия_удалённой = вставить("Version", ref_doctype="Agent Student Note", docname=удалённая, data="{}")
		_, копия_ответа = копия("Agent Quiz Answer", owner=self.ученик, answer="x")
		_, чужая_копия = копия("Agent Student Note", student=self.другой, text="x")
		# Замечание к курсу остаётся автору — без занятия, почты и имени; дубль
		# чужого замечания сохраняет оригинал.
		frappe.db.set_value("User", self.ученик, "full_name", "Ученик Удаляемый")
		моё_замечание = self.замечание(
			занятие,
			owner=self.ученик,
			text=f"{self.ученик} (Ученик Удаляемый) не понял пример",
			resolution="Ученик Удаляемый, поправили",
		)
		чужое_занятие = next(
			iter(next(и for (д, п, _), и in чужие.items() if (д, п) == ("Agent Learning Session", "student")))
		)
		дубль = self.замечание(чужое_занятие, text="То же", duplicate_of=моё_замечание)
		# Чужие записи со ссылкой на записи ученика — отвязываются.
		чужая_заметка = вставить("Agent Student Note", student=self.другой, source_session=занятие)
		_, _, чужая_цель = строка_таблицы("Agent Lesson Run Goal", session=занятие)
		# Записи команды — остаются без него, с ними его правки и комментарии.
		команда = вставить(
			"Learning Organization", organization_name=frappe.generate_hash(), created_by=self.ученик
		)
		frappe.db.set_value("Learning Organization", команда, "owner", self.ученик)
		правка_команды = вставить(
			"Version", ref_doctype="Learning Organization", docname=команда, data="{}", owner=self.ученик
		)
		комментарий_команде = вставить(
			"Comment",
			comment_type="Comment",
			reference_doctype="Learning Organization",
			reference_name=команда,
			comment_email=self.ученик,
		)
		приглашение = вставить("Organization Invite", organization=команда, created_by=self.ученик)
		согласие = вставить(
			"Agent Consent", user=self.ученик, method=РЕГИСТРАЦИЯ, accepted_at=frappe.utils.now_datetime()
		)

		выгрузка = personal_data.выгрузить(self.ученик)

		self.assertEqual(выгрузка["account"]["email"], self.ученик)
		выгружаемые = [(д, и) for (д, _, _), и in мои.items()] + list(мои_зависимые.items())
		for доктайп, имена in выгружаемые:
			with self.subTest(выгрузка=доктайп):
				self.assertLessEqual(имена, {з["name"] for з in выгрузка["records"].get(доктайп, [])})
		self.assertIn(моё_замечание, {з["name"] for з in выгрузка["records"]["Agent Course Report"]})
		self.assertEqual(
			set(выгрузка["records"]["Learning Organization"][0]), {"name", "organization_name", "creation"}
		)
		self.assertEqual([ф["name"] for ф in выгрузка["files"]], [файл.name])

		with без_коммитов() as коммит:
			итог = personal_data.удалить(self.ученик)

		self.assertTrue(коммит.called)
		self.assertEqual(итог["files"], 1)
		for (доктайп, поле, режим), имена in мои.items():
			with self.subTest(удаление=(доктайп, поле)):
				if режим == УДАЛИТЬ:
					self.assertEqual([имя for имя in имена if есть(доктайп, имя)], [])
				else:
					self.assertTrue(all(есть(доктайп, имя) for имя in имена))
					if режим == ОБЕЗЛИЧИТЬ:
						self.assertFalse(frappe.db.exists(доктайп, {поле: self.ученик}))
		for доктайп, имена in мои_зависимые.items():
			with self.subTest(зависимые=доктайп):
				self.assertEqual([имя for имя in имена if есть(доктайп, имя)], [])
		for доктайп, имена in [*((д, и) for (д, _, _), и in чужие.items()), *чужие_зависимые.items()]:
			with self.subTest(чужие=доктайп):
				self.assertTrue(all(есть(доктайп, имя) for имя in имена))
		self.assertFalse(есть("User", self.ученик))
		self.assertFalse(frappe.db.exists("Contact", {"email_id": self.ученик}))
		self.assertFalse(frappe.db.exists("Sessions", {"user": self.ученик}))
		for доктайп, имя in (
			("Version", версия),
			("Comment", комментарий),
			("Deleted Document", моя_копия),
			("Version", версия_удалённой),
			("Deleted Document", копия_ответа),
			("Version", правка_команды),
			("Comment", комментарий_команде),
			("File", файл.name),
		):
			self.assertFalse(есть(доктайп, имя), доктайп)
		self.assertFalse(os.path.exists(путь))
		self.assertTrue(есть("Deleted Document", чужая_копия))
		замечание = frappe.db.get_value(
			"Agent Course Report", моё_замечание, ["session", "owner", "text", "resolution"], as_dict=True
		)
		self.assertEqual(
			замечание,
			{
				"session": None,
				"owner": personal_data.АНОНИМ,
				"text": f"{personal_data.УБРАНО} ({personal_data.УБРАНО}) не понял пример",
				"resolution": f"{personal_data.УБРАНО}, поправили",
			},
		)
		self.assertEqual(frappe.db.get_value("Agent Course Report", дубль, "duplicate_of"), моё_замечание)
		# Автор разбирает обезличенное замечание как обычное.
		документ = frappe.get_doc("Agent Course Report", моё_замечание)
		документ.status = "In Progress"
		документ.save(ignore_permissions=True)
		self.assertIsNone(frappe.db.get_value("Agent Student Note", чужая_заметка, "source_session"))
		self.assertIsNone(frappe.db.get_value("Agent Lesson Run Goal", чужая_цель, "session"))
		self.assertEqual(
			frappe.db.get_value("Learning Organization", команда, ["created_by", "owner"]),
			(None, personal_data.АНОНИМ),
		)
		self.assertIsNone(frappe.db.get_value("Organization Invite", приглашение, "created_by"))
		self.assertTrue(есть("Agent Consent", согласие))
		self.assertTrue(frappe.db.exists("Agent Consent", {"user": self.ученик, "method": УДАЛЁН}))

	def test_ссылка_вне_реестра_отменяет_удаление_а_доступ_закрыт(self):
		заметка = вставить("Agent Student Note", student=self.ученик)
		сдача = вставить("Agent Homework Submission", member=self.ученик)
		путь = self.файл(сдача).get_full_path()
		вставить("Agent Course Release", published_by=self.ученик)
		self.сессия()

		with без_коммитов(), self.assertRaises(frappe.LinkExistsError):
			personal_data.удалить(self.ученик)

		self.assertTrue(есть("User", self.ученик))
		self.assertTrue(есть("Agent Student Note", заметка))
		self.assertTrue(os.path.exists(путь))
		self.assertFalse(frappe.db.exists("Agent Consent", {"user": self.ученик, "method": УДАЛЁН}))
		self.assertFalse(frappe.db.exists("Sessions", {"user": self.ученик}))

	def test_процедура_удаляет_только_учеников(self):
		суффикс = frappe.generate_hash(length=6)
		администратор = создать_пользователя(f"pd-admin-{суффикс}@example.com", "System Manager")
		служебный = создать_пользователя(f"pd-service-{суффикс}@example.com", "Agent Service")
		единственный = создать_ученика(f"pd-org-admin-{суффикс}@example.com")
		добавить_в_организацию(единственный, создать_организацию(f"Команда {суффикс}"), "Org Admin")
		for пользователь, ошибка in (
			(администратор, frappe.ValidationError),
			(служебный, frappe.ValidationError),
			(единственный, frappe.ValidationError),
			("Administrator", frappe.DoesNotExistError),
			("nobody-pd@example.com", frappe.DoesNotExistError),
		):
			with self.subTest(пользователь=пользователь), без_коммитов(), self.assertRaises(ошибка):
				personal_data.удалить(пользователь)
		for пользователь in (администратор, служебный, единственный):
			self.assertTrue(есть("User", пользователь))

	def test_штатные_запросы_frappe_закрыты(self):
		frappe.db.set_single_value("Website Settings", "auto_account_deletion", 72)
		for форма in personal_data.ФОРМЫ_FRAPPE:
			frappe.db.set_value("Web Form", форма, "published", 1)

		personal_data.закрыть_штатные_запросы()

		self.assertEqual(frappe.db.get_single_value("Website Settings", "auto_account_deletion"), 0)
		for форма in personal_data.ФОРМЫ_FRAPPE:
			self.assertEqual(frappe.db.get_value("Web Form", форма, "published"), 0)
		for запрос in (
			{"doctype": "Personal Data Deletion Request", "email": self.ученик},
			{"doctype": "Personal Data Download Request", "user": self.ученик},
		):
			with self.subTest(запрос["doctype"]), self.assertRaises(frappe.ValidationError):
				frappe.get_doc(запрос).insert(ignore_permissions=True)


class IntegrationTestLogRetention(IntegrationTestCase):
	def test_сроки_журналов_держатся_политики(self):
		настройки = frappe.get_single("Log Settings")
		настройки.logs_to_clear = [
			строка for строка in настройки.logs_to_clear if строка.ref_doctype != "Error Log"
		]
		for строка in настройки.logs_to_clear:
			if строка.ref_doctype == "Activity Log":
				строка.days = 365
		настройки.save(ignore_permissions=True)

		сроки = {
			строка.ref_doctype: строка.days for строка in frappe.get_single("Log Settings").logs_to_clear
		}
		self.assertEqual({доктайп: сроки.get(доктайп) for доктайп in СРОКИ_ЖУРНАЛОВ}, СРОКИ_ЖУРНАЛОВ)
