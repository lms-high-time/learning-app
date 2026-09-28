# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Куда попадает вошедший человек.

`Why:` домашнюю страницу задаёт не наше приложение и не `frappe/lms` — без
хука Frappe доходит до последнего фолбэка и показывает страницу настроек
`me`, из которой новому ученику некуда идти. Пропажа хука выглядела бы как
«всё работает»: сайт отвечает, вход проходит, просто ведёт не туда.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.website.utils import get_home_page

from lms_frappe_app.tests.sample_data import (
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
)

ДОМАШНЯЯ = "lms"


class IntegrationTestHomePage(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(frappe.local.flags.pop, "home_page", None)
		frappe.local.flags.pop("home_page", None)
		self.ученик = создать_ученика(
			f"home-{frappe.generate_hash(length=6)}@example.com"
		)

	def test_ученик_попадает_в_lms(self):
		frappe.set_user(self.ученик)

		self.assertEqual(
			get_home_page(),
			ДОМАШНЯЯ,
			"домашней не должна быть страница настроек `me` — именно её видел первый вошедший через Google",
		)

	def test_сотрудника_платформы_ведём_в_воркспейс(self):
		"""`Why:` строковый хук уводил администратора в ученический интерфейс,
		а без хука он попадал на общий `/desk` — стартовый экран приложений,
		откуда до курсов и учеников ещё идти (learning-services#302)."""
		from lms_frappe_app.www.home import РАБОЧАЯ, домашняя_страница

		self.assertEqual(домашняя_страница("Administrator"), РАБОЧАЯ)
		self.assertEqual(РАБОЧАЯ, "desk/agent-learning")

	def test_ученику_отдаём_каталог(self):
		from lms_frappe_app.www.home import домашняя_страница

		self.assertEqual(домашняя_страница(self.ученик), ДОМАШНЯЯ)

	def test_руководителя_организации_ведём_в_lms(self):
		"""Роль по членству не открывает desk: с `desk_access` руководитель
		становился System User и после входа попадал в воркспейс сотрудников
		вместо «Команды» (learning-services#374)."""
		from lms_frappe_app.www.home import домашняя_страница

		for роль in ("Organization Manager", "Organization Admin"):
			self.assertFalse(frappe.db.get_value("Role", роль, "desk_access"), роль)

		организация = создать_организацию(f"home-{frappe.generate_hash(length=6)}")
		руководитель = создать_менеджера(
			f"home-mgr-{frappe.generate_hash(length=6)}@example.com", организация
		)

		self.assertIn("Organization Manager", frappe.get_roles(руководитель))
		self.assertEqual(frappe.db.get_value("User", руководитель, "user_type"), "Website User")
		self.assertEqual(домашняя_страница(руководитель), ДОМАШНЯЯ)

	def test_хук_объявлен_приложением(self):
		"""Значение приходит из hooks.py, а не из настроек сайта: настройка в
		базе не переживает пересоздания сайта."""
		self.assertTrue(frappe.get_hooks("get_website_user_home_page"))
