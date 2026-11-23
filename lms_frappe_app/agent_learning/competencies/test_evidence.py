# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Свидетельства об освоении компетенций и пересмотр уровня (learning-services#808)."""

import json

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.agent_learning.competencies import evidence
from lms_frappe_app.agent_learning.errors import Отказ

ТЕКСТЫ = {
	"task": "Объяснить выбор",
	"student_action": "Назвал свойства задачи",
	"assessment": "Своими словами",
}
ОТМЕТКИ = {"hints": False, "corrected": False, "indirect": False}


def _код(вызов) -> tuple[str, dict]:
	try:
		вызов()
	except Отказ as отказ:
		return отказ.код, отказ.подробности
	raise AssertionError("отказа не было")


class TestФормаСвидетельства(UnitTestCase):
	def test_допустимые_свидетельства(self):
		for случай, аргументы, итог_уровень in (
			("подтверждено, свободный ответ", ("confirmed", 2, ТЕКСТЫ, "free", ОТМЕТКИ), 2),
			("уровень строкой", ("confirmed", "3", ТЕКСТЫ, "work", ОТМЕТКИ), 3),
			("выбор — уровень 1", ("confirmed", 1, ТЕКСТЫ, "choice", ОТМЕТКИ), 1),
			("подсказки — уровень 2", ("confirmed", 2, ТЕКСТЫ, "free", {**ОТМЕТКИ, "hints": True}), 2),
			(
				"уровень 4 с переносом",
				(
					"confirmed",
					4,
					{**ТЕКСТЫ, "transfer_difference": "Другой процесс", "transfer_adaptation": "Добавил шаг"},
					"work",
					ОТМЕТКИ,
				),
				4,
			),
			# Ограничения отметок — только у «подтверждено»: затруднение пишется с тем уровнем, где пытались.
			(
				"затруднение на 3 с подсказкой",
				("difficulty", 3, ТЕКСТЫ, "choice", {**ОТМЕТКИ, "hints": True}),
				3,
			),
			("недостаточно данных без уровня", ("insufficient", None, ТЕКСТЫ, None, {}), None),
			("пропуск", ("skipped", None, {"task": "Предложил задачу"}, None, {}), None),
		):
			with self.subTest(случай):
				уровень, тексты, _, отметки = evidence.форма_свидетельства(*аргументы)
				self.assertEqual(уровень, итог_уровень)
				# У «недостаточно» и пропуска отметок нет — не переданы.
				self.assertIn(отметки["indirect"], (False, None))

	def test_отказы_формы(self):
		for случай, аргументы, код, подробность in (
			(
				"исход вне списка",
				("passed", 2, ТЕКСТЫ, "free", ОТМЕТКИ),
				"outcome_unknown",
				("outcome", "passed"),
			),
			("уровень 5", ("confirmed", 5, ТЕКСТЫ, "free", ОТМЕТКИ), "level_invalid", ("level", 5)),
			(
				"уровень дробный",
				("confirmed", "2.0", ТЕКСТЫ, "free", ОТМЕТКИ),
				"level_invalid",
				("level", "2.0"),
			),
			("уровень булев", ("confirmed", True, ТЕКСТЫ, "free", ОТМЕТКИ), "level_invalid", ("level", True)),
			(
				"форма вне списка",
				("confirmed", 2, ТЕКСТЫ, "quiz", ОТМЕТКИ),
				"task_form_unknown",
				("task_form", "quiz"),
			),
			(
				"нет уровня",
				("confirmed", None, ТЕКСТЫ, "free", ОТМЕТКИ),
				"evidence_field_required",
				("field", "level"),
			),
			(
				"нет действия ученика",
				("difficulty", 2, {**ТЕКСТЫ, "student_action": "  "}, "free", ОТМЕТКИ),
				"evidence_field_required",
				("field", "student_action"),
			),
			# Отметка «да/нет» обязательна явно: молчание о подсказке обходило бы потолок.
			(
				"не сказано о подсказках",
				("confirmed", 2, ТЕКСТЫ, "free", {"corrected": False}),
				"evidence_field_required",
				("field", "hints"),
			),
			(
				"у пропуска уровень",
				("skipped", 1, ТЕКСТЫ, None, {}),
				"evidence_field_not_allowed",
				("field", "level"),
			),
			(
				"выбор выше 1",
				("confirmed", 2, ТЕКСТЫ, "choice", ОТМЕТКИ),
				"level_capped",
				("reason", "task_form"),
			),
			(
				"подсказки выше 2",
				("confirmed", 3, ТЕКСТЫ, "free", {**ОТМЕТКИ, "hints": "true"}),
				"level_capped",
				("reason", "hints"),
			),
			(
				"исправлял выше 2",
				("confirmed", 3, ТЕКСТЫ, "work", {**ОТМЕТКИ, "corrected": 1}),
				"level_capped",
				("reason", "corrected"),
			),
			(
				"уровень 4 без переноса",
				("confirmed", 4, ТЕКСТЫ, "work", ОТМЕТКИ),
				"evidence_field_required",
				("field", "transfer_difference"),
			),
			(
				"длинный текст",
				("confirmed", 2, {**ТЕКСТЫ, "task": "з" * 1001}, "free", ОТМЕТКИ),
				"evidence_too_long",
				("field", "task"),
			),
		):
			with self.subTest(случай):
				код_, подробности = _код(lambda: evidence.форма_свидетельства(*аргументы))
				self.assertEqual(код_, код)
				self.assertEqual(подробности[подробность[0]], подробность[1])


class TestКлючЗапроса(UnitTestCase):
	def test_ключ_запроса(self):
		self.assertEqual(evidence._ключ_запроса("  k-1 "), "k-1")
		# Скобка — нет: поле хранится очищенным от HTML, и повтор не нашёл бы ключ.
		for ключ in (None, " ", "к" * 81, "a<b", "a>b"):
			with self.subTest(ключ=ключ):
				self.assertEqual(_код(lambda: evidence._ключ_запроса(ключ))[0], "request_key_invalid")


class _Запись(dict):
	__getattr__ = dict.get


class TestСостояние(UnitTestCase):
	def запись(self, имя, вид, компетенция, исход=None, уровень=0, выведено=None):
		return _Запись(
			name=имя,
			kind=вид,
			competency=компетенция,
			outcome=исход,
			level=уровень,
			withdrawn=json.dumps(выведено) if выведено is not None else None,
		)

	def test_наивысший_действующий_и_последний_исход(self):
		записи = [
			self.запись("e1", "Evidence", "K1", "confirmed", 3),
			self.запись("e2", "Evidence", "K1", "difficulty", 2),
			self.запись("e3", "Evidence", "K2", "skipped"),
		]
		self.assertEqual(
			evidence.состояние_по_записям(записи),
			{
				# Затруднение уровень не снижает — только пересмотр.
				"K1": {"level": 3, "last_outcome": "difficulty", "last_level": 2},
				"K2": {"level": None, "last_outcome": "skipped", "last_level": None},
			},
		)

	def test_пересмотр_выводит_только_перечисленные(self):
		записи = [
			self.запись("e1", "Evidence", "K1", "confirmed", 1),
			self.запись("e2", "Evidence", "K1", "confirmed", 3),
			self.запись("r1", "Revision", "K1", уровень=1, выведено=["e2"]),
			# Подтверждение после пересмотра им не затронуто.
			self.запись("e3", "Evidence", "K1", "confirmed", 2),
		]
		self.assertEqual(evidence.состояние_по_записям(записи)["K1"]["level"], 2)
		self.assertEqual(evidence.состояние_по_записям(записи[:3])["K1"]["level"], 1)

	def test_пересмотр_до_нуля(self):
		записи = [
			self.запись("e1", "Evidence", "K1", "confirmed", 2),
			self.запись("r1", "Revision", "K1", уровень=0, выведено=["e1"]),
		]
		self.assertEqual(
			evidence.состояние_по_записям(записи)["K1"],
			{"level": None, "last_outcome": "confirmed", "last_level": 2},
		)


class IntegrationTestСвидетельства(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from lms_frappe_app.tests.release_sample import пример_релиза, с_каталогом_компетенций
		from lms_frappe_app.tests.sample_data import курс_из_релиза

		frappe.set_user("Administrator")
		cls.курс, cls.релиз = курс_из_релиза(
			релиз=с_каталогом_компетенций(пример_релиза(f"evidence-{frappe.generate_hash(length=6)}"))
		)

	def setUp(self):
		from lms_frappe_app.tests.sample_data import занятие_релиза, зачислить_на_курс, создать_ученика

		self.addCleanup(frappe.set_user, "Administrator")
		self.ученик = создать_ученика(f"evidence-{frappe.generate_hash(length=6)}@example.com")
		зачислить_на_курс(self.ученик, self.курс)
		self.занятие = frappe.get_doc("Agent Learning Session", занятие_релиза(self.ученик, self.курс, "l-1"))

	def записать(self, ключ: str, компетенция: str = "base-1", исход: str = "confirmed", уровень=2, **ещё):
		return evidence.записать(
			self.занятие,
			self.релиз,
			"l-1",
			компетенция,
			исход,
			уровень,
			{**ТЕКСТЫ, **ещё.pop("тексты", {})},
			ещё.pop("форма", "free"),
			{**ОТМЕТКИ, **ещё},
			ключ,
		)

	def test_запись_повтор_и_компетенция_другого_урока(self):
		первое = self.записать("k-1")
		self.assertEqual(
			(первое["kind"], первое["competency"], первое["duplicate"], первое["state"]),
			("evidence", "base-1", False, {"level": 2, "last_outcome": "confirmed", "last_level": 2}),
		)
		запись = frappe.get_doc(evidence.ЖУРНАЛ, первое["event"])
		self.assertEqual(
			(
				запись.student,
				запись.course,
				запись.lesson_key,
				запись.session,
				запись.release,
				запись.competency_version,
			),
			# Версия компетенции каталога на момент записи (learning-services#826).
			(
				self.ученик,
				self.курс,
				"l-1",
				self.занятие.name,
				self.релиз,
				frappe.db.get_value("Agent Competency", "base-1", "version"),
			),
		)
		# Повтор ключа — та же запись, даже с другими полями.
		повтор = self.записать("k-1", уровень=1)
		self.assertEqual((повтор["event"], повтор["duplicate"]), (первое["event"], True))
		# Косвенное свидетельство по компетенции другого урока (whole-1 — цель l-3).
		self.assertEqual(
			self.записать("k-2", компетенция="whole-1", уровень=3, indirect=True)["state"]["level"], 3
		)
		self.assertEqual(frappe.db.count(evidence.ЖУРНАЛ, {"student": self.ученик}), 2)

	def test_компетенция_не_из_карты_ничего_не_пишет(self):
		self.assertEqual(
			_код(lambda: self.записать("x-1", компетенция="no-such-skill"))[0], "competency_unknown"
		)
		self.assertEqual(frappe.db.count(evidence.ЖУРНАЛ, {"student": self.ученик}), 0)

	def test_запись_журнала_только_компетенция_каталога_с_версией(self):
		"""Запись журнала — компетенция каталога и её версия (learning-services#839): мимо сервиса
		база не принимает ни ключ не из каталога, ни запись без версии."""
		for случай, компетенция, версия, отказ in (
			("ключ не из каталога", "no-such-skill", 1, frappe.LinkValidationError),
			("без версии", "base-1", 0, frappe.ValidationError),
		):
			with self.subTest(случай), self.assertRaises(отказ) as ошибка:
				frappe.get_doc(
					{
						"doctype": evidence.ЖУРНАЛ,
						"kind": evidence.СВИДЕТЕЛЬСТВО,
						"student": self.ученик,
						"course": self.курс,
						"competency": компетенция,
						"competency_version": версия,
						"outcome": evidence.ПРОПУЩЕНО,
						"request_key": f"link-{версия}",
					}
				).insert(ignore_permissions=True)
			if версия == 0:
				self.assertIn("нет версии компетенции каталога", str(ошибка.exception))
		self.assertEqual(frappe.db.count(evidence.ЖУРНАЛ, {"student": self.ученик}), 0)

	def test_пересмотр_только_вниз(self):
		self.записать("p-1", уровень=1)
		второе = self.записать("p-2", уровень=3, форма="work")
		self.assertEqual(
			_код(lambda: evidence.пересмотреть(self.занятие, self.релиз, "l-1", "base-1", 3, "Нет", "p-r0")),
			("revision_not_lower", {"level": 3, "current_level": 3}),
		)
		итог = evidence.пересмотреть(
			self.занятие, self.релиз, "l-1", "base-1", 1, "Затруднение повторилось", "p-r1"
		)
		self.assertEqual((итог["kind"], итог["state"]["level"]), ("revision", 1))
		self.assertEqual(
			json.loads(frappe.db.get_value(evidence.ЖУРНАЛ, итог["event"], "withdrawn")), [второе["event"]]
		)
		# Без уровня пересматривать нечего.
		self.assertEqual(
			_код(lambda: evidence.пересмотреть(self.занятие, self.релиз, "l-1", "base-2", 0, "Нет", "p-r2"))[
				0
			],
			"revision_not_lower",
		)

	def test_журнал_не_правится(self):
		запись = frappe.get_doc(evidence.ЖУРНАЛ, self.записать("j-1")["event"])
		запись.level = 4
		with self.assertRaises(frappe.ValidationError):
			запись.save(ignore_permissions=True)

	def test_сброс_прогресса_архивирует_журнал(self):
		from lms_frappe_app.agent_learning.reset import сбросить_прогресс

		self.записать("r-1")
		итог = сбросить_прогресс(self.ученик, self.курс, "Administrator")
		self.assertEqual(итог["competency_events_archived"], 1)
		self.assertEqual(evidence._записи(self.ученик, self.курс), [])
		self.assertEqual(frappe.db.count(evidence.ЖУРНАЛ, {"archived_student": self.ученик}), 1)
