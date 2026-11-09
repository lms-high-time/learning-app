# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Письмо о получателях данных при создании аккаунта (learning-services#747).

ЗРУ-547, ст. 23: «В случае передачи персональных данных третьему лицу
собственник и (или) оператор в течение трех дней уведомляет в письменной форме
об этом субъекта» — исключений для согласия и договора нет. Получатели — те,
через кого работает платформа, и данные к ним уходят с первых минут аккаунта:
письмо уходит сразу, при создании.

Список повторяет политику конфиденциальности, §8
(`site/content/privacy.md` в learning-services). Меняешь получателей — правь
политику, это письмо и `docs/process/data-classification.md` вместе: письмо
обещает то же, что политика. Новый получатель — повод для новой редакции
политики, а о ней пользователи узнают письмом и экраном согласия.
"""

import frappe
from frappe import _
from frappe.utils import escape_html, get_url

from lms_frappe_app.agent_learning import notices

#: Куда писать о данных — та же почта, что в политике.
ПОЧТА_ДАННЫХ = "privacy@cenda.app"


def сообщить_о_получателях(user, method=None):
	"""`after_insert` пользователя: самостоятельная регистрация — письмо.

	Самостоятельная — в гостевом запросе, как у записи согласия
	(`consent.записать_согласие`): формой, первым входом через Google,
	принятием приглашения. Заведённому сотрудником в desk письмо не уходит —
	его заводят по просьбе человека, и о данных ему сообщают при этом.
	"""
	if frappe.session.user != "Guest" or user.name in ("Guest", "Administrator"):
		return
	if not user.email or not notices.почта_есть():
		return
	тема, текст = письмо(user.language or frappe.db.get_single_value("System Settings", "language"))
	frappe.sendmail(recipients=[user.email], subject=тема, message=текст)


def письмо(язык: str) -> tuple[str, str]:
	"""Тема и HTML письма на языке получателя."""
	название = escape_html(frappe.db.get_single_value("Website Settings", "app_name") or get_url())
	получатели = (
		_("Resend — your email address, name and the text of service emails, to deliver them.", lang=язык),
		_(
			"Cloudflare — backups of the platform, encrypted before they leave our server; Cloudflare has no key.",
			lang=язык,
		),
		_("Google — the fact that you signed in, if you sign in with Google.", lang=язык),
		_(
			"OpenRouter and the language model provider — your web chat conversation, if you study in the browser.",
			lang=язык,
		),
		_(
			"PostHog — how you use the site, only if you allow analytics, and without your name or email.",
			lang=язык,
		),
	)
	политика = notices.ссылка(
		get_url("/en/privacy" if язык == "en" else "/privacy"), _("Privacy Policy, section 8", lang=язык)
	)
	почта = notices.ссылка(f"mailto:{ПОЧТА_ДАННЫХ}", ПОЧТА_ДАННЫХ)
	текст = (
		"<p>"
		+ _(
			"You have created an account on {0}. The platform runs on several services, and some of your data goes to them:",
			lang=язык,
		).format(название)
		+ "</p><ul>"
		+ "".join(f"<li>{строка}</li>" for строка in получатели)
		+ "</ul><p>"
		+ _(
			"If you connect your own AI assistant (Claude, ChatGPT or another), its provider receives lesson data under your agreement with it.",
			lang=язык,
		)
		+ "</p><p>"
		+ _(
			"Where these services are and on what grounds we pass data to them: {0}. Questions: {1}.",
			lang=язык,
		).format(политика, почта)
		+ "</p>"
	)
	return _("Who receives your data on {0}", lang=язык).format(название), текст
