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
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import зачислить, создать_ученика, создать_урок

РОЛЬ = "LMS Student"
ЭТАЛОН = "LMS Question"
ПОПЫТКА = "LMS Quiz Submission"


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

	def test_роль_ученика_не_имеет_права_читать_вопросы(self):
		frappe.set_user(self.ученик)

		self.assertFalse(
			frappe.has_permission(ЭТАЛОН, "read"),
			сигнал(ЭТАЛОН, "получила право «read»"),
		)
		self.assertFalse(
			frappe.has_permission(ЭТАЛОН, "read", doc=self.вопрос),
			сигнал(ЭТАЛОН, "получила право «read» на конкретный вопрос"),
		)

	def test_список_вопросов_не_отдаётся(self):
		"""То, что делает `GET /api/resource/LMS Question`."""
		frappe.set_user(self.ученик)

		with self.assertRaises(
			frappe.PermissionError, msg=сигнал(ЭТАЛОН, "получила список вопросов")
		):
			frappe.client.get_list(ЭТАЛОН, fields=["name", "question"])

	def test_вопрос_своего_квиза_не_открывается_по_имени(self):
		"""То, что делает `GET /api/resource/LMS Question/<name>`.

		Вопрос берётся из квиза урока, на курс которого ученик зачислен:
		фильтр списка такую запись всё равно бы вернул, если бы право
		появилось, — значит, дыру надо ловить и на прямом обращении по имени.
		"""
		frappe.set_user(self.ученик)

		with self.assertRaises(
			frappe.PermissionError, msg=сигнал(ЭТАЛОН, "открыла вопрос своего квиза по имени")
		):
			frappe.client.get(ЭТАЛОН, self.вопрос)

	def test_вопросы_не_отдаются_через_report(self):
		"""Report view — вторая дверь, и право у неё своё."""
		frappe.set_user(self.ученик)

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
