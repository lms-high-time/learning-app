# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Запись согласия с документами при самостоятельной регистрации (learning-services#577).

Проверяется, кто получает запись и с чем: регистрация формой и первым входом
через провайдера — да, с редакцией документов из настроек; аккаунт от
администратора — нет. Пустая настройка не роняет регистрацию.
"""

from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from lms_frappe_app.agent_learning.consent import ВНЕШНИЙ_ВХОД, РЕГИСТРАЦИЯ
from lms_frappe_app.agent_learning.doctype.agent_learning_settings.agent_learning_settings import (
	НАСТРОЙКИ,
)


def почта() -> str:
	return f"consent-{frappe.generate_hash(length=8)}@example.com"


def зарегистрировать(почта: str, *, провайдер: str | None = None) -> str:
	"""Пользователь создаётся так, как это делают `sign_up` и вход через
	провайдера: в гостевом запросе, с флагом обхода прав."""
	пользователь = frappe.new_doc("User")
	пользователь.update({"email": почта, "first_name": "Ученик", "send_welcome_email": 0})
	if провайдер:
		пользователь.append("social_logins", {"provider": провайдер, "userid": "id-" + почта})
	пользователь.flags.ignore_permissions = True
	пользователь.insert()
	return почта


def согласия(почта: str) -> list[frappe._dict]:
	return frappe.get_all(
		"Agent Consent",
		filters={"user": почта},
		fields=["documents_version", "method", "provider", "accepted_at"],
	)


class IntegrationTestConsent(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")

	def test_регистрация_формой_записывает_согласие_с_редакцией(self):
		frappe.db.set_single_value(НАСТРОЙКИ, "legal_version", "2026-10-14")

		[запись] = согласия(зарегистрировать(почта()))

		self.assertEqual(запись.documents_version, "2026-10-14")
		# Служебную строку входа `frappe` Frappe добавляет каждому пользователю:
		# она не делает регистрацию формой входом через провайдера.
		self.assertEqual(запись.method, РЕГИСТРАЦИЯ)
		self.assertFalse(запись.provider)
		self.assertTrue(запись.accepted_at)

	def test_первый_вход_через_google_записывает_провайдера(self):
		"""Кнопка «Войти через Google» у нового человека и есть регистрация."""
		[запись] = согласия(зарегистрировать(почта(), провайдер="google"))

		self.assertEqual(запись.method, ВНЕШНИЙ_ВХОД)
		self.assertEqual(запись.provider, "google")

	def test_аккаунт_от_администратора_записи_не_получает(self):
		"""Человек страниц входа не видел — согласие собирает тот, кто его заводит."""
		frappe.set_user("Administrator")

		self.assertEqual(согласия(зарегистрировать(почта())), [])

	def test_удаление_пользователя_не_мешает_и_запись_остаётся(self):
		"""Ссылка из записи согласия не запрещает удалить пользователя, а сама
		запись — доказательство — остаётся."""
		адрес = зарегистрировать(почта())
		frappe.set_user("Administrator")

		frappe.delete_doc("User", адрес, ignore_permissions=True)

		self.assertFalse(frappe.db.exists("User", адрес))
		self.assertEqual(len(согласия(адрес)), 1)

	def test_пустая_редакция_в_настройках_не_роняет_регистрацию(self):
		frappe.db.set_single_value(НАСТРОЙКИ, "legal_version", "")
		умолчание = frappe.get_meta(НАСТРОЙКИ).get_field("legal_version").default

		[запись] = согласия(зарегистрировать(почта()))

		self.assertTrue(умолчание)
		self.assertEqual(запись.documents_version, умолчание)

	def test_вход_существующего_ничего_не_записывает(self):
		"""Привязка Google к уже существующему аккаунту — сохранение, а не вставка."""
		адрес = зарегистрировать(почта())
		frappe.set_user("Guest")
		пользователь = frappe.get_doc("User", адрес)
		пользователь.append("social_logins", {"provider": "google", "userid": "id-existing"})
		пользователь.flags.ignore_permissions = True
		пользователь.save()

		self.assertEqual(len(согласия(адрес)), 1)


class UnitTestConsentNotice(UnitTestCase):
	def test_подпись_подключена_к_веб_страницам(self):
		"""Без подписи запись согласия ничего не доказывает: человек не видел,
		с чем соглашается."""
		адрес = "/assets/lms_frappe_app/js/consent-notice.js"
		self.assertIn(адрес, frappe.get_hooks("web_include_js"))
		# Аналитика — общий скрипт сайта cenda.app, а не своя копия
		# (learning-services#582).
		self.assertIn("/site/analytics.js", frappe.get_hooks("web_include_js"))
		файл = Path(frappe.get_app_path("lms_frappe_app", "public", "js", "consent-notice.js"))
		текст = файл.read_text(encoding="utf-8")
		for нужное in ('"/terms"', '"/privacy"', ".for-login", ".for-signup"):
			self.assertIn(нужное, текст)
