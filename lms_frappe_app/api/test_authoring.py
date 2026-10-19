# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Методы автора курса: публикация, анонс, ревизия и граница прав.

Публикацию релиза и его проекцию в Learning проверяют `test_publish_release`
и `agent_learning/releases/test_service.py`; здесь — что вокруг неё.
"""

from functools import cached_property

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_days, now_datetime

from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases.index import РЕЛИЗ
from lms_frappe_app.api import authoring
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	привязать_главу,
	привязать_урок,
	создать_куратора,
	создать_ученика,
)


class IntegrationTestAuthoring(IntegrationTestCase):
	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"curator-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Курс {суффикс}", summary="Анонс")["data"]["id"]

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_ученик_не_правит_курсы_и_не_видит_ответов(self):
		"""Отдельный эндпоинт ничего не защищает — защищает эта проверка."""
		курс = authoring.publish_release(release=пример_релиза(f"pupil-{frappe.generate_hash(length=6)}"))[
			"data"
		]["course"]
		ученик = создать_ученика(f"pupil-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(ученик)

		for вызов in (
			lambda: authoring.list_courses(),
			lambda: authoring.course_release(course=курс),
			lambda: authoring.course_release(course=курс, lesson="l-1"),
			lambda: authoring.update_course(course=self.курс, title="Свой"),
			lambda: authoring.publish_course(course=курс),
			lambda: authoring.announce_course(course=self.курс, objectives="Цель"),
		):
			with self.assertRaises(frappe.PermissionError):
				вызов()


class IntegrationTestAuthoringEdits(IntegrationTestCase):
	"""Список курсов и правка анонса."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"editor-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.ключ = f"edits-{суффикс}"

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_курс_находится_по_списку(self):
		"""Без списка идентификатор курса взять негде — все методы требуют его."""
		курс = authoring.publish_release(release=пример_релиза(self.ключ))["data"]["course"]

		курсы = authoring.list_courses()["data"]["courses"]

		наш = [строка for строка in курсы if строка["id"] == курс]
		self.assertTrue(наш, "опубликованный релизом курс не виден в списке")
		self.assertFalse(наш[0]["published"])
		self.assertEqual(наш[0]["lessons_total"], 3)

	def test_правится_только_анонс_без_уроков(self):
		"""Курс без релиза, но с уроками — заведёнными мимо релиза, в Learning, —
		не анонс: программу курсу даёт релиз."""
		курс = authoring.create_course(title="С уроком", summary="было")["data"]["id"]
		frappe.set_user("Administrator")
		глава = frappe.get_doc({"doctype": "Course Chapter", "title": "Глава", "course": курс}).insert(
			ignore_permissions=True
		)
		урок = frappe.get_doc({"doctype": "Course Lesson", "title": "Урок", "chapter": глава.name}).insert(
			ignore_permissions=True
		)
		привязать_главу(курс, глава.name)
		привязать_урок(глава.name, урок.name)
		frappe.set_user(self.куратор)

		ответ = authoring.update_course(course=курс, title="Стало")

		self.assertEqual(ответ["error"]["code"], "course_has_content")
		self.assertEqual(ответ["error"]["lessons"], 1)
		self.assertNotEqual(frappe.db.get_value("LMS Course", курс, "title"), "Стало")

		анонс = authoring.create_course(title="Анонс", summary="было")["data"]["id"]
		ответ = authoring.update_course(course=анонс, title="Стало", summary="и описание")
		self.assertEqual(ответ["data"]["title"], "Стало")
		self.assertEqual(frappe.db.get_value("LMS Course", анонс, "short_introduction"), "и описание")


class IntegrationTestCourseRevision(IntegrationTestCase):
	"""Отметка изменения курса: по ней кабинет автора узнаёт, что курс поменялся,
	не перечитывая его целиком (#261). Курс правится только новым релизом (#512)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"revision-{суффикс}@example.com")
		self.ключ = f"revision-{суффикс}"
		frappe.set_user(self.куратор)

	@cached_property
	def курс(self) -> str:
		"""Курс из релиза — при первом обращении: тесту анонса он не нужен."""
		прежний = frappe.session.user
		frappe.set_user(self.куратор)
		try:
			return self.опубликовать()["course"]
		finally:
			frappe.set_user(прежний)

	def опубликовать(self, релиз: dict | None = None) -> dict:
		ответ = authoring.publish_release(release=релиз or пример_релиза(self.ключ))
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def отметки(self) -> dict:
		return authoring.course_revision(course=self.курс)["data"]

	def ревизия(self) -> str:
		return self.отметки()["revision"]

	def состарить(self, курс: str | None = None) -> str:
		"""Отметки курса (по умолчанию — своего), его релиза и заметок — на день
		назад; возвращает ревизию курса.

		`Why:` ревизия — это метки `modified`, а часы одноразового контейнера
		тестов отступают назад и совпадают до микросекунды: «следующая запись
		позже» держится только против метки, заведомо оставшейся в прошлом
		(learning-services#509).
		"""
		курс = курс or self.курс
		назад = add_days(now_datetime(), -1)
		frappe.db.set_value("LMS Course", курс, "modified", назад, update_modified=False)
		релиз = frappe.db.get_value("LMS Course", курс, "active_release")
		if релиз:
			frappe.db.set_value(РЕЛИЗ, релиз, "modified", назад, update_modified=False)
		for заметка in frappe.get_all("Agent Author Note", filters={"course": курс}, pluck="name"):
			frappe.db.set_value("Agent Author Note", заметка, "modified", назад, update_modified=False)
		return authoring.course_revision(course=курс)["data"]["revision"]

	def test_новый_релиз_двигает_ревизию_а_тот_же_и_чтение_нет(self):
		до = self.ревизия()
		authoring.course_release(course=self.курс)
		authoring.course_release(course=self.курс, lesson="l-1")
		authoring.course_releases(course=self.курс)
		self.assertEqual(self.ревизия(), до)

		self.опубликовать()
		self.assertEqual(self.ревизия(), до)

		до = self.состарить()
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, переписанный"
		self.опубликовать(релиз)

		self.assertGreater(self.ревизия(), до)

	def test_заметка_двигает_свою_метку_а_не_ревизию(self):
		до = self.отметки()
		self.assertIsNone(до["notes_revision"])

		ид = authoring.add_note(course=self.курс, target="course", text="Проверь карточку")["data"]["id"]
		после_записи = self.отметки()
		self.assertEqual(после_записи["revision"], до["revision"])
		self.assertIsNotNone(после_записи["notes_revision"])

		self.состарить()
		перед_ответом = self.отметки()
		authoring.reply_note(note=ид, text="Уточню: обещание")
		после_ответа = self.отметки()

		self.assertEqual(после_ответа["revision"], перед_ответом["revision"])
		self.assertGreater(после_ответа["notes_revision"], перед_ответом["notes_revision"])

	def test_снятие_с_публикации_и_анонс_двигают_ревизию(self):
		было = self.состарить()
		authoring.publish_course(course=self.курс)
		self.assertGreater(self.ревизия(), было)
		до = self.состарить()
		authoring.unpublish_course(course=self.курс)
		self.assertGreater(self.ревизия(), до)

		до = self.состарить()
		ответ = authoring.announce_course(course=self.курс)

		self.assertTrue(ответ["ok"], ответ)
		self.assertGreater(self.ревизия(), до)

	def test_анонс_без_релиза_отмечается_карточкой(self):
		анонс = authoring.create_course(title="Анонс", summary="было")["data"]["id"]
		до = self.состарить(анонс)
		authoring.update_course(course=анонс, summary="стало")

		self.assertGreater(authoring.course_revision(course=анонс)["data"]["revision"], до)

	def test_ученику_ревизия_недоступна(self):
		ученик = создать_ученика(f"revision-s-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(ученик)

		with self.assertRaises(frappe.PermissionError):
			authoring.course_revision(course=self.курс)


class IntegrationTestLessonHookAndPromise(IntegrationTestCase):
	"""Зачин урока и обещание курса (#238).

	Адресат обоих — ученик, а директива адресована агенту; поэтому это поля
	`Course Lesson` и `LMS Course`, а не поля директив (решение владельца 1Б).
	"""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"curator-{суффикс}@example.com")
		frappe.set_user(self.куратор)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_обещание_курса_задаётся_и_очищается(self):
		анонс = authoring.create_course(title="Анонс", summary="Без уроков")["data"]["id"]
		ответ = authoring.update_course(course=анонс, promise="Уйдёте с готовым канвасом")
		self.assertEqual(ответ["data"]["promise"], "Уйдёте с готовым канвасом")
		self.assertEqual(
			frappe.db.get_value("LMS Course", анонс, "course_promise"), "Уйдёте с готовым канвасом"
		)

		authoring.update_course(course=анонс, promise="")
		self.assertFalse(frappe.db.get_value("LMS Course", анонс, "course_promise"))


class TestПараметрыАвтора(UnitTestCase):
	"""Без базы: разбор параметров методов автора (learning-services#536).

	Проводку проверяют `api/test_goal_runs.py` и `api/test_publish_release.py`.
	"""

	def test_целое_страницы(self):
		"""`_целое(значение, по_умолчанию, поле, наименьшее)`: пусто — умолчание,
		строка цифр — число."""
		случаи = (
			("None — умолчание", None, 0, (20,)),
			("пустая строка — умолчание", "", 1, (20,)),
			("число", 5, 1, (5,)),
			("ноль при наименьшем 0", 0, 0, (0,)),
			("наименьшее включительно", 1, 1, (1,)),
			("строка цифр", "7", 0, (7,)),
			("строка цифр с пробелами", " 7 ", 0, (7,)),
			("ноль меньше наименьшего", 0, 1, ("invalid_page", 0)),
			("строка 0 меньше наименьшего — значение числом", "0", 1, ("invalid_page", 0)),
			("отрицательное", -1, 0, ("invalid_page", -1)),
			("отрицательное строкой", "-1", 1, ("invalid_page", "-1")),
			("слово", "десять", 1, ("invalid_page", "десять")),
			("цифры с буквой", "1a", 0, ("invalid_page", "1a")),
			("только пробелы", "  ", 0, ("invalid_page", "  ")),
			("дробное", 2.5, 1, ("invalid_page", 2.5)),
			("True — не число", True, 0, ("invalid_page", True)),
			("False — не число", False, 0, ("invalid_page", False)),
		)
		for что, значение, наименьшее, ожидание in случаи:
			with self.subTest(что):
				try:
					получено = (authoring._целое(значение, 20, "limit", наименьшее),)
				except Отказ as отказ:
					self.assertEqual(sorted(отказ.подробности), ["value", "where"])
					self.assertEqual(отказ.подробности["where"], "limit")
					получено = (отказ.код, отказ.подробности["value"])
				self.assertEqual(получено, ожидание)
				# Why: `0 == False` и `1 == True` — без сверки типа строки с bool сошлись бы.
				self.assertIs(type(получено[-1]), type(ожидание[-1]))

	def test_коммит(self):
		"""Полный хеш git ровно как печатает `git rev-parse`, иначе `invalid_commit`."""
		sha1 = "0123456789abcdef0123456789abcdef01234567"
		sha256 = "fedcba9876543210" * 4
		случаи = (
			("SHA-1", sha1, (sha1,)),
			("SHA-256", sha256, (sha256,)),
			("пустая строка", "", ("invalid_commit",)),
			("короткий хеш", sha1[:7], ("invalid_commit",)),
			("верхний регистр", sha1.upper(), ("invalid_commit",)),
			("41 знак", sha1 + "8", ("invalid_commit",)),
			("63 знака", sha256[:63], ("invalid_commit",)),
			("пробел в начале", f" {sha1}", ("invalid_commit",)),
			("перевод строки в конце", f"{sha1}\n", ("invalid_commit",)),
			("не 0-9a-f", "g" * 40, ("invalid_commit",)),
			("число", 40, ("invalid_commit",)),
			("список", [sha1], ("invalid_commit",)),
			("None", None, ("invalid_commit",)),
		)
		for что, значение, ожидание in случаи:
			with self.subTest(что):
				try:
					получено = (authoring._коммит(значение),)
				except Отказ as отказ:
					получено = (отказ.код,)
				self.assertEqual(получено, ожидание)
