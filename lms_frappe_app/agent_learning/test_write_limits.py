# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Пределы записи ученика: частота методов и квота файлов (learning-services#684)."""

import base64

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_days, now_datetime

from lms_frappe_app.agent_learning import write_limits as пределы
from lms_frappe_app.api import student
from lms_frappe_app.tests.sample_data import (
	зачислить,
	создать_пользователя,
	политика_по_умолчанию,
	создать_домашку,
	создать_ученика,
	создать_урок,
)
from lms_frappe_app.tests.test_consent import запрос

НАСТРОЙКИ = "Agent Learning Settings"

#: POST-методы ученика без предела частоты — с причиной.
БЕЗ_ПРЕДЕЛА = {
	"lms_frappe_app.api.student.homework": "только читает: POST — по договору транспорта",
	"lms_frappe_app.api.student.my_homework": "только читает: POST — по договору транспорта",
}


def _картинка() -> bytes:
	"""PNG больше мегабайта: шум не сжимается. Фото профиля Frappe открывает как
	изображение, поэтому байты должны им быть."""
	import io
	import os

	from PIL import Image

	буфер = io.BytesIO()
	Image.frombytes("RGB", (640, 640), os.urandom(640 * 640 * 3)).save(буфер, "PNG")
	return буфер.getvalue()


class TestМетодыПодПределом(UnitTestCase):
	def test_post_методы_ученика_под_пределом_или_в_исключениях(self):
		"""Ворота: каждый POST-метод `api/student.py` и `api/consent.py` — под
		пределом частоты или в `БЕЗ_ПРЕДЕЛА` с причиной. Новый метод без
		предела валит тест поимённо."""
		from lms_frappe_app.api import consent

		без = []
		for модуль in (student, consent):
			for имя, функция in vars(модуль).items():
				if not callable(функция):
					continue
				if frappe.allowed_http_methods_for_whitelisted_func.get(функция) != ["POST"]:
					continue
				полное = f"{модуль.__name__}.{имя}"
				if getattr(функция, "предел_частоты", None) not in пределы.ГРУППЫ and полное not in БЕЗ_ПРЕДЕЛА:
					без.append(полное)
		self.assertEqual(без, [], "POST-методы без предела частоты и без записи в БЕЗ_ПРЕДЕЛА")
		self.assertEqual(
			(student.remember.предел_частоты, student.save_chat_state.предел_частоты, student.submit_homework.предел_частоты),
			("запись", "состояние", "домашка"),
		)


class IntegrationTestWriteLimits(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(политика_по_умолчанию)
		адрес = getattr(frappe.local, "request_ip", None)
		self.addCleanup(setattr, frappe.local, "request_ip", адрес)
		# Все вызовы агентов приходят с одного адреса — MCP-сервиса.
		frappe.local.request_ip = "10.0.0.1"
		с = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"wl-{с}@example.com")
		self.сосед = создать_ученика(f"wln-{с}@example.com")
		self.урок = создать_урок(f"Урок {с}")
		зачислить(self.ученик, self.урок)

	def задать(self, **поля):
		for поле, значение in поля.items():
			frappe.db.set_single_value(НАСТРОЙКИ, поле, значение)
		frappe.clear_document_cache(НАСТРОЙКИ, НАСТРОЙКИ)

	def test_частота_считается_на_ученика_а_не_на_адрес(self):
		self.задать(student_writes_per_minute=2)
		with запрос("/api/method/lms_frappe_app.api.student.remember", "POST"):
			frappe.set_user(self.ученик)
			for номер in range(2):
				self.assertTrue(student.remember(kind="fact", key=f"k{номер}", text="да")["ok"])
			with self.assertRaises(frappe.RateLimitExceededError):
				student.remember(kind="fact", key="k3", text="да")
			# Сколько ждать — заголовком: MCP-сервис передаёт его агенту.
			self.assertTrue(1 <= int(frappe.local.response_headers.get("Retry-After")) <= 60)
			# Тот же адрес, другой ученик — свой счёт.
			frappe.set_user(self.сосед)
			self.assertTrue(student.remember(kind="fact", key="k0", text="да")["ok"])

	def test_сохранения_домашки_считаются_отдельно(self):
		self.задать(student_writes_per_minute=1, homework_saves_per_hour=2)
		создать_домашку(self.урок)
		with запрос("/api/method/lms_frappe_app.api.student.submit_homework", "POST"):
			frappe.set_user(self.ученик)
			for _ in range(2):
				self.assertTrue(student.submit_homework(lesson=self.урок, answer="сделал")["ok"])
			with self.assertRaises(frappe.RateLimitExceededError):
				student.submit_homework(lesson=self.урок, answer="сделал")

	def test_квота_файлов_ученика(self):
		"""Квота — на все файлы ученика: и сдачи домашки, и загрузки мимо неё."""
		self.задать(student_files_quota_mb=1)
		создать_домашку(self.урок)
		полмегабайта = b"x" * (600 * 1024)
		frappe.set_user(self.ученик)
		первая = student.submit_homework(
			lesson=self.урок, files=[{"name": "a.txt", "data": base64.b64encode(полмегабайта).decode()}]
		)
		self.assertTrue(первая["ok"], первая)

		вторая = student.submit_homework(
			lesson=self.урок, files=[{"name": "b.txt", "data": base64.b64encode(полмегабайта).decode()}]
		)
		self.assertEqual(вторая["error"]["code"], пределы.КВОТА_ИСЧЕРПАНА)
		self.assertEqual(вторая["error"]["limit_mb"], 1)

		with self.assertRaises(пределы.КвотаИсчерпана):
			frappe.get_doc(
				{"doctype": "File", "file_name": "c.txt", "content": полмегабайта, "is_private": 0}
			).insert()

	def test_квота_за_тридцать_дней(self):
		"""Файлы старше окна квоты места не занимают: свои файлы сдачи ученик не удаляет."""
		self.задать(student_files_quota_mb=1)
		frappe.set_user(self.ученик)
		старый = frappe.get_doc(
			{"doctype": "File", "file_name": "old.txt", "content": b"o" * (900 * 1024), "is_private": 1}
		).insert()
		frappe.set_user("Administrator")
		frappe.db.set_value("File", старый.name, "creation", add_days(now_datetime(), -31))
		frappe.set_user(self.ученик)
		свежий = frappe.get_doc(
			{"doctype": "File", "file_name": "new.txt", "content": b"n" * (900 * 1024), "is_private": 1}
		).insert()
		self.assertTrue(свежий.name)

	def test_кто_под_квотой(self):
		"""Квота — у учеников, руководитель команды — тоже ученик. Методист,
		модератор и администратор загружают материалы курса без квоты. Фото
		профиля — под квотой, как любая загрузка."""
		self.задать(student_files_quota_mb=1)
		с = frappe.generate_hash(length=6)
		кто = {
			"методист": (создать_пользователя(f"wlc-{с}@example.com", "Course Creator"), False),
			"модератор": (создать_пользователя(f"wlm-{с}@example.com", "Moderator"), False),
			"руководитель команды": (
				создать_пользователя(f"wlo-{с}@example.com", "LMS Student", "Organization Manager"),
				True,
			),
			"ученик": (self.ученик, True),
		}
		большой = _картинка()
		for что, (пользователь, под_квотой) in кто.items():
			with self.subTest(что):
				frappe.set_user(пользователь)
				try:
					фото = frappe.get_doc(
						{
							"doctype": "File",
							"file_name": f"photo-{что}.png",
							"content": большой,
							"is_private": 0,
							"attached_to_doctype": "User",
							"attached_to_name": пользователь,
							"attached_to_field": "user_image",
						}
					)
					if под_квотой:
						with self.assertRaises(пределы.КвотаИсчерпана):
							фото.insert()
					else:
						self.assertTrue(фото.insert().name)
				finally:
					frappe.set_user("Administrator")

	def test_квота_не_касается_авторов_и_администраторов(self):
		self.задать(student_files_quota_mb=1)
		большой = b"y" * (1100 * 1024)
		файл = frappe.get_doc(
			{"doctype": "File", "file_name": "big.txt", "content": большой, "is_private": 1}
		).insert()
		self.assertTrue(файл.name)
