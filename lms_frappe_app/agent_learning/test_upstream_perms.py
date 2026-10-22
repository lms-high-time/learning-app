# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Регрессия на права доктайпов Frappe Learning после слияния upstream.

Контракт «`LMS Question` не читается ни одной ролью ученика ни при каких
условиях» (CLAUDE.md, §10) держится не кодом этого приложения, а списком
`permissions` внутри Frappe Learning — чужого приложения, которое ставится из
форка и периодически сливается с upstream. Коммит upstream, добавивший роль
`LMS Student` в права `LMS Question`, сольётся без конфликта и пройдёт зелёный
CI: ни одна прежняя проверка не смотрела на сырой `/api/resource/LMS Question`.
`api/test_no_leak.py` закрывает ответы методов контракта, но не дверь мимо них.

Отсюда форма проверок: не через методы контракта, а ровно теми вызовами,
которыми отвечает REST-слой Frappe за `/api/resource/<doctype>` и
`/api/method/frappe.desk.reportview.get`.

Тот же класс риска у `LMS Quiz Submission`: там право ученика есть, но только
`if_owner`. Потеря `if_owner` при слиянии открывает попытки чужих учеников —
без текста верного варианта, но с `is_correct` по каждому вопросу.

Вопросы, квизы и попытки Learning заводятся здесь же, напрямую: квиз
приложения идёт по релизу и записей Learning не пишет, а проверяются права
самих доктайпов Learning.
"""

import json

import frappe
import frappe.client
from frappe.desk import reportview
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.agent_learning import enrollment_guard
from lms_frappe_app.tests.sample_data import (
	зачислить,
	зачислить_на_курс,
	создать_куратора,
	создать_курс,
	создать_урок,
	создать_ученика,
)

РОЛЬ = "LMS Student"
ЭТАЛОН = "LMS Question"
ПОПЫТКА = "LMS Quiz Submission"
ЗАЧИСЛЕНИЕ = "LMS Enrollment"


def сигнал(доктайп: str, что: str) -> str:
	"""Сообщение о провале: куда смотреть, когда тест покраснел.

	Красный тест здесь почти никогда не значит «сломался код приложения»:
	`lms_frappe_app` этих прав не выдаёт и выдать не может. Сообщение обязано
	назвать доктайп и роль и отправить человека в чужое приложение, иначе
	первый час после слияния уйдёт на поиски в своём коде.
	"""
	return (
		f"{доктайп}: роль «{РОЛЬ}» {что}. "
		f"Права этого доктайпа живут в чужом приложении Frappe Learning "
		f"(apps/lms/lms/lms/doctype/…/*.json) и в `Custom DocPerm` сайта, а не в "
		f"коде lms_frappe_app. Тест покраснел сразу после слияния upstream "
		f"Learning — значит, слияние открыло права: сверь `permissions` доктайпа "
		f"{доктайп} с прежней версией и убери роль «{РОЛЬ}». "
		f"Основание контракта — CLAUDE.md, §10."
	)


class IntegrationTestUpstreamPerms(IntegrationTestCase):
	"""Ученик со всеми законными основаниями не достаёт эталоны напрямую.

	Зачисление активно, квиз его собственный, роль штатная — то есть проверки
	падают ровно тогда, когда права действительно открылись, а не потому, что
	пользователю чего-то не хватает.
	"""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)

		self.ученик = создать_ученика(f"up-{суффикс}@example.com")
		self.чужой = создать_ученика(f"up-other-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(self.ученик, self.урок)
		зачислить(self.чужой, self.урок)
		self.вопрос = frappe.get_doc(
			{
				"doctype": ЭТАЛОН,
				"question": "Столица России?",
				"type": "Choices",
				"option_1": "Москва",
				"is_correct_1": 1,
				"explanation_1": "Столицей она стала в пятнадцатом веке",
				"option_2": "Тула",
				"is_correct_2": 0,
			}
		).insert(ignore_permissions=True).name
		self.квиз = frappe.get_doc(
			{
				"doctype": "LMS Quiz",
				"title": f"Квиз {суффикс}",
				"lesson": self.урок,
				"course": self.курс,
				"total_marks": 1,
				"passing_percentage": 100,
				"questions": [{"question": self.вопрос, "marks": 1}],
			}
		).insert(ignore_permissions=True).name

	# --- вспомогательное ---

	def _report_view(self, доктайп: str):
		"""Вызов, которым отвечает `/api/method/frappe.desk.reportview.get`.

		Аргументы report view берёт из `form_dict`, а не из сигнатуры, поэтому
		подменяем его и возвращаем прежний: иначе следующий тест в процессе
		получит чужие параметры запроса.
		"""
		прежний = frappe.local.form_dict
		frappe.local.form_dict = frappe._dict(
			doctype=доктайп, fields=json.dumps(["name"]), limit_page_length=20
		)
		try:
			return reportview.get()
		finally:
			frappe.local.form_dict = прежний

	def _сдать_квиз(self, ученик: str) -> str:
		"""Заводит `LMS Quiz Submission` от имени ученика и возвращает её имя.

		Именно от его имени: право на записи `if_owner`, а владельца задаёт
		пользователь сессии в момент вставки. Запись, созданная администратором,
		проверяла бы не то.
		"""
		frappe.set_user(ученик)
		try:
			return frappe.get_doc(
				{
					"doctype": ПОПЫТКА,
					"quiz": self.квиз,
					"course": self.курс,
					"member": ученик,
					"score": 1,
					"score_out_of": 1,
					"percentage": 100,
					"passing_percentage": 100,
					"result": [
						{
							"question_name": self.вопрос,
							"question": "Столица России?",
							"answer": "Москва",
							"marks": 1,
							"marks_out_of": 1,
							"is_correct": 1,
						}
					],
				}
			).insert(ignore_permissions=True).name
		finally:
			frappe.set_user("Administrator")

	# --- эталоны ---

	def test_вопросы_ученику_не_отдаются(self):
		"""Ни права «read» — на доктайп и на конкретный вопрос, ни списка, ни
		вопроса своего квиза по имени, ни report view.

		Вопрос по имени берётся из квиза урока, на курс которого ученик
		зачислен: фильтр списка такую запись всё равно бы вернул, если бы право
		появилось, — значит, дыру надо ловить и на прямом обращении по имени.
		Report view — вторая дверь, и право у неё своё.
		"""
		frappe.set_user(self.ученик)

		with self.subTest("роль ученика не имеет права читать вопросы"):
			self.assertFalse(
				frappe.has_permission(ЭТАЛОН, "read"),
				сигнал(ЭТАЛОН, "получила право «read»"),
			)
			self.assertFalse(
				frappe.has_permission(ЭТАЛОН, "read", doc=self.вопрос),
				сигнал(ЭТАЛОН, "получила право «read» на конкретный вопрос"),
			)

		with self.subTest("список вопросов — `GET /api/resource/LMS Question`"):
			with self.assertRaises(
				frappe.PermissionError, msg=сигнал(ЭТАЛОН, "получила список вопросов")
			):
				frappe.client.get_list(ЭТАЛОН, fields=["name", "question"])

		with self.subTest("вопрос своего квиза по имени — `GET /api/resource/LMS Question/<name>`"):
			with self.assertRaises(
				frappe.PermissionError, msg=сигнал(ЭТАЛОН, "открыла вопрос своего квиза по имени")
			):
				frappe.client.get(ЭТАЛОН, self.вопрос)

		with self.subTest("вопросы через report view"):
			self.assertFalse(
				frappe.has_permission(ЭТАЛОН, "report"),
				сигнал(ЭТАЛОН, "получила право «report»"),
			)
			with self.assertRaises(
				frappe.PermissionError, msg=сигнал(ЭТАЛОН, "получила вопросы через report view")
			):
				self._report_view(ЭТАЛОН)

	# --- попытки чужих учеников ---

	def test_чужая_попытка_квиза_недоступна(self):
		"""У `LMS Quiz Submission` право ученика есть, но только на свои записи.

		Своя запись читаться обязана — на ней ученик видит результат в
		браузере; проверяется она здесь же, чтобы отличить закрытую чужую
		запись от доктайпа, закрытого целиком.
		"""
		своя = self._сдать_квиз(self.ученик)
		чужая = self._сдать_квиз(self.чужой)

		frappe.set_user(self.ученик)

		self.assertTrue(frappe.has_permission(ПОПЫТКА, "read", doc=своя))
		self.assertFalse(
			frappe.has_permission(ПОПЫТКА, "read", doc=чужая),
			сигнал(ПОПЫТКА, "получила право читать попытку чужого ученика"),
		)
		with self.assertRaises(
			frappe.PermissionError,
			msg=сигнал(ПОПЫТКА, "открыла попытку чужого ученика по имени"),
		):
			frappe.client.get(ПОПЫТКА, чужая)

		видимые = frappe.client.get_list(ПОПЫТКА, fields=["name"], limit_page_length=0)
		имена = {строка["name"] for строка in видимые}
		self.assertIn(своя, имена)
		self.assertNotIn(
			чужая, имена, сигнал(ПОПЫТКА, "увидела попытку чужого ученика в списке")
		)


class IntegrationTestEnrollmentWrites(IntegrationTestCase):
	"""Поля зачисления пишет код платформы (`agent_learning/enrollment_guard.py`).

	Проверки идут тем же путём, что `/api/resource/LMS Enrollment`: `PUT` —
	`_изменить` повторяет `frappe.api.v1.update_doc`, `POST` —
	`frappe.client.insert`; оба сохраняют документ без `ignore_permissions`.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		суффикс = frappe.generate_hash(length=6)
		cls.ученик = создать_ученика(f"enr-{суффикс}@example.com")
		cls.чужой = создать_ученика(f"enr-other-{суффикс}@example.com")
		cls.свой_курс = создать_курс(f"Свой курс {суффикс}")
		cls.другой_курс = создать_курс(f"Другой курс {суффикс}")
		for курс in (cls.свой_курс, cls.другой_курс):
			frappe.db.set_value("LMS Course", курс, "published", 1)
		cls.неопубликованный = создать_курс(f"Неопубликованный курс {суффикс}")
		зачислить_на_курс(cls.ученик, cls.свой_курс)
		cls.зачисление = frappe.db.get_value(ЗАЧИСЛЕНИЕ, {"member": cls.ученик, "course": cls.свой_курс})
		cls.группа = cls._группа(суффикс, cls.другой_курс)
		cls.оплата = cls._запись("LMS Payment", member=cls.ученик)
		cls.сертификат = cls._запись("LMS Certificate", member=cls.ученик, course=cls.свой_курс)

	@staticmethod
	def _запись(доктайп: str, **поля) -> str:
		"""Запись Learning, на которую ссылается поле: только строка, без проверок доктайпа."""
		запись = frappe.get_doc({"doctype": доктайп, "name": frappe.generate_hash(length=10), **поля})
		запись.db_insert()
		return запись.name

	@staticmethod
	def _группа(суффикс: str, курс: str) -> str:
		"""Группа Learning с самозаписью и одним курсом.

		Проверки группы (даты, преподаватели, расписание) к правилу не
		относятся, поэтому `validate` её пропускается.
		"""
		группа = frappe.get_doc(
			{
				"doctype": "LMS Batch",
				"title": f"Группа {суффикс}",
				"allow_self_enrollment": 1,
				"courses": [{"course": курс}],
			}
		)
		группа.flags.ignore_validate = True
		группа.flags.ignore_mandatory = True
		return группа.insert(ignore_permissions=True).name

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")

	@staticmethod
	def _изменить(имя: str, поля: dict) -> None:
		"""`PUT /api/resource/LMS Enrollment/<имя>`: то же, что `frappe.api.v1.update_doc`."""
		запись = frappe.get_doc(ЗАЧИСЛЕНИЕ, имя, for_update=True)
		запись.update(поля)
		запись.save()

	def test_своё_зачисление_ученик_не_меняет(self):
		"""Ни одно поле своей записи на курс ученику не меняется."""
		поля = {
			"course": self.неопубликованный,
			"member": self.чужой,
			"agent_tester": 1,
			"role": "Admin",
			"member_type": "Mentor",
			"enrollment_from_batch": self.группа,
			"payment": self.оплата,
			"certificate": self.сертификат,
			"owner": self.чужой,
			"creation": "2020-01-01 00:00:00",
		}
		frappe.set_user(self.ученик)
		for поле, значение in поля.items():
			with self.subTest(поле=поле):
				with self.assertRaises(frappe.PermissionError):
					self._изменить(self.зачисление, {поле: значение})

		frappe.set_user("Administrator")
		запись = frappe.db.get_value(ЗАЧИСЛЕНИЕ, self.зачисление, list(поля), as_dict=True)
		self.assertEqual(запись.course, self.свой_курс)
		self.assertEqual(запись.member, self.ученик)
		self.assertFalse(запись.agent_tester)
		self.assertFalse(запись.enrollment_from_batch)

	def test_зачисление_меняет_роль_с_полным_правом(self):
		"""Правило — для права только на свои записи: у `Moderator` оно полное."""
		суффикс = frappe.generate_hash(length=6)
		модератор = создать_куратора(f"enr-mod-{суффикс}@example.com", "Moderator")
		зачисление = frappe.db.get_value(
			ЗАЧИСЛЕНИЕ,
			{"member": self.чужой, "course": зачислить_на_курс(self.чужой, создать_курс(f"Курс {суффикс}"))},
		)
		frappe.set_user(модератор)
		frappe.client.set_value(ЗАЧИСЛЕНИЕ, зачисление, "agent_tester", 1)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(ЗАЧИСЛЕНИЕ, зачисление, "agent_tester"), 1)

	def test_запись_на_курс_только_методами(self):
		"""Вставка напрямую не заводит зачисление — ни на другого, ни на себя,
		ни по группе мимо записи в неё.

		Запись на курс — метод `enroll`: там пространство, анонс и порядок
		программы. Вставку по группе пропускает только запись в эту группу
		(`enrollment_guard.отметить_группу`) — и то без служебных полей.
		"""
		по_группе = {"member": self.ученик, "enrollment_from_batch": self.группа}
		заявки = {
			"на другого": ({"member": self.чужой}, False),
			"на себя": ({"member": self.ученик}, False),
			"на себя тестером": ({"member": self.ученик, "agent_tester": 1}, False),
			"на себя по группе мимо записи в неё": (по_группе, False),
			"на другого изнутри записи в группу": ({**по_группе, "member": self.чужой}, True),
			"по группе с оплатой": ({**по_группе, "payment": self.оплата}, True),
			"по группе наставником": ({**по_группе, "member_type": "Mentor"}, True),
		}
		frappe.set_user(self.ученик)
		for что, (поля, в_группе) in заявки.items():
			with self.subTest(что):
				if в_группе:
					enrollment_guard.отметить_группу(frappe._dict(batch=self.группа, member=self.ученик))
				try:
					with self.assertRaises(frappe.PermissionError):
						frappe.client.insert({"doctype": ЗАЧИСЛЕНИЕ, "course": self.другой_курс, **поля})
				finally:
					enrollment_guard.снять_отметку_группы(None)

		frappe.set_user("Administrator")
		self.assertFalse(
			frappe.db.exists(
				ЗАЧИСЛЕНИЕ, {"course": self.другой_курс, "member": ("in", [self.ученик, self.чужой])}
			)
		)

	def test_группа_learning_записывает_на_свои_курсы(self):
		"""Самозапись в группу Learning заводит зачисление на её курс.

		Learning вставляет его от имени ученика, без `ignore_permissions`, — это
		единственный такой путь, и правило его пропускает.
		"""
		from lms.lms.utils import enroll_in_batch

		суффикс = frappe.generate_hash(length=6)
		курс = создать_курс(f"Курс группы {суффикс}")
		frappe.db.set_value("LMS Course", курс, "published", 1)
		группа = self._группа(суффикс, курс)

		frappe.set_user(self.ученик)
		enroll_in_batch(группа)

		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value(ЗАЧИСЛЕНИЕ, {"member": self.ученик, "course": курс}, "enrollment_from_batch"),
			группа,
		)


class TestТолькоСвоиЗаписи(UnitTestCase):
	"""Без базы: кому право на зачисление дано только на свои записи
	(`enrollment_guard.только_свои`)."""

	def test_только_свои(self):
		def строка(роль, *, if_owner=0, permlevel=0, **права):
			return frappe._dict(role=роль, if_owner=if_owner, permlevel=permlevel, **права)

		ученик = строка("LMS Student", write=1, create=1, if_owner=1)
		модератор = строка("Moderator", write=1, create=1)
		случаи = {
			"только роль ученика": ([ученик], {"LMS Student"}, True),
			"ученик и модератор": ([ученик, модератор], {"LMS Student", "Moderator"}, False),
			"Custom DocPerm сайта даёт право без if_owner": (
				[ученик, строка("Organization Manager", write=1)],
				{"LMS Student", "Organization Manager"},
				False,
			),
			"строка чужой роли не в счёт": ([ученик, модератор], {"LMS Student"}, True),
			"строка уровня 1 не в счёт": (
				[ученик, строка("LMS Student", write=1, permlevel=1)],
				{"LMS Student"},
				True,
			),
			"права нет вовсе": ([модератор], {"LMS Student"}, False),
		}
		for что, (строки, роли, ожидание) in случаи.items():
			with self.subTest(что):
				self.assertEqual(enrollment_guard.только_свои(строки, роли, "write"), ожидание)
