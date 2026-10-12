# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import json
import os
from datetime import timedelta

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_datetime

from lms_frappe_app.agent_learning import homework as домашка
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	создать_домашку,
	создать_занятие,
	создать_организацию,
	создать_ученика,
	создать_урок,
)


def занятие(ученик, урок):
	"""`создать_занятие` отдаёт имя, а `выдать` ждёт документ."""
	return frappe.get_doc("Agent Learning Session", создать_занятие(ученик, урок))


def не_найти_с_первого_раза():
	"""Первый поиск сдачи отвечает «нет», хотя она есть, — как у вызова,
	который проиграл гонку параллельному (learning-services#439)."""
	from unittest.mock import patch

	настоящий = домашка.найти_сдачу
	вызовы = []

	def найти(*args, **kwargs):
		вызовы.append(args)
		return None if len(вызовы) == 1 else настоящий(*args, **kwargs)

	return patch.object(домашка, "найти_сдачу", side_effect=найти)


class IntegrationTestHomeworkIssue(IntegrationTestCase):
	"""Выдача домашки при закрытии урока (learning-services#439)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.заново()

	def заново(self) -> None:
		"""Свои ученик и урок — у теста или у строки таблицы."""
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"hw-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(self.ученик, self.урок)

	def сдачи(self):
		return frappe.get_all(
			"Agent Homework Submission",
			filters={"member": self.ученик},
			fields=["name", "status", "organization", "due_at", "assigned_at"],
		)

	def test_урок_без_задания_ничего_не_выдаёт(self):
		домашка.выдать(занятие(self.ученик, self.урок))
		self.assertEqual(self.сдачи(), [])

	def test_выдача_ставит_срок(self):
		"""Относительный — от закрытия; абсолютный — конец дня. У каждого случая
		свои ученик и урок."""
		случаи = {
			"относительный срок от закрытия": (
				{"due_mode": "relative", "due_days": 5},
				timedelta(days=5),
				None,
			),
			"абсолютный срок — конец дня": (
				{"due_mode": "absolute", "due_date": "2030-01-15"},
				None,
				"2030-01-15 23:59:59",
			),
		}
		for номер, (что, (правило, от_выдачи, срок)) in enumerate(случаи.items()):
			with self.subTest(что):
				if номер:
					self.заново()
				создать_домашку(self.урок, **правило)

				домашка.выдать(занятие(self.ученик, self.урок))

				[сдача] = self.сдачи()
				if от_выдачи is not None:
					self.assertEqual(сдача.status, "Assigned")
					self.assertEqual(get_datetime(сдача.due_at) - get_datetime(сдача.assigned_at), от_выдачи)
				if срок is not None:
					self.assertEqual(str(сдача.due_at), срок)

	def test_повторное_закрытие_ничего_не_меняет(self):
		создать_домашку(self.урок, due_mode="relative", due_days=5)
		запись = занятие(self.ученик, self.урок)
		домашка.выдать(запись)
		[было] = self.сдачи()
		домашка.выдать(запись)
		[стало] = self.сдачи()
		self.assertEqual(было, стало)
		self.assertEqual(len(frappe.get_doc("Agent Homework Submission", стало.name).history), 1)

	def test_правка_правила_не_пересчитывает_выставленный_срок(self):
		"""Ученик не получает просрочку задним числом за правку правила."""
		правило = создать_домашку(self.урок, due_mode="relative", due_days=5)
		запись = занятие(self.ученик, self.урок)
		домашка.выдать(запись)
		[было] = self.сдачи()
		правило.update({"due_mode": "absolute", "due_date": "2020-01-01"})
		правило.save(ignore_permissions=True)
		домашка.выдать(запись)
		домашка.сохранить(self.ученик, self.урок, None, answer="сделал")
		[стало] = self.сдачи()
		self.assertEqual(стало.due_at, было.due_at)

	def test_закрытие_в_другом_пространстве_даёт_свою_сдачу(self):
		создать_домашку(self.урок)
		домашка.выдать(занятие(self.ученик, self.урок))
		организация = создать_организацию(f"Орг {frappe.generate_hash(length=4)}")
		добавить_в_организацию(self.ученик, организация)
		имя = создать_занятие(self.ученик, self.урок)
		frappe.db.set_value("Agent Learning Session", имя, "organization", организация)
		домашка.выдать(frappe.get_doc("Agent Learning Session", имя))
		self.assertEqual({с.organization for с in self.сдачи()}, {None, организация})

	def test_выдача_в_гонке_берёт_уже_вставленную_сдачу(self):
		"""Закрытие урока не падает, если сдачу уже вставил параллельный вызов."""
		создать_домашку(self.урок, due_mode="relative", due_days=3)
		домашка.сохранить(self.ученик, self.урок, None, answer="раньше закрытия")
		with не_найти_с_первого_раза():
			домашка.выдать(занятие(self.ученик, self.урок))
		[сдача] = self.сдачи()
		self.assertEqual(сдача.status, "Submitted")
		self.assertIsNotNone(сдача.assigned_at)
		self.assertIsNotNone(сдача.due_at)

	def test_complete_lesson_выдаёт_домашку(self):
		from lms_frappe_app.api import student
		from lms_frappe_app.tests.sample_data import (
			занятие_релиза,
			зачислить_на_курс,
			курс_из_релиза,
			настроить_квиз,
			отметить_все_пункты,
			политика_по_умолчанию,
		)

		self.addCleanup(политика_по_умолчанию)
		настроить_квиз(quiz_required=0)
		курс, _ = курс_из_релиза()
		зачислить_на_курс(self.ученик, курс)
		занятие = занятие_релиза(self.ученик, курс, "l-3")
		отметить_все_пункты(frappe.db.get_value("Agent Learning Session", занятие, "run"))
		frappe.set_user(self.ученик)
		ответ = student.complete_lesson(занятие)
		self.assertTrue(ответ["ok"], ответ)
		frappe.set_user("Administrator")
		[сдача] = self.сдачи()
		self.assertEqual(сдача.status, "Assigned")

	def test_закрытие_урока_и_очередь_выдачи(self):
		"""Выдача — не в транзакции закрытия: её сбой не срывает зачёт; задача
		ставится после коммита. Урок без задания и задание, снятое из релиза,
		задачу не ставят. У каждого случая свои ученик и урок."""
		from unittest.mock import patch

		from lms_frappe_app.agent_learning import quiz

		случаи = {
			"задание есть — выдача в очереди после коммита": ({}, True),
			"урок без задания не ставит задачу": (None, False),
			"снятое из релиза задание не ставит задачу": ({"retired": 1}, False),
		}
		for номер, (что, (задание, ставит)) in enumerate(случаи.items()):
			with self.subTest(что):
				if номер:
					self.заново()
				if задание is not None:
					создать_домашку(self.урок, **задание)
				запись = занятие(self.ученик, self.урок)

				with patch.object(frappe, "enqueue") as очередь:
					quiz.отметить_урок_пройденным(запись)

				if not ставит:
					очередь.assert_not_called()
					continue
				очередь.assert_called_once()
				self.assertEqual(
					очередь.call_args.args, ("lms_frappe_app.agent_learning.homework.выдать_по_записи",)
				)
				self.assertEqual(
					{к: очередь.call_args.kwargs[к] for к in ("enqueue_after_commit", "doctype", "name")},
					{"enqueue_after_commit": True, "doctype": "Agent Learning Session", "name": запись.name},
				)

	def test_сбой_очереди_не_срывает_закрытие_урока(self):
		from unittest.mock import patch

		from lms_frappe_app.agent_learning import quiz

		создать_домашку(self.урок)
		with (
			patch.object(frappe, "enqueue", side_effect=RuntimeError("очередь переполнена")),
			patch.object(frappe, "log_error") as журнал,
		):
			quiz.отметить_урок_пройденным(занятие(self.ученик, self.урок))
		журнал.assert_called_once()
		self.assertEqual(
			frappe.db.get_value("LMS Course Progress", {"member": self.ученик, "lesson": self.урок}, "status"),
			"Complete",
		)

	def test_фоновая_выдача_при_взаимоблокировке(self):
		"""Повторяет после взаимоблокировки; без успеха за три попытки — пишет в лог."""
		from unittest.mock import patch

		имя = создать_занятие(self.ученик, self.урок)
		случаи = {
			"повторяет после взаимоблокировки": (
				[frappe.QueryDeadlockError("1020"), None],
				{"выдать": 2, "откат": 1, "лог": 0},
			),
			"без успеха пишет в лог": (frappe.QueryDeadlockError("1020"), {"выдать": 3, "лог": 1}),
		}
		for что, (исход, вызовов) in случаи.items():
			with self.subTest(что):
				with (
					patch.object(домашка, "выдать", side_effect=исход) as выдать,
					patch.object(frappe.db, "rollback") as откат,
					patch.object(frappe, "log_error") as лог,
				):
					домашка.выдать_по_записи("Agent Learning Session", имя)

				счёт = {"выдать": выдать.call_count, "откат": откат.call_count, "лог": лог.call_count}
				self.assertEqual({к: счёт[к] for к in вызовов}, вызовов)

	def test_попытка_квиза_выдаёт_в_пространстве_своего_занятия(self):
		создать_домашку(self.урок)
		организация = создать_организацию(f"Орг {frappe.generate_hash(length=4)}")
		добавить_в_организацию(self.ученик, организация)
		имя = создать_занятие(self.ученик, self.урок)
		frappe.db.set_value("Agent Learning Session", имя, "organization", организация)
		попытка = frappe._dict(
			doctype="Agent Quiz Attempt", session=имя, lesson=self.урок, student=self.ученик, course=self.курс
		)
		домашка.выдать(попытка)
		self.assertEqual([с.organization for с in self.сдачи()], [организация])

	def test_задание_проверяет_правило_срока(self):
		"""Отказ задания не заводит: случаи идут по очереди на одном уроке, как и шли.

		Срок — одной строкой проводки: правило целиком проверяет
		`doctype/agent_lesson_homework/test_agent_lesson_homework.py`."""
		from lms_frappe_app.agent_learning.errors import Отказ

		случаи = [
			("относительный без дней", {"due_mode": "relative"}, "invalid_due"),
			("ответ голосом", {"answer_mode": "audio"}, "invalid_answer_mode"),
		]
		for что, правило, код in случаи:
			with self.subTest(что):
				with self.assertRaises(Отказ) as отказ:
					создать_домашку(self.урок, **правило)
				self.assertEqual(отказ.exception.код, код)


class IntegrationTestHomeworkSave(IntegrationTestCase):
	"""Сохранение ответа: версия на каждое сохранение, журнал, файлы (learning-services#439)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.заново()

	def заново(self) -> None:
		"""Свои ученик и урок — у теста или у строки таблицы."""
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"hws-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(self.ученик, self.урок)

	def test_каждое_сохранение_новая_версия(self):
		создать_домашку(self.урок)
		домашка.сохранить(self.ученик, self.урок, None, answer="первая")
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="вторая")
		self.assertEqual(документ.status, "Submitted")
		self.assertEqual(документ.version, 2)
		self.assertEqual([в.answer for в in документ.versions], ["первая", "вторая"])
		self.assertEqual([с.event for с in документ.history], ["submitted", "submitted"])

	def test_сохранение_в_гонке_пишет_в_уже_вставленную_сдачу(self):
		создать_домашку(self.урок)
		первая = домашка.сохранить(self.ученик, self.урок, None, answer="первая", новые=[("a.txt", b"one")])
		with не_найти_с_первого_раза():
			вторая = домашка.сохранить(self.ученик, self.урок, None, answer="вторая")
		self.assertEqual(вторая.name, первая.name)
		self.assertEqual((вторая.version, вторая.answer), (2, "вторая"))
		self.assertEqual([с.file for с in вторая.files], [с.file for с in первая.files], "файлы прежней сдачи на месте")
		self.assertEqual(frappe.db.count("Agent Homework Submission", {"member": self.ученик}), 1)

	def test_отказы_сохранения(self):
		"""Без задания; пустой ответ; текст в задание только файлами и файл — в
		задание только текстом; больше десяти файлов. Пустой файл — отказ до
		записи: не пропадает молча из ответа, ученик думал бы, что сдал его. У
		каждого случая свои ученик и урок."""
		случаи = {
			"без задания": (None, {"answer": "сделал"}, "no_homework", {}, False),
			"пустой ответ": ({}, {"answer": "   "}, "empty_answer", {}, False),
			"текст в задание только файлами": (
				{"answer_mode": "files"},
				{"answer": "текст"},
				"answer_mode",
				{},
				False,
			),
			"файл в задание только текстом": (
				{"answer_mode": "text"},
				{"новые": [("a.txt", b"one")]},
				"answer_mode",
				{},
				False,
			),
			"предел числа файлов": (
				{},
				{"новые": [(f"{н}.txt", b"x") for н in range(11)]},
				"too_many_files",
				{},
				False,
			),
			"пустой файл": (
				{},
				{"answer": "текст", "новые": [("a.txt", b"")]},
				"file_missing",
				{"file": "a.txt"},
				True,
			),
		}
		for номер, (что, (задание, ответ, код, подробности, сдачи_нет)) in enumerate(случаи.items()):
			with self.subTest(что):
				if номер:
					self.заново()
				if задание is not None:
					создать_домашку(self.урок, **задание)

				with self.assertRaises(домашка.Отказ) as отказ:
					домашка.сохранить(self.ученик, self.урок, None, **ответ)

				self.assertEqual(отказ.exception.код, код)
				self.assertEqual({к: отказ.exception.подробности[к] for к in подробности}, подробности)
				if сдачи_нет:
					self.assertFalse(frappe.db.exists("Agent Homework Submission", {"member": self.ученик}))

	def test_принятую_не_правят(self):
		"""И в гонке: сохранение, проигравшее гонку, принятую не правит. У каждого
		случая свои ученик и урок."""
		from contextlib import nullcontext

		for номер, (что, гонка) in enumerate(
			(("без гонки", nullcontext), ("в гонке", не_найти_с_первого_раза))
		):
			with self.subTest(что):
				if номер:
					self.заново()
				создать_домашку(self.урок)
				первая = домашка.сохранить(self.ученик, self.урок, None, answer="первая")
				frappe.db.set_value("Agent Homework Submission", первая.name, "status", "Accepted")

				with гонка(), self.assertRaises(домашка.Отказ) as отказ:
					домашка.сохранить(self.ученик, self.урок, None, answer="вторая")

				self.assertEqual(отказ.exception.код, "accepted_locked")

	def test_файлы_сохраняются_и_остаются_в_старой_версии(self):
		создать_домашку(self.урок)
		первая = домашка.сохранить(self.ученик, self.урок, None, новые=[("a.txt", b"one")])
		[файл] = [с.file for с in первая.files]
		вторая = домашка.сохранить(self.ученик, self.урок, None, answer="без файла", убрать=[файл])
		self.assertEqual(list(вторая.files), [])
		self.assertEqual(json.loads(вторая.versions[0].files), [файл])
		self.assertTrue(frappe.db.exists("File", файл))
		self.assertEqual(
			frappe.db.get_value("File", файл, ["attached_to_doctype", "is_private"]),
			("Agent Homework Submission", 1),
		)

	def _отклонить_второй_файл(self, ошибка: Exception | None = None):
		"""Вставка файла, которая принимает первый файл и отклоняет второй — как
		Frappe отклоняет запрещённый тип. Принятые файлы — в `self.принятые`."""
		from unittest.mock import patch

		настоящая = домашка._вложить_файл
		self.принятые = []

		def вложить(*args):
			if self.принятые:
				raise ошибка or frappe.ValidationError("Тип файла не разрешён")
			файл = настоящая(*args)
			self.принятые.append(файл)
			return файл

		return patch.object(домашка, "_вложить_файл", side_effect=вложить)

	@staticmethod
	def _на_диске(файл) -> bool:
		return os.path.exists(frappe.get_site_path("private", "files", файл.file_url.rsplit("/", 1)[-1]))

	def test_отклонённый_файл_убирает_с_диска_байты_принятых(self):
		"""Откат к точке сохранения не зовёт `after_rollback`: байты, записанные
		`File.before_insert`, остались бы на диске без записи о них."""
		создать_домашку(self.урок)
		with self._отклонить_второй_файл(), self.assertRaises(домашка.Отказ):
			домашка.сохранить(
				self.ученик, self.урок, None, новые=[("a.txt", frappe.generate_hash().encode()), ("b.exe", b"two")]
			)
		[принятый] = self.принятые
		self.assertFalse(self._на_диске(принятый))

	def test_откат_не_удаляет_общий_файл_другой_записи(self):
		"""Frappe хранит одинаковое содержимое одним файлом на диске: откат своей
		записи не должен удалять байты, на которые ссылается чужая."""
		создать_домашку(self.урок)
		содержимое = frappe.generate_hash().encode()
		первая = домашка.сохранить(self.ученик, self.урок, None, новые=[("a.txt", содержимое)])
		прежний = frappe.get_doc("File", первая.files[0].file)
		with self._отклонить_второй_файл(), self.assertRaises(домашка.Отказ):
			домашка.сохранить(self.ученик, self.урок, None, новые=[("copy.txt", содержимое), ("b.exe", b"two")])
		self.assertTrue(self._на_диске(прежний))

	def test_наш_отказ_при_вложении_не_становится_file_rejected(self):
		создать_домашку(self.урок)
		with self._отклонить_второй_файл(домашка.Отказ("свой_код", "Свой отказ")), self.assertRaises(
			домашка.Отказ
		) as отказ:
			домашка.сохранить(
				self.ученик, self.урок, None, новые=[("a.txt", frappe.generate_hash().encode()), ("b.txt", b"two")]
			)
		self.assertEqual(отказ.exception.код, "свой_код")
		self.assertFalse(frappe.db.exists("Agent Homework Submission", {"member": self.ученик}))
		self.assertFalse(self._на_диске(self.принятые[0]))

	def test_предел_размера_frappe_даёт_file_too_large(self):
		"""Предел Frappe (`max_file_size`) бывает меньше нашего — отказ тем же кодом."""
		from unittest.mock import patch

		создать_домашку(self.урок)
		with patch("frappe.core.api.file.get_max_file_size", return_value=3), self.assertRaises(
			домашка.Отказ
		) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, новые=[("a.txt", b"hello")])
		self.assertEqual(отказ.exception.код, "file_too_large")
		self.assertEqual(отказ.exception.подробности["file"], "a.txt")
		self.assertFalse(frappe.db.exists("Agent Homework Submission", {"member": self.ученик}))

	def test_отклонённый_файл_не_оставляет_новой_сдачи(self):
		создать_домашку(self.урок)
		файлов = frappe.db.count("File", {"attached_to_doctype": "Agent Homework Submission"})
		with self._отклонить_второй_файл(), self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, новые=[("a.txt", b"one"), ("b.exe", b"two")])
		self.assertEqual(отказ.exception.код, "file_rejected")
		self.assertEqual(отказ.exception.подробности["file"], "b.exe")
		self.assertFalse(frappe.db.exists("Agent Homework Submission", {"member": self.ученик}))
		self.assertEqual(frappe.db.count("File", {"attached_to_doctype": "Agent Homework Submission"}), файлов)

	def test_отклонённый_файл_не_меняет_существующую_сдачу(self):
		создать_домашку(self.урок)
		было = домашка.сохранить(self.ученик, self.урок, None, answer="первая", новые=[("a.txt", b"one")])
		файлы_было = set(frappe.get_all("File", filters={"attached_to_name": было.name}, pluck="name"))
		with self._отклонить_второй_файл(), self.assertRaises(домашка.Отказ):
			домашка.сохранить(self.ученик, self.урок, None, answer="вторая", новые=[("b.txt", b"two"), ("c.exe", b"x")])
		стало = frappe.get_doc("Agent Homework Submission", было.name)
		self.assertEqual((стало.version, стало.answer, len(стало.versions)), (1, "первая", 1))
		self.assertEqual([с.file for с in стало.files], [с.file for с in было.files])
		self.assertEqual(set(frappe.get_all("File", filters={"attached_to_name": было.name}, pluck="name")), файлы_было)

	def test_почта_в_журнале_только_у_событий_читателя(self):
		создать_домашку(self.урок)
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="сделал")
		[своё] = домашка.описание_сдачи(документ, читатель=self.ученик)["history"]
		[чужое] = домашка.описание_сдачи(документ, читатель="Administrator")["history"]
		self.assertEqual((своё["by"], чужое["by"]), (self.ученик, None))

	def test_версии_читают_файлы_одним_запросом(self):
		from unittest.mock import patch

		создать_домашку(self.урок)
		for номер in range(3):
			документ = домашка.сохранить(self.ученик, self.урок, None, новые=[(f"{номер}.txt", f"v{номер}".encode())])
		with patch.object(
			домашка.файлы_платформы, "сведения_о_файлах", wraps=домашка.файлы_платформы.сведения_о_файлах
		) as сведения:
			описание = домашка.описание_сдачи(документ, с_версиями=True)
		self.assertEqual(сведения.call_count, 1)
		self.assertEqual([[ф["name"] for ф in в["files"]] for в in описание["versions"]], [
			["0.txt"], ["0.txt", "1.txt"], ["0.txt", "1.txt", "2.txt"]
		])
		self.assertEqual([ф["name"] for ф in описание["files"]], ["0.txt", "1.txt", "2.txt"])

	def test_предел_объёма_сохранения(self):
		создать_домашку(self.урок)
		# Размер файла пропускает оба, сумма — нет: отказ именно за объём сохранения.
		было = frappe.db.get_single_value("Agent Learning Settings", "artifact_file_max_mb")
		frappe.db.set_single_value("Agent Learning Settings", "artifact_file_max_mb", 20)
		frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")
		self.addCleanup(frappe.clear_document_cache, "Agent Learning Settings", "Agent Learning Settings")
		self.addCleanup(frappe.db.set_single_value, "Agent Learning Settings", "artifact_file_max_mb", было)
		большой = b"x" * (16 * 1024 * 1024)
		with self.assertRaises(домашка.Отказ) as отказ:
			домашка.сохранить(self.ученик, self.урок, None, новые=[("a.bin", большой), ("b.bin", большой)])
		self.assertEqual(отказ.exception.код, "answer_too_large")

	def test_абсолютный_срок_ставится_при_создании(self):
		создать_домашку(self.урок, due_mode="absolute", due_date="2030-01-15")
		документ = домашка.сохранить(self.ученик, self.урок, None, answer="рано")
		self.assertEqual(str(документ.due_at), "2030-01-15 23:59:59")
		self.assertIsNone(документ.assigned_at)


class IntegrationTestHomeworkAllocationDue(IntegrationTestCase):
	"""Срок домашки из назначения курса перекрывает срок автора (learning-services#452)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.заново()

	def заново(self) -> None:
		"""Свои ученик, урок с заданием и организация — у теста или у строки таблицы."""
		с = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"hwa-{с}@example.com")
		self.урок = создать_урок(f"Урок {с}")
		self.курс = зачислить(self.ученик, self.урок)
		self.задание = создать_домашку(self.урок, due_mode="relative", due_days=5)
		self.организация = создать_организацию(f"Орг {с}")
		добавить_в_организацию(self.ученик, self.организация)

	def назначить(self, *сроки, организация=None, **поля):
		return frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": организация or self.организация,
				"course": self.курс,
				"homework_due": [{"homework": self.задание.name, **срок} for срок in сроки],
				**поля,
			}
		).insert(ignore_permissions=True)

	def выдать(self, организация):
		имя = создать_занятие(self.ученик, self.урок)
		frappe.db.set_value("Agent Learning Session", имя, "organization", организация)
		домашка.выдать(frappe.get_doc("Agent Learning Session", имя))
		имя = домашка.найти_сдачу(self.задание.name, self.ученик, организация)
		return frappe.get_doc(домашка.СДАЧА, имя)

	def дней_до_срока(self, сдача) -> timedelta:
		return get_datetime(сдача.due_at) - get_datetime(сдача.assigned_at)

	def test_срок_выдачи_из_назначения(self):
		"""Правило назначения перекрывает автора; из двух назначений — ближайший
		срок; назначение другой организации, личная сдача и назначение не адресату
		оставляют срок автора; выбранный самим курс тоже действует. У каждого
		случая свои ученик, урок и организация."""

		def другая_организация():
			другая = создать_организацию(f"Другая {frappe.generate_hash(length=6)}")
			добавить_в_организацию(self.ученик, другая)
			self.назначить({"due_mode": "relative", "due_days": 1}, организация=другая)

		def не_адресату():
			коллега = создать_ученика(f"hwa-c-{frappe.generate_hash(length=6)}@example.com")
			добавить_в_организацию(коллега, self.организация)
			self.назначить(
				{"due_mode": "relative", "due_days": 1},
				audience="Selected Members",
				members=[{"user": коллега}],
			)

		случаи = {
			"правило назначения перекрывает автора": (
				lambda: self.назначить({"due_mode": "relative", "due_days": 2}),
				True,
				{"дней": timedelta(days=2), "источник": "allocation"},
			),
			"два назначения — ближайший срок": (
				lambda: (
					self.назначить({"due_mode": "relative", "due_days": 4}),
					self.назначить(
						{"due_mode": "absolute", "due_date": "2020-01-01"},
						audience="Selected Members",
						members=[{"user": self.ученик}],
					),
				),
				True,
				{"срок": "2020-01-01 23:59:59"},
			),
			"назначение другой организации не действует": (
				другая_организация,
				True,
				{"дней": timedelta(days=5), "источник": "author"},
			),
			"личная сдача — только автор": (
				lambda: self.назначить({"due_mode": "relative", "due_days": 1}),
				False,
				{"дней": timedelta(days=5)},
			),
			"выбранный самим курс тоже действует": (
				lambda: self.назначить(
					{"due_mode": "relative", "due_days": 3},
					audience="Selected Members",
					members=[{"user": self.ученик}],
					chosen_by_member=1,
				),
				True,
				{"дней": timedelta(days=3)},
			),
			"назначение не адресату не действует": (не_адресату, True, {"дней": timedelta(days=5)}),
		}
		for номер, (что, (назначить, в_организации, ожидаемо)) in enumerate(случаи.items()):
			with self.subTest(что):
				if номер:
					self.заново()
				назначить()

				сдача = self.выдать(self.организация if в_организации else None)

				итог = {
					"дней": self.дней_до_срока(сдача),
					"срок": str(сдача.due_at),
					"источник": сдача.history[-1].due_source,
				}
				self.assertEqual({к: итог[к] for к in ожидаемо}, ожидаемо)

	def test_правка_назначения_не_меняет_выставленный_срок(self):
		назначение = self.назначить({"due_mode": "relative", "due_days": 2})
		было = self.выдать(self.организация).due_at
		назначение.set("homework_due", [{"homework": self.задание.name, "due_mode": "absolute", "due_date": "2020-01-01"}])
		назначение.save(ignore_permissions=True)
		домашка.сохранить(self.ученик, self.урок, self.организация, answer="сделал")
		стало = frappe.db.get_value(домашка.СДАЧА, {"member": self.ученик}, "due_at")
		self.assertEqual(стало, было)

	def test_сохранение_до_выдачи_ставит_абсолютный_срок_назначения(self):
		self.назначить({"due_mode": "absolute", "due_date": "2030-03-01"})
		документ = домашка.сохранить(self.ученик, self.урок, self.организация, answer="рано")
		self.assertEqual(str(документ.due_at), "2030-03-01 23:59:59")

	def test_сохранение_до_выдачи_при_относительном_правиле_без_срока(self):
		"""Правило назначения относительное — срок выставит выдача, хотя автор
		и задал бы абсолютный: действуют правила назначения, а не автора."""
		self.задание.update({"due_mode": "absolute", "due_date": "2030-01-15"})
		self.задание.save(ignore_permissions=True)
		self.назначить({"due_mode": "relative", "due_days": 2})
		документ = домашка.сохранить(self.ученик, self.урок, self.организация, answer="рано")
		self.assertIsNone(документ.due_at)
		self.assertIsNone(документ.assigned_at)
		сдача = self.выдать(self.организация)
		self.assertEqual(сдача.status, "Submitted")
		self.assertEqual(self.дней_до_срока(сдача), timedelta(days=2))

	def test_источник_срока_из_журнала_если_правило_сняли(self):
		"""Срок выставлен сохранением по правилу назначения, правило потом сняли:
		выдача не пересчитывает срок и не приписывает его автору."""
		назначение = self.назначить({"due_mode": "absolute", "due_date": "2030-03-01"})
		документ = домашка.сохранить(self.ученик, self.урок, self.организация, answer="рано")
		первая = документ.history[0]
		self.assertEqual(
			(str(первая.due_at), первая.due_source), ("2030-03-01 23:59:59", "allocation")
		)
		назначение.set("homework_due", [])
		назначение.save(ignore_permissions=True)
		сдача = self.выдать(self.организация)
		self.assertEqual(str(сдача.due_at), "2030-03-01 23:59:59")
		self.assertEqual((сдача.history[-1].event, сдача.history[-1].due_source), ("assigned", "allocation"))
