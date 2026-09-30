# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Письма домашки: напоминание, «вернули», дайджест куратору (learning-services#452)."""

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning import homework as домашка
from lms_frappe_app.agent_learning import homework_notices as письма
from lms_frappe_app.agent_learning import notices
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

#: «Сейчас» запуска: утро, после часа дайджеста.
УТРО = now_datetime().replace(hour=10, minute=0, second=0, microsecond=0)


class IntegrationTestHomeworkNotices(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.отправка = patch("frappe.sendmail").start()
		self.почта = patch.object(notices, "почта_есть", return_value=True).start()
		self.сейчас = patch.object(письма, "now_datetime", return_value=УТРО).start()
		self.addCleanup(patch.stopall)
		self.addCleanup(frappe.db.set_single_value, "Agent Learning Settings", "deadline_reminder_days", 3)
		frappe.db.set_single_value("Agent Learning Settings", "deadline_reminder_days", 3)
		с = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"hn-{с}@example.com")
		self.урок = создать_урок(f"Урок {с}")
		self.курс = зачислить(self.ученик, self.урок)
		создать_домашку(self.урок)
		self.организация = создать_организацию(f"Орг {с}")
		добавить_в_организацию(self.ученик, self.организация)
		self.менеджер = создать_менеджера(f"hnm-{с}@example.com", self.организация)

	# --- помощники ---

	def выдана(self, организация=None, *, выдана_за: timedelta, срок_через: timedelta) -> str:
		"""Выданная сдача: выдача и срок — относительно «сейчас» запуска."""
		имя = создать_занятие(self.ученик, self.урок)
		frappe.db.set_value("Agent Learning Session", имя, "organization", организация)
		домашка.выдать(frappe.get_doc("Agent Learning Session", имя))
		сдача = домашка.найти_сдачу(домашка.задание_урока(self.урок).name, self.ученик, организация)
		frappe.db.set_value(
			домашка.СДАЧА, сдача, {"assigned_at": УТРО - выдана_за, "due_at": УТРО + срок_через}
		)
		return сдача

	def журнал(self, **фильтры) -> list[str]:
		return frappe.get_all("Homework Notice", filters=фильтры, pluck="key")

	def письма_к(self, кому: str) -> list[dict]:
		return [
			вызов.kwargs for вызов in self.отправка.call_args_list if вызов.kwargs["recipients"] == [кому]
		]

	# --- напоминание ---

	def test_напоминание_однократно(self):
		сдача = self.выдана(self.организация, выдана_за=timedelta(days=5), срок_через=timedelta(days=2))
		письма.разослать()
		письма.разослать()
		[ключ] = self.журнал(submission=сдача, kind="reminder")
		self.assertTrue(ключ.startswith(f"{сдача}:reminder:"))
		[письмо] = self.письма_к(self.ученик)
		self.assertIn(f"/lms/courses/{self.курс}/learn/", письмо["message"])

	def test_далёкий_срок_не_напоминает(self):
		self.выдана(выдана_за=timedelta(days=1), срок_через=timedelta(days=10))
		письма.разослать()
		self.assertEqual(self.письма_к(self.ученик), [])

	def test_короткий_срок_в_середине_промежутка(self):
		"""От выдачи до срока меньше порога в три дня — напоминание в середине
		промежутка, а не сразу после выдачи."""
		сдача = self.выдана(выдана_за=timedelta(hours=12), срок_через=timedelta(days=1))
		письма.разослать()
		self.assertEqual(self.журнал(submission=сдача), [], "середина — через шесть часов")
		frappe.db.set_value(домашка.СДАЧА, сдача, "assigned_at", УТРО - timedelta(days=1))
		письма.разослать()
		self.assertEqual(len(self.журнал(submission=сдача)), 1)

	def test_сдача_до_выдачи_считается_от_сохранения(self):
		домашка.сохранить(self.ученик, self.урок, None, answer="рано")
		сдача = домашка.найти_сдачу(домашка.задание_урока(self.урок).name, self.ученик, None)
		frappe.db.set_value(
			домашка.СДАЧА,
			сдача,
			{
				"status": "Returned",
				"submitted_at": УТРО - timedelta(days=5),
				"due_at": УТРО + timedelta(days=1),
			},
		)
		письма.разослать()
		self.assertEqual(len(self.журнал(submission=сдача, kind="reminder")), 1)

	def test_ноль_дней_не_напоминать(self):
		frappe.db.set_single_value("Agent Learning Settings", "deadline_reminder_days", 0)
		self.выдана(выдана_за=timedelta(days=5), срок_через=timedelta(hours=2))
		письма.разослать()
		self.assertEqual(self.письма_к(self.ученик), [])

	def test_сданную_и_просроченную_не_напоминать(self):
		сдача = self.выдана(выдана_за=timedelta(days=5), срок_через=timedelta(days=1))
		frappe.db.set_value(домашка.СДАЧА, сдача, "status", "Submitted")
		письма.разослать()
		frappe.db.set_value(домашка.СДАЧА, сдача, {"status": "Assigned", "due_at": УТРО - timedelta(hours=1)})
		письма.разослать()
		self.assertEqual(self.письма_к(self.ученик), [])

	def test_без_почты_ни_писем_ни_журнала(self):
		сдача = self.выдана(выдана_за=timedelta(days=5), срок_через=timedelta(days=1))
		self.почта.return_value = False
		письма.разослать()
		self.assertEqual((self.отправка.call_count, self.журнал(submission=сдача)), (0, []))
		self.почта.return_value = True
		письма.разослать()
		self.assertEqual(len(self.журнал(submission=сдача)), 1)

	def test_отключённому_не_пишем(self):
		self.выдана(выдана_за=timedelta(days=5), срок_через=timedelta(days=1))
		frappe.db.set_value("User", self.ученик, "enabled", 0)
		письма.разослать()
		self.assertEqual(self.письма_к(self.ученик), [])

	def test_без_доступа_к_курсу_не_пишем(self):
		self.выдана(выдана_за=timedelta(days=5), срок_через=timedelta(days=1))
		frappe.db.delete("LMS Enrollment", {"member": self.ученик, "course": self.курс})
		письма.разослать()
		self.assertEqual(self.письма_к(self.ученик), [])

	def test_ушедшему_из_организации_не_пишем(self):
		self.выдана(self.организация, выдана_за=timedelta(days=5), срок_через=timedelta(days=1))
		frappe.db.set_value(
			"Organization Membership",
			{"user": self.ученик, "organization": self.организация},
			"status",
			"Left",
		)
		письма.разослать()
		self.assertEqual(self.письма_к(self.ученик), [])

	# --- «вернули» ---

	def вернуть(self, действие="send_back", комментарий="Добавьте итог встречи") -> str:
		документ = домашка.сохранить(self.ученик, self.урок, self.организация, answer="сделал")
		if действие == "reopen":
			домашка.проверить(self.менеджер, документ.name, "accept", документ.version)
		домашка.проверить(self.менеджер, документ.name, действие, документ.version, комментарий)
		return документ.name

	def test_вернули_по_строке_журнала_однократно(self):
		сдача = self.вернуть()
		письма.разослать()
		письма.разослать()
		строка = frappe.get_doc(домашка.СДАЧА, сдача).history[-1].name
		self.assertEqual(self.журнал(submission=сдача, kind="returned"), [f"{строка}:returned"])
		[письмо] = self.письма_к(self.ученик)
		self.assertIn("Добавьте итог встречи", письмо["message"])
		self.assertIn("#homework", письмо["message"])

	def test_отмена_приёма_тоже_пишет(self):
		сдача = self.вернуть("reopen", "Принял по ошибке")
		письма.разослать()
		self.assertEqual(len(self.журнал(submission=сдача, kind="returned")), 1)
		self.assertIn("Принял по ошибке", self.письма_к(self.ученик)[0]["message"])

	def test_пересдал_до_рассылки_не_пишем(self):
		сдача = self.вернуть()
		домашка.сохранить(self.ученик, self.урок, self.организация, answer="доделал")
		письма.разослать()
		self.assertEqual(self.журнал(submission=сдача, kind="returned"), [])

	def test_старый_возврат_не_пишем(self):
		сдача = self.вернуть()
		строка = frappe.get_doc(домашка.СДАЧА, сдача).history[-1].name
		frappe.db.set_value("Agent Homework Event", строка, "at", УТРО - timedelta(days=8))
		письма.разослать()
		self.assertEqual(self.журнал(submission=сдача, kind="returned"), [])

	# --- дайджест ---

	def сдать(self, организация=None, ученик=None):
		return домашка.сохранить(ученик or self.ученик, self.урок, организация, answer="сделал").name

	def дайджест(self, кому: str) -> list[str]:
		return self.журнал(recipient=кому, kind="digest")

	def test_дайджест_руководителю_раз_в_день_после_часа(self):
		self.сдать(self.организация)
		self.сдать(None)
		self.сейчас.return_value = УТРО.replace(hour=8)
		письма.разослать()
		self.assertEqual(self.дайджест(self.менеджер), [])
		self.сейчас.return_value = УТРО
		письма.разослать()
		письма.разослать()
		self.assertEqual(self.дайджест(self.менеджер), [f"{self.менеджер}:digest:{УТРО:%Y%m%d}"])
		[письмо] = self.письма_к(self.менеджер)
		# Личная сдача руководителю не видна: в счёте одна.
		self.assertIn("1 домашка", письмо["subject"])
		self.assertIn("/lms/homework?tab=queue", письмо["message"])

	def test_дайджест_методисту_по_своим_курсам_без_своих_сдач(self):
		методист = создать_куратора(f"hnc-{frappe.generate_hash(length=6)}@example.com")
		чужой = создать_куратора(f"hnc2-{frappe.generate_hash(length=6)}@example.com")
		курс = frappe.get_doc("LMS Course", self.курс)
		курс.append("instructors", {"instructor": методист})
		курс.save(ignore_permissions=True)
		self.сдать(None)
		зачислить(методист, self.урок)
		self.сдать(None, ученик=методист)
		письма.разослать()
		[письмо] = self.письма_к(методист)
		self.assertIn("1 домашка", письмо["subject"])
		self.assertEqual(self.дайджест(чужой), [])

	def test_дайджест_модератору(self):
		модератор = создать_куратора(f"hnmod-{frappe.generate_hash(length=6)}@example.com", роль="Moderator")
		self.сдать(None)
		письма.разослать()
		self.assertEqual(len(self.дайджест(модератор)), 1)

	def test_пустая_очередь_без_письма(self):
		письма.разослать()
		self.assertEqual(self.дайджест(self.менеджер), [])

	def test_отключённому_руководителю_без_дайджеста(self):
		self.сдать(self.организация)
		frappe.db.set_value("User", self.менеджер, "enabled", 0)
		письма.разослать()
		self.assertEqual(self.дайджест(self.менеджер), [])

	def test_сбой_одного_письма_не_валит_рассылку(self):
		self.сдать(self.организация)
		сдача = self.выдана(выдана_за=timedelta(days=5), срок_через=timedelta(days=1))

		def отправить(**письмо):
			if письмо["recipients"] == [self.ученик]:
				raise frappe.OutgoingEmailError("SMTP недоступен")

		self.отправка.side_effect = отправить
		with patch.object(frappe, "log_error") as лог:
			письма.разослать()
		лог.assert_called()
		self.assertEqual(self.журнал(submission=сдача), [], "журнал откатан вместе с письмом")
		self.assertEqual(len(self.дайджест(self.менеджер)), 1)

	def test_повтор_ключа_при_вставке_пропускается(self):
		self.сдать(self.организация)
		frappe.get_doc(
			{
				"doctype": "Homework Notice",
				"key": f"{self.менеджер}:digest:{УТРО:%Y%m%d}",
				"kind": "digest",
				"recipient": self.менеджер,
			}
		).insert(ignore_permissions=True)
		# Уже записанный ключ «не виден» выборке — как у параллельного запуска.
		with patch.object(письма, "_записанные", return_value=set()):
			письма.разослать()
		self.assertEqual(self.письма_к(self.менеджер), [])
