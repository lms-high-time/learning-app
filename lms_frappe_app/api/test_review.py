# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import homework as домашка
from lms_frappe_app.api import review
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	создать_домашку,
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_урок,
	создать_ученика,
)


class IntegrationTestReviewQueue(IntegrationTestCase):
	"""Очередь «Ждут проверки» и карточка куратора (learning-services#452)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		с = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"rq-{с}@example.com")
		frappe.db.set_value("User", self.ученик, {"first_name": "Анна", "full_name": "Анна Петрова"})
		frappe.clear_document_cache("User", self.ученик)
		self.урок = создать_урок(f"Урок {с}")
		self.курс = зачислить(self.ученик, self.урок)
		создать_домашку(self.урок)
		self.организация = создать_организацию(f"Орг {с}")
		добавить_в_организацию(self.ученик, self.организация)
		self.чужая_организация = создать_организацию(f"Чужая {с}")
		добавить_в_организацию(self.ученик, self.чужая_организация)
		self.менеджер = создать_менеджера(f"rqm-{с}@example.com", self.организация)
		self.методист = создать_куратора(f"rqc-{с}@example.com", роль="Course Creator")
		self.рабочая = домашка.сохранить(self.ученик, self.урок, self.организация, answer="в компании")
		self.личная = домашка.сохранить(self.ученик, self.урок, None, answer="лично")
		self.чужая = домашка.сохранить(self.ученик, self.урок, self.чужая_организация, answer="в другой")
		# Старые сверху: время сдачи задаётся явно, иначе порядок решала бы
		# секунда вставки.
		for номер, сдача in enumerate((self.личная, self.рабочая, self.чужая)):
			frappe.db.set_value(домашка.СДАЧА, сдача.name, "submitted_at", f"2026-10-0{номер + 1} 10:00:00")
		# Рабочая — обязательная, как в пространстве команды, назначившей курс;
		# откуда берётся признак — `test_homework`. Остальные — практика.
		frappe.db.set_value(домашка.СДАЧА, self.рабочая.name, "required", 1)

	def очередь(self, user, **параметры) -> dict:
		frappe.set_user(user)
		ответ = review.queue(**параметры)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def вызвать(self, user, метод, **параметры) -> dict:
		frappe.set_user(user)
		return getattr(review, метод)(**параметры)

	def код(self, ответ) -> str:
		self.assertFalse(ответ["ok"], ответ)
		return ответ["error"]["code"]

	# --- очередь ---

	def test_очередь_по_ролям_и_фильтрам(self):
		"""Руководитель видит только свою организацию — ни личного, ни чужой;
		методист — всё по курсу, старые сверху; `limit` режет страницу, а не
		`total`; ученику очередь пуста. Свою сдачу куратор в очереди не видит —
		последним: эта строка добавляет сдачу в курс."""
		with self.subTest("руководитель: строка своей организации"):
			данные = self.очередь(self.менеджер)
			self.assertEqual([с["id"] for с in данные["items"]], [self.рабочая.name])
			self.assertEqual(данные["total"], 1)
			[строка] = данные["items"]
			self.assertEqual(строка["student"], {"name": "Анна Петрова"})
			self.assertEqual(
				(строка["course"], строка["lesson"], строка["organization"], строка["organization_title"]),
				(self.курс, self.урок, self.организация, self.организация),
			)
			self.assertEqual((строка["version"], строка["reviewed_version"], строка["overdue"]), (1, None, False))
			self.assertTrue(строка["required"])
			self.assertEqual(данные["organizations"], [{"id": self.организация, "title": self.организация}])
			self.assertEqual([к["id"] for к in данные["courses"]], [self.курс])

		with self.subTest("методист: личное подписано, практика — в очереди с признаком"):
			# Подпись личного — на языке запроса (learning-services#766).
			self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
			frappe.local.lang = "ru"
			данные = self.очередь(self.методист, course=self.курс)
			личная = данные["items"][0]
			self.assertEqual((личная["organization"], личная["organization_title"]), ("personal", "Личное"))
			self.assertFalse(личная["required"])
			self.assertIn({"id": "personal", "title": "Личное"}, данные["organizations"])
			frappe.local.lang = "en"
			self.assertIn(
				{"id": "personal", "title": "Personal"}, self.очередь(self.методист, course=self.курс)["organizations"]
			)

		отборы = {
			"руководитель: личное": (self.менеджер, {"organization": "personal"}, []),
			"руководитель: чужая организация": (self.менеджер, {"organization": self.чужая_организация}, []),
			"методист: курс, старые сверху": (
				self.методист,
				{"course": self.курс},
				[self.личная.name, self.рабочая.name, self.чужая.name],
			),
			"методист: личное": (self.методист, {"course": self.курс, "organization": "personal"}, [self.личная.name]),
			"методист: организация": (
				self.методист,
				{"course": self.курс, "organization": self.организация},
				[self.рабочая.name],
			),
		}
		for что, (кто, параметры, сдачи) in отборы.items():
			with self.subTest(что):
				self.assertEqual([с["id"] for с in self.очередь(кто, **параметры)["items"]], сдачи)

		with self.subTest("limit и total"):
			данные = self.очередь(self.методист, course=self.курс, limit=2)
			self.assertEqual((len(данные["items"]), данные["total"]), (2, 3))
			self.assertEqual(len(self.очередь(self.методист, course=self.курс, limit=500)["items"]), 3)

		with self.subTest("ученику очередь пуста"):
			данные = self.очередь(self.ученик)
			self.assertEqual((данные["items"], данные["total"]), ([], 0))
			self.assertEqual(self.вызвать(self.ученик, "pending_count")["data"], {"count": 0})

		frappe.set_user("Administrator")
		зачислить(self.методист, self.урок)
		своя = домашка.сохранить(self.методист, self.урок, None, answer="моя")
		with self.subTest("свою сдачу куратор не видит"):
			self.assertNotIn(своя.name, [с["id"] for с in self.очередь(self.методист, course=self.курс)["items"]])

	def test_фильтр_курса(self):
		другой = создать_урок(f"Урок {frappe.generate_hash(length=6)}")
		зачислить(self.ученик, другой)
		создать_домашку(другой)
		домашка.сохранить(self.ученик, другой, self.организация, answer="другой курс")
		self.assertEqual(self.очередь(self.менеджер)["total"], 2)
		self.assertEqual(
			[с["id"] for с in self.очередь(self.менеджер, course=self.курс)["items"]], [self.рабочая.name]
		)

	def test_фильтр_статуса(self):
		домашка.проверить(self.методист, self.рабочая.name, "send_back", 1, "Доделайте")
		self.assertEqual(self.очередь(self.менеджер)["items"], [])
		[строка] = self.очередь(self.менеджер, status="Returned")["items"]
		self.assertEqual((строка["status"], строка["reviewed_version"]), ("Returned", 1))

	def test_архивные_не_видны(self):
		frappe.db.set_value(
			домашка.СДАЧА, self.рабочая.name, {"member": None, "archived_student": self.ученик}
		)
		данные = self.очередь(self.менеджер)
		self.assertEqual((данные["items"], данные["total"]), ([], 0))

	def test_счётчик_ожидающих(self):
		self.assertEqual(self.вызвать(self.менеджер, "pending_count")["data"], {"count": 1})
		домашка.проверить(self.методист, self.рабочая.name, "accept", 1)
		self.assertEqual(self.вызвать(self.менеджер, "pending_count")["data"], {"count": 0})

	# --- карточка ---

	def test_карточка_и_действия_по_статусу(self):
		"""Своя сдача — карточка без действий; чужую не открыть и не вернуть; нет
		сдачи — свой код. Отказы ничего не меняют, затем карточка руководителя и
		действия по статусу."""
		with self.subTest("своя сдача: карточка без действий"):
			карточка = self.вызвать(self.ученик, "submission", submission=self.рабочая.name)["data"]
			self.assertEqual(карточка["actions"], [])

		отказы = {
			"ученик: принять свою": (self.ученик, "accept", {"submission": self.рабочая.name, "version": 1}, "not_allowed"),
			"руководитель: карточка личной": (self.менеджер, "submission", {"submission": self.личная.name}, "not_allowed"),
			"руководитель: вернуть личную": (
				self.менеджер,
				"send_back",
				{"submission": self.личная.name, "version": 1, "comment": "x"},
				"not_allowed",
			),
			"нет сдачи: карточка": (self.менеджер, "submission", {"submission": "нет-такой"}, "submission_not_found"),
			"нет сдачи: принять": (
				self.менеджер,
				"accept",
				{"submission": "нет-такой", "version": 1},
				"submission_not_found",
			),
		}
		for что, (кто, метод, параметры, код) in отказы.items():
			with self.subTest(что):
				self.assertEqual(self.код(self.вызвать(кто, метод, **параметры)), код)
		with self.subTest("отказы сдачи не меняют"):
			self.assertEqual(
				frappe.db.get_value(домашка.СДАЧА, self.рабочая.name, ["status", "version"]), ("Submitted", 1)
			)
			self.assertEqual(frappe.db.get_value(домашка.СДАЧА, self.личная.name, ["status", "version"]), ("Submitted", 1))

		карточка = self.вызвать(self.менеджер, "submission", submission=self.рабочая.name)["data"]
		self.assertEqual(карточка["actions"], ["accept", "send_back"])
		self.assertEqual(карточка["submission"]["id"], self.рабочая.name)
		self.assertIn("versions", карточка["submission"])
		self.assertEqual(карточка["homework"]["title"], "Встреча со спонсором")
		self.assertEqual((карточка["submission"]["required"], карточка["homework"]["required"]), (True, True))
		self.assertEqual(
			(карточка["student"], карточка["reviewed_version"]), ({"name": "Анна Петрова"}, None)
		)
		self.assertTrue(карточка["lesson_url"].startswith(f"/lms/courses/{self.курс}/learn/"))
		[событие] = карточка["submission"]["history"]
		self.assertEqual((событие["by"], событие["by_name"]), (None, "Анна Петрова"))

		принята = self.вызвать(self.менеджер, "accept", submission=self.рабочая.name, version=1)["data"]
		self.assertEqual((принята["submission"]["status"], принята["actions"]), ("Accepted", ["reopen"]))
		self.assertEqual(принята["reviewed_version"], 1)
		self.assertEqual(принята["submission"]["history"][-1]["by"], self.менеджер)
		возвращена = self.вызвать(
			self.менеджер, "reopen", submission=self.рабочая.name, version=1, comment="Не то"
		)["data"]
		self.assertEqual((возвращена["submission"]["status"], возвращена["actions"]), ("Returned", []))

	def test_вернуть_и_пересдать(self):
		ответ = self.вызвать(
			self.менеджер, "send_back", submission=self.рабочая.name, version="1", comment="Доделайте"
		)
		self.assertEqual(ответ["data"]["submission"]["status"], "Returned")
		домашка.сохранить(self.ученик, self.урок, self.организация, answer="доделал")
		карточка = self.вызвать(self.менеджер, "submission", submission=self.рабочая.name)["data"]
		self.assertEqual((карточка["submission"]["version"], карточка["reviewed_version"]), (2, 1))

	def гонка(self, *исходы):
		"""`проверить` по очереди: исключение — бросить, `None` — настоящий вызов.

		`Why:` взаимоблокировку MariaDB стенда в тесте не вызвать — одна
		транзакция; заглушка отдаёт её так, как база отдала бы при гонке.
		Откат подменён: настоящий снял бы данные теста.
		"""
		настоящий = домашка.проверить
		очередь = list(исходы)

		def проверить(*args, **kwargs):
			исход = очередь.pop(0)
			if callable(исход):
				исход = исход()
			if исход is None:
				return настоящий(*args, **kwargs)
			raise исход

		return patch.object(домашка, "проверить", side_effect=проверить), patch.object(frappe.db, "rollback")

	def test_гонка_дважды_даёт_busy_а_один_раз_повторяется(self):
		"""Повтор, который принимает сдачу, — последней строкой: отказы `busy`
		сдачу не трогают."""
		взаимоблокировка = frappe.QueryDeadlockError("1213")
		таймаут = frappe.QueryTimeoutError("1205")
		случаи = {
			"взаимоблокировка дважды": ((взаимоблокировка, взаимоблокировка), (False, "busy", None)),
			"таймаут дважды": ((таймаут, таймаут), (False, "busy", None)),
			"взаимоблокировка, затем успех": ((frappe.QueryDeadlockError("1213"), None), (True, None, "Accepted")),
		}
		for что, (исходы, ожидание) in случаи.items():
			with self.subTest(что):
				подмена, откат = self.гонка(*исходы)
				with подмена, откат:
					ответ = self.вызвать(self.менеджер, "accept", submission=self.рабочая.name, version=1)
				итог = (
					ответ["ok"],
					(ответ.get("error") or {}).get("code"),
					((ответ.get("data") or {}).get("submission") or {}).get("status"),
				)
				self.assertEqual(итог, ожидание, ответ)

	def test_гонка_с_новой_версией_даёт_stale_version(self):
		"""Ученик сохранил, пока куратор ждал блокировку: повтор видит новую версию."""

		def сохранил_и_заблокировал():
			frappe.db.set_value(домашка.СДАЧА, self.рабочая.name, "version", 2)
			return frappe.QueryDeadlockError("1213")

		подмена, откат = self.гонка(сохранил_и_заблокировал, None)
		with подмена, откат:
			ответ = self.вызвать(self.менеджер, "accept", submission=self.рабочая.name, version=1)
		self.assertEqual((self.код(ответ), ответ["error"]["version"]), ("stale_version", 2))
		self.assertEqual(
			self.код(
				self.вызвать(self.менеджер, "send_back", submission=self.рабочая.name, version=2, comment=" ")
			),
			"comment_required",
		)
		ответ = self.вызвать(
			self.менеджер, "reopen", submission=self.рабочая.name, version=2, comment="Не то"
		)
		self.assertEqual((self.код(ответ), ответ["error"]["status"]), ("wrong_status", "Submitted"))
