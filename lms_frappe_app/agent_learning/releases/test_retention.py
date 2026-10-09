# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Содержимое хранится только у действующего релиза (learning-services#514)."""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import release_quiz
from lms_frappe_app.agent_learning.constants import (
	АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ,
	ПОПЫТКА_АННУЛИРОВАНА,
	ПОПЫТКА_ЗАЧТЕНА,
	ПОПЫТКА_ИДЁТ,
)
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases import index, places, retention, service
from lms_frappe_app.agent_learning.releases.course_guard import ИЗ_РЕЛИЗА
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.api import authoring, manager, student
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	занятие_релиза,
	зачислить_на_курс,
	отметить_все_пункты,
	политика_по_умолчанию,
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
	урок_релиза,
)
from lms_frappe_app.tests.test_author_page import страница

ПОПЫТКА = "Agent Quiz Attempt"
КОММИТ = "3e7a1c0d9b2f4e6a8c1d3f5b7e9a0c2d4f6b8e1a"


def без_урока(ключ_курса: str, ключ_урока: str) -> dict:
	"""Образец релиза без урока `ключ_урока` первой главы."""
	релиз = пример_релиза(ключ_курса)
	релиз["chapters"][0]["lessons"].remove(ключ_урока)
	релиз["lessons"] = [у for у in релиз["lessons"] if у["key"] != ключ_урока]
	del релиз["agent"]["lessons"][ключ_урока]
	return релиз


class IntegrationTestОсвобождение(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"retention-{суффикс}@example.com")
		self.ученик = создать_ученика(f"retention-pupil-{суффикс}@example.com")
		self.ключ = f"retention-{суффикс}"
		self.суффикс = суффикс

	def опубликовать(self, релиз: dict | None = None, коммит: str | None = None) -> dict:
		return service.опубликовать(релиз or пример_релиза(self.ключ), None, self.куратор, коммит=коммит)

	def строк_индекса(self, релиз: str) -> dict[str, int]:
		return {
			таблица: frappe.db.count(таблица, {"parenttype": index.РЕЛИЗ, "parent": релиз})
			for таблица in retention.ТАБЛИЦЫ_ИНДЕКСА
		}

	def освобождён(self, релиз: str) -> None:
		запись = frappe.db.get_value(index.РЕЛИЗ, релиз, ["snapshot", "agent_frame"], as_dict=True)
		self.assertEqual((запись.snapshot, запись.agent_frame), (None, None), релиз)
		self.assertEqual(set(self.строк_индекса(релиз).values()), {0}, релиз)

	def цел(self, релиз: str) -> None:
		self.assertEqual(
			json.loads(frappe.db.get_value(index.РЕЛИЗ, релиз, "snapshot"))["format"], "lms-release/1"
		)
		self.assertNotIn(0, self.строк_индекса(релиз).values(), релиз)

	# --- освобождение ---

	def test_таблицы_индекса_все_дочерние_таблицы_релиза(self):
		self.assertEqual(
			set(retention.ТАБЛИЦЫ_ИНДЕКСА),
			{поле.options for поле in frappe.get_meta(index.РЕЛИЗ).get_table_fields()},
		)

	def test_публикация_освобождает_прежнюю_версию_и_оставляет_запись(self):
		первый = self.опубликовать(коммит=КОММИТ)
		поля = ["course", "course_key", "version", "release_format", "digest", "source_commit"]
		поля += ["document_key", "published_by", "published_at"]
		запись_до = frappe.db.get_value(index.РЕЛИЗ, первый["release"], поля, as_dict=True)
		кэш = places.ключ_кэша(первый["release"], запись_до.digest)
		frappe.cache.set_value(кэш, {"узел": "текст"})
		self.addCleanup(frappe.cache.delete_value, кэш)
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["title"] = "Урок первый, исправленный"

		второй = self.опубликовать(релиз)

		self.освобождён(первый["release"])
		self.цел(второй["release"])
		self.assertEqual(frappe.db.get_value(index.РЕЛИЗ, первый["release"], поля, as_dict=True), запись_до)
		self.assertIsNone(frappe.cache.get_value(кэш))
		self.assertEqual([р.version for р in index.история(первый["course"])], [2, 1])
		with self.assertRaises(ValueError):
			index.снимок(первый["release"])

	def test_действующий_не_освобождается_освобождённый_повторно_ничего(self):
		первый = self.опубликовать()
		self.assertEqual(retention.освободить_прежние(первый["course"]), [])
		self.цел(первый["release"])

		релиз = пример_релиза(self.ключ)
		релиз["course"]["title"] = "Пример курса, второе издание"
		with patch.object(retention, "освободить_прежние"):
			второй = self.опубликовать(релиз)
		self.цел(первый["release"])

		освобождены = retention.освободить_прежние(первый["course"])

		запись = frappe.db.get_value(
			index.РЕЛИЗ, первый["release"], ["version", "digest", "published_at"], as_dict=True
		)
		self.assertEqual(
			освобождены,
			[{"name": первый["release"], **запись, "source_commit": None}],
		)
		self.освобождён(первый["release"])
		self.цел(второй["release"])
		self.assertEqual(retention.освободить_прежние(первый["course"]), [])

	def test_освобождённая_версия_не_становится_действующей(self):
		первый = self.опубликовать()
		релиз = пример_релиза(self.ключ)
		релиз["course"]["title"] = "Пример курса, второе издание"
		второй = self.опубликовать(релиз)
		курс = frappe.get_doc("LMS Course", первый["course"])
		курс.active_release = первый["release"]
		курс.flags[ИЗ_РЕЛИЗА] = True

		with self.assertRaisesRegex(frappe.ValidationError, "освобождено"):
			курс.save()

		frappe.clear_last_message()
		self.assertEqual(
			frappe.db.get_value("LMS Course", первый["course"], "active_release"), второй["release"]
		)

	def test_откат_публикации_откатывает_освобождение_и_попытки(self):
		первый = self.опубликовать()
		курс = первый["course"]
		зачислить_на_курс(self.ученик, курс)
		run = прохождения.прохождение(self.ученик, курс, "l-1")
		попытка = release_quiz.начать(run, занятие_релиза(self.ученик, курс, "l-1"))["attempt"]
		релиз = пример_релиза(self.ключ)
		релиз["lessons"][0]["quiz"]["questions"][0]["text"] = "Другая ситуация"
		освободить = retention.освободить_прежние

		def освободить_и_отказать(курс_релиза):
			освободить(курс_релиза)
			raise Отказ("boom", "сбой после освобождения")

		with patch.object(retention, "освободить_прежние", side_effect=освободить_и_отказать):
			with self.assertRaises(Отказ):
				self.опубликовать(релиз)

		self.цел(первый["release"])
		self.assertEqual(frappe.db.get_value("LMS Course", курс, "active_release"), первый["release"])
		self.assertEqual(frappe.db.count(index.РЕЛИЗ, {"course": курс}), 1)
		self.assertEqual(
			frappe.db.get_value(ПОПЫТКА, попытка, ["status", "release"]), (ПОПЫТКА_ИДЁТ, первый["release"])
		)

	# --- сквозной сценарий ---

	def test_данные_ученика_и_автора_живут_без_прежних_версий(self):
		"""v1 — урок `l-1` пройден, заметка и репорт по нему, квиз `l-2` начат;
		v2 — `l-1` снят, квиз `l-2` тот же; v3 — `l-1` вернулся, квиз `l-2`
		изменён. v1 и v2 освобождены целиком, а методы автора, руководителя и
		старт занятия подписывают всё без их содержимого."""
		v1 = self.опубликовать()
		курс = v1["course"]
		организация = создать_организацию(f"Компания {self.суффикс}")
		добавить_в_организацию(self.ученик, организация)
		frappe.get_doc({"doctype": "Course Allocation", "organization": организация, "course": курс}).insert(
			ignore_permissions=True
		)
		менеджер = создать_менеджера(f"retention-mg-{self.суффикс}@example.com", организация)
		зачислить_на_курс(self.ученик, курс)

		первое = занятие_релиза(self.ученик, курс, "l-1")
		run = frappe.db.get_value("Agent Learning Session", первое, "run")
		отметить_все_пункты(run, первое)
		начало = release_quiz.начать(прохождения.прохождение(self.ученик, курс, "l-1"), первое)
		release_quiz.ответить(начало["attempt"], начало["question"]["id"], "V1", "Первый")
		self.assertEqual(frappe.db.get_value(ПОПЫТКА, начало["attempt"], "status"), ПОПЫТКА_ЗАЧТЕНА)
		второе = занятие_релиза(self.ученик, курс, "l-2")
		прохождения.отметить(
			frappe.db.get_value("Agent Learning Session", второе, "run"),
			"term:T1",
			"done",
			"Свидетельство",
			занятие=второе,
		)
		открытая = release_quiz.начать(прохождения.прохождение(self.ученик, курс, "l-2"), второе)["attempt"]

		frappe.set_user(self.ученик)
		репорт = student.report_issue(session=первое, kind="stuck", text="Застрял на примере")
		self.assertTrue(репорт["ok"], репорт)
		frappe.set_user(self.куратор)
		заметка = authoring.add_note(course=курс, target="objective.l-1-D1", text="Цель расплывчата")
		self.assertTrue(заметка["ok"], заметка)
		self.заметка = заметка["data"]["id"]
		frappe.set_user("Administrator")

		v2 = self.опубликовать(без_урока(self.ключ, "l-1"))
		self.освобождён(v1["release"])
		self.assertEqual(
			frappe.db.get_value(ПОПЫТКА, открытая, ["status", "release"]), (ПОПЫТКА_ИДЁТ, v2["release"])
		)
		self.проверить_автору(курс)

		v3_релиз = пример_релиза(self.ключ)
		v3_релиз["lessons"][1]["quiz"]["questions"][0]["text"] = "Другая ситуация"
		v3 = self.опубликовать(v3_релиз)
		for релиз in (v1, v2):
			self.освобождён(релиз["release"])
		self.цел(v3["release"])
		self.assertEqual(v3["lessons"]["restored"], ["l-1"])
		self.assertEqual(
			frappe.db.get_value(ПОПЫТКА, открытая, ["status", "cancel_reason"]),
			(ПОПЫТКА_АННУЛИРОВАНА, АННУЛИРОВАНА_КВИЗ_ИЗМЕНИЛСЯ),
		)
		self.проверить_автору(курс)

		frappe.set_user(self.ученик)
		старт = student.start_lesson(lesson=урок_релиза(курс, "l-3"))
		self.assertTrue(старт["ok"], старт)
		история = {у["key"]: у for у in старт["data"]["history"]["lessons"]}
		self.assertEqual(
			(история["l-1"]["title"], история["l-1"]["status"], история["l-1"]["objectives_open"]),
			("Урок первый", "passed", []),
		)
		self.assertEqual(история["l-2"]["title"], "Урок второй")
		self.assertEqual([ц["text"] for ц in история["l-2"]["objectives_open"]], ["Цель урока «Урок второй»"])

		frappe.set_user(менеджер)
		подробности = manager.student_detail(self.ученик)
		self.assertTrue(подробности["ok"], подробности)
		цели = {
			ц["key"]: (ц["text"], ц["status"])
			for с in подробности["data"]["sessions"]
			if с["course"] == курс
			for ц in с["objectives"]
		}
		self.assertEqual(цели["l-1-D1"], ("Цель урока «Урок первый»", "covered"))
		self.assertEqual(цели["l-2-D1"], ("Цель урока «Урок второй»", "touched"))
		попытки = {п["status"] for п in подробности["data"]["quiz_attempts"]}
		self.assertEqual(попытки, {ПОПЫТКА_ЗАЧТЕНА, ПОПЫТКА_АННУЛИРОВАНА})

	def проверить_автору(self, курс: str) -> None:
		"""Прохождения, заметки и репорты урока `l-1` — с подписями, в том числе пока он снят."""
		frappe.set_user(self.куратор)
		try:
			прохождения_урока = authoring.goal_runs(course=курс, lesson="l-1")
			self.assertTrue(прохождения_урока["ok"], прохождения_урока)
			[строка] = прохождения_урока["data"]["runs"]
			self.assertEqual(строка["lesson"], {"key": "l-1", "title": "Урок первый"})
			self.assertEqual([ц["objective_key"] for ц in строка["objectives"]], ["l-1-D1"])
			self.assertEqual(
				[п["title"] for п in строка["goals"] if п["goal_key"] == "term:T1"], ["Термин «пример»"]
			)

			заметки = authoring.list_notes(course=курс, lesson="l-1")
			self.assertTrue(заметки["ok"], заметки)
			[заметка] = заметки["data"]["notes"]
			self.assertEqual(заметка["lesson_key"], "l-1")

			репорты = authoring.course_reports(course=курс, lesson="l-1")
			self.assertTrue(репорты["ok"], репорты)
			[репорт] = репорты["data"]["reports"]
			self.assertEqual(репорт["lesson_key"], "l-1")

			версии = authoring.course_releases(course=курс)
			self.assertTrue(версии["ok"], версии)
			история = версии["data"]["releases"]
			self.assertEqual([р["active"] for р in история], [True] + [False] * (len(история) - 1))
			self.assertTrue(all(р["digest"] for р in история))
			# Кабинет автора: «История» и «Заметки» курса с освобождёнными версиями.
			html = страница(self.куратор, course=курс, view="history")
			for р in история:
				self.assertIn(р["digest"][:12], html)
			html = страница(self.куратор, course=курс, view="notes")
			self.assertIn(f'id="note-card-{self.заметка}"', html)
		finally:
			frappe.set_user("Administrator")
