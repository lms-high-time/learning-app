# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Заметки автора и репорты пишут только методы (learning-services#528).

Course Creator читает обе записи, а пишет только методами авторинга. Строку
нити заметки не вставить, не править и не удалить саму по себе. Moderator и
System Manager правят записи и в Desk, но у репорта не меняются поля привязки,
а статус и там идёт по переходам. Пути — те, которыми ходят Desk и REST:
`frappe.client` и `/api/resource` (API v1).
"""

from unittest.mock import patch

import frappe
from frappe.api import v1 as rest
from frappe.client import delete as удалить_из_desk
from frappe.client import insert as вставить_из_desk
from frappe.client import save as сохранить_из_desk
from frappe.client import set_value
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import notes
from lms_frappe_app.agent_learning.doctype.agent_course_report.agent_course_report import ПОЛЯ_ПРИВЯЗКИ
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases.test_course_guard import права_из_файла
from lms_frappe_app.api import authoring, student
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	зачислить_на_курс,
	курс_из_релиза,
	создать_занятие,
	создать_куратора,
	создать_курс,
	создать_ученика,
	урок_релиза,
)

ЗАМЕТКА = "Agent Author Note"
ОТВЕТ = "Agent Note Reply"
РЕПОРТ = "Agent Course Report"
#: Текст отказа на правку поля привязки репорта.
ЖАЛОБА = "ставит жалоба из занятия"


def через_rest(метод, doctype: str, /, *имя: str, **поля):
	"""Вызов `/api/resource` без HTTP: тело запроса API v1 берёт из `form_dict.data`."""
	with patch.object(frappe.local, "form_dict", frappe._dict(data=frappe.as_json(поля))):
		return метод(doctype, *имя)


def сохранить(doctype: str, имя: str, **поля):
	"""`frappe.client.save` — так сохраняет форма Desk: документ целиком с правкой."""
	return сохранить_из_desk(frappe.as_json({**frappe.get_doc(doctype, имя).as_dict(), **поля}))


class IntegrationTestЗаметкиМимоМетодов(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		права_из_файла(self, ЗАМЕТКА)
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"notes-guard-{суффикс}@example.com")
		self.модератор = создать_куратора(f"notes-guard-mod-{суффикс}@example.com", "Moderator")
		frappe.set_user(self.куратор)
		публикация = authoring.publish_release(release=пример_релиза(f"notes-guard-{суффикс}"))
		self.assertTrue(публикация["ok"], публикация)
		self.курс = публикация["data"]["course"]
		ответ = authoring.add_note(course=self.курс, target="lesson.l-1", text="Слишком длинно")
		self.assertTrue(ответ["ok"], ответ)
		self.заметка = ответ["data"]["id"]
		frappe.set_user("Administrator")

	def новая(self) -> dict:
		return {"doctype": ЗАМЕТКА, "course": self.курс, "target": "course", "text": "Своя"}

	def состояние(self) -> tuple:
		заметка = frappe.get_doc(ЗАМЕТКА, self.заметка)
		return заметка.status, заметка.via, заметка.release, [(о.via, о.text) for о in заметка.replies]

	def строка(self) -> dict:
		"""Строка нити, как её шлёт `/api/resource`: с родителем и полем родителя."""
		return {
			"parent": self.заметка,
			"parenttype": ЗАМЕТКА,
			"parentfield": "replies",
			"via": "agent",
			"text": "Сделал",
		}

	def статус(self, status: str, **поля) -> dict:
		return authoring.set_note_status(note=self.заметка, status=status, **поля)

	def test_методы_пишут_от_куратора(self):
		"""Права на запись у Course Creator нет, а весь цикл заметки ему доступен."""
		self.assertFalse(frappe.has_permission(ЗАМЕТКА, "write", user=self.куратор))
		frappe.set_user(self.куратор)

		self.assertTrue(authoring.reply_note(note=self.заметка, text="Уточню: шаг 3")["ok"])
		self.assertEqual(self.статус("done", text="Сократил", via="agent")["data"]["status"], "done")
		self.assertEqual(self.статус("open", text="Всё ещё длинно")["data"]["status"], "open")
		self.assertEqual(self.статус("accepted")["data"]["status"], "accepted")
		в_обход = self.статус("done", text="Ещё", via="agent")
		self.assertEqual(в_обход["error"]["code"], "invalid_transition")
		self.assertEqual(len(authoring.list_notes(course=self.курс)["data"]["notes"][0]["replies"]), 3)

	def test_куратор_мимо_метода_не_пишет(self):
		"""Ни статус, ни `via`, ни релиз, ни нить: `frappe.client` и `/api/resource`
		отклоняются правами — и вставка строки нити тоже."""
		self.assertTrue(frappe.has_permission(ЗАМЕТКА, "read", user=self.куратор))
		было = self.состояние()
		frappe.set_user(self.куратор)
		новая = {**self.новая(), "status": "accepted"}
		пути = {
			"save: статус": lambda: сохранить(ЗАМЕТКА, self.заметка, status="accepted"),
			"save: релиз": lambda: сохранить(ЗАМЕТКА, self.заметка, release=None),
			"set_value: via": lambda: set_value(ЗАМЕТКА, self.заметка, "via", "agent"),
			"insert": lambda: вставить_из_desk(новая),
			"delete": lambda: удалить_из_desk(ЗАМЕТКА, self.заметка),
			"rest: создать": lambda: через_rest(rest.create_doc, ЗАМЕТКА, **новая),
			"rest: править": lambda: через_rest(rest.update_doc, ЗАМЕТКА, self.заметка, status="done"),
			"rest: удалить": lambda: через_rest(rest.delete_doc, ЗАМЕТКА, self.заметка),
			"insert: строка нити": lambda: вставить_из_desk({"doctype": ОТВЕТ, **self.строка()}),
			"rest: строка нити": lambda: через_rest(rest.create_doc, ОТВЕТ, **self.строка()),
		}
		for что, путь in пути.items():
			with self.subTest(что), self.assertRaises(frappe.PermissionError):
				путь()

		frappe.set_user("Administrator")
		self.assertEqual(self.состояние(), было)
		self.assertFalse(frappe.db.exists(ЗАМЕТКА, {"course": self.курс, "target": "course"}))

	def test_строку_нити_не_вставить_не_править_и_не_удалить_саму_по_себе(self):
		"""У Moderator и администратора право на заметку есть — строку держит её
		собственный `validate` и `on_trash`."""
		self.assertTrue(authoring.reply_note(note=self.заметка, text="Уточню: шаг 3")["ok"])
		строка = frappe.get_doc(ЗАМЕТКА, self.заметка).replies[0].name
		было = self.состояние()
		for кто in (self.модератор, "Administrator"):
			frappe.set_user(кто)
			пути = {
				"rest: вставить": lambda: через_rest(rest.create_doc, ОТВЕТ, **self.строка()),
				"rest: править": lambda: через_rest(rest.update_doc, ОТВЕТ, строка, via="agent"),
				"rest: удалить": lambda: через_rest(rest.delete_doc, ОТВЕТ, строка),
				"delete_doc": lambda: frappe.delete_doc(ОТВЕТ, строка),
			}
			for что, путь in пути.items():
				with (
					self.subTest(кто=кто, что=что),
					self.assertRaisesRegex(frappe.ValidationError, "нить заметки"),
				):
					путь()

		frappe.set_user("Administrator")
		self.assertEqual(self.состояние(), было)

	def test_модератор_в_desk_идёт_по_переходам(self):
		"""«Сделано» отмечает агент с текстом, возвращает автор с текстом — и в Desk."""
		frappe.set_user(self.модератор)
		for что, путь in {
			"сделано без агента": lambda: сохранить(ЗАМЕТКА, self.заметка, status="done"),
			"новая сделанной": lambda: вставить_из_desk({**self.новая(), "status": "done"}),
		}.items():
			with self.subTest(что), self.assertRaises(Отказ) as пойман:
				путь()
			self.assertEqual(пойман.exception.код, notes.НЕДОПУСТИМЫЙ_ПЕРЕХОД)

		set_value(ЗАМЕТКА, self.заметка, "status", "accepted")
		for что, путь in {
			"из принятой в сделано": lambda: set_value(ЗАМЕТКА, self.заметка, "status", "done"),
			"возврат без текста": lambda: сохранить(ЗАМЕТКА, self.заметка, status="open"),
		}.items():
			with self.subTest(что), self.assertRaises(Отказ) as пойман:
				путь()
			self.assertEqual(пойман.exception.код, notes.НЕДОПУСТИМЫЙ_ПЕРЕХОД)
		заметка = frappe.get_doc(ЗАМЕТКА, self.заметка).as_dict()
		заметка["replies"].append({"doctype": ОТВЕТ, "via": "author", "text": "Не то"})
		сохранить_из_desk(frappe.as_json({**заметка, "status": "open"}))

		frappe.set_user("Administrator")
		self.assertEqual(self.состояние()[0], "open")
		self.assertEqual(self.состояние()[3], [("author", "Не то")])


class IntegrationTestРепортМимоМетода(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		права_из_файла(self, РЕПОРТ)
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"rep-guard-{суффикс}@example.com")
		self.модератор = создать_куратора(f"rep-guard-mod-{суффикс}@example.com", "Moderator")
		self.ученик = создать_ученика(f"rep-guard-s-{суффикс}@example.com")
		self.сосед = создать_ученика(f"rep-guard-n-{суффикс}@example.com")
		self.курс, _ = курс_из_релиза()
		self.урок = урок_релиза(self.курс, "l-1")
		for ученик in (self.ученик, self.сосед):
			зачислить_на_курс(ученик, self.курс)
		занятие = создать_занятие(self.ученик, self.урок)
		self.чужое_занятие = создать_занятие(self.сосед, self.урок)
		frappe.set_user(self.ученик)
		ответ = student.report_issue(session=занятие, kind="material_issue", text="Пример неверный")
		self.репорт = ответ["data"]["report"]
		frappe.set_user("Administrator")
		self.привязка = self.привязка_репорта()

	def привязка_репорта(self) -> dict:
		return frappe.db.get_value(РЕПОРТ, self.репорт, list(ПОЛЯ_ПРИВЯЗКИ), as_dict=True)

	def разобрать(self, status: str, **поля) -> dict:
		return authoring.resolve_report(report=self.репорт, status=status, **поля)

	def репорты_соседа(self) -> list[dict]:
		frappe.set_user(self.сосед)
		репорты = student.my_reports()["data"]["reports"]
		frappe.set_user("Administrator")
		return репорты

	def test_разбор_методом_от_куратора(self):
		self.assertFalse(frappe.has_permission(РЕПОРТ, "write", user=self.куратор))
		frappe.set_user(self.куратор)

		self.assertEqual(self.разобрать("in_progress")["data"]["status"], "in_progress")
		self.assertEqual(self.разобрать("fixed", resolution="Поправили пример")["data"]["status"], "fixed")
		в_обход = self.разобрать("rejected", resolution="Передумали")
		self.assertEqual(в_обход["error"]["code"], authoring.НЕДОПУСТИМЫЙ_ПЕРЕХОД_РЕПОРТА)

	def test_куратор_мимо_метода_не_пишет(self):
		"""Репорт не переставить на занятие другого ученика: тот увидел бы в
		`my_reports` чужой текст и ответ."""
		self.assertTrue(frappe.has_permission(РЕПОРТ, "read", user=self.куратор))
		frappe.set_user(self.куратор)
		новый = {**self.привязка, "doctype": РЕПОРТ, "text": "Подделка"}
		чужое = self.чужое_занятие
		пути = {
			"save: занятие": lambda: сохранить(РЕПОРТ, self.репорт, session=чужое),
			"save: статус": lambda: сохранить(РЕПОРТ, self.репорт, status="Fixed", resolution="Сам"),
			"set_value: текст": lambda: set_value(РЕПОРТ, self.репорт, "text", "Подделка"),
			"insert": lambda: вставить_из_desk(новый),
			"delete": lambda: удалить_из_desk(РЕПОРТ, self.репорт),
			"rest: создать": lambda: через_rest(rest.create_doc, РЕПОРТ, **новый),
			"rest: занятие": lambda: через_rest(rest.update_doc, РЕПОРТ, self.репорт, session=чужое),
			"rest: удалить": lambda: через_rest(rest.delete_doc, РЕПОРТ, self.репорт),
		}
		for что, путь in пути.items():
			with self.subTest(что), self.assertRaises(frappe.PermissionError):
				путь()

		frappe.set_user("Administrator")
		self.assertEqual(self.привязка_репорта(), self.привязка)
		self.assertEqual(frappe.db.get_value(РЕПОРТ, self.репорт, "status"), "New")
		self.assertEqual(frappe.db.count(РЕПОРТ, {"course": self.курс}), 1)
		self.assertEqual(self.репорты_соседа(), [])

	def test_модератор_в_desk_не_меняет_привязку(self):
		подмены = {
			"session": self.чужое_занятие,
			"course": создать_курс(f"Другой {frappe.generate_hash(length=6)}"),
			"lesson": урок_релиза(self.курс, "l-2"),
			"release": None,
			"kind": "Stuck",
			"question_key": "S1/l-1-D1",
			"objective": "Чужая цель",
			"text": "Подделка",
		}
		self.assertEqual(set(подмены), set(ПОЛЯ_ПРИВЯЗКИ))
		for кто in (self.модератор, "Administrator"):
			frappe.set_user(кто)
			for поле, значение in подмены.items():
				with self.subTest(кто=кто, поле=поле), self.assertRaisesRegex(frappe.ValidationError, ЖАЛОБА):
					сохранить(РЕПОРТ, self.репорт, **{поле: значение})
			with (
				self.subTest(кто=кто, поле="rest: session"),
				self.assertRaisesRegex(frappe.ValidationError, ЖАЛОБА),
			):
				через_rest(rest.update_doc, РЕПОРТ, self.репорт, session=self.чужое_занятие)

		frappe.set_user("Administrator")
		self.assertEqual(self.привязка_репорта(), self.привязка)
		self.assertEqual(self.репорты_соседа(), [])

	def test_модератор_в_desk_разбирает_по_переходам(self):
		"""Статус и ответ в Desk правятся, а закрытый репорт только переоткрывают."""
		frappe.set_user(self.модератор)
		сохранить(РЕПОРТ, self.репорт, status="Fixed", resolution="Поправили пример")
		with self.assertRaisesRegex(frappe.ValidationError, "перейти нельзя"):
			set_value(РЕПОРТ, self.репорт, "status", "Rejected")
		set_value(РЕПОРТ, self.репорт, "status", "In Progress")
		set_value(РЕПОРТ, self.репорт, "status", "Rejected")

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(РЕПОРТ, self.репорт, "status"), "Rejected")
