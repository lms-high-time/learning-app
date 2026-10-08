# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Организация-клиент: обязательность квиза, домены и список курсов.

Лимит попыток и пауза — платформы, одни на всех: урок, сданный лично,
годится любой компании (learning-services#353). Организация решает только,
обязателен ли квиз.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.doctype.learning_organization.learning_organization import (
	политика_квиза,
)
from lms_frappe_app.tests.sample_data import (
	настроить_квиз,
	политика_по_умолчанию,
	создать_курс,
	создать_организацию,
)


class IntegrationTestLearningOrganization(IntegrationTestCase):
	def setUp(self):
		политика_по_умолчанию()
		self.организация = создать_организацию(f"Компания {frappe.generate_hash(length=6)}")

	def правка(self, **поля):
		организация = frappe.get_doc("Learning Organization", self.организация)
		организация.update(поля)
		организация.save(ignore_permissions=True)
		return организация

	# --- политика квиза ---

	def test_лимит_и_пауза_у_организации_не_свои(self):
		настроить_квиз(max_attempts=0, retry_delay_minutes=10)

		политика = политика_квиза(self.организация)

		self.assertEqual((политика["max_attempts"], политика["retry_delay_minutes"]), (0, 10))

	def test_требование_квиза_перекрывается_и_наследуется(self):
		self.правка(quiz_required="No")
		self.assertFalse(политика_квиза(self.организация)["quiz_required"])

		self.правка(quiz_required="Yes")
		self.assertTrue(политика_квиза(self.организация)["quiz_required"])

		# Пустое — как в общих настройках, где квиз обязателен.
		self.правка(quiz_required="")
		self.assertTrue(политика_квиза(self.организация)["quiz_required"])

	# --- проверки при сохранении ---

	def test_домены_приводятся_к_единому_виду(self):
		организация = self.правка(email_domains="@Example.COM\n\n example.com \nzavod.ru")

		self.assertEqual(организация.email_domains, "example.com\nzavod.ru")

	# --- список курсов ---

	def test_пустой_список_курсов_означает_весь_каталог(self):
		"""`Why:` у большинства клиентов ограничений нет, и перечислять курс за
		курсом они не обязаны — список рано или поздно разъедется с каталогом."""
		организация = frappe.get_doc("Learning Organization", self.организация)

		self.assertTrue(организация.разрешает_курс(создать_курс("Любой курс")))

	def test_непустой_список_закрывает_остальные_курсы(self):
		разрешённый = создать_курс(f"Разрешённый {frappe.generate_hash(length=6)}")
		посторонний = создать_курс(f"Посторонний {frappe.generate_hash(length=6)}")
		организация = self.правка(allowed_courses=[{"course": разрешённый}])

		self.assertTrue(организация.разрешает_курс(разрешённый))
		self.assertFalse(организация.разрешает_курс(посторонний))
