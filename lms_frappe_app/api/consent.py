# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Явное согласие с документами с экрана `/consent` (learning-services#624).

Экран видит вошедший, у которого нет явного согласия с действующей редакцией:
пришёл через Google, зарегистрировался до отметок или редакция сменилась.
Метод зовёт сам экран, агент — нет.
"""

import frappe

from lms_frappe_app.agent_learning import consent
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.api import контракт, текущий_пользователь

#: Не отмечено «мне есть 18 лет» или «принимаю документы».
НУЖНЫ_ОТМЕТКИ = "consent_marks_required"


def _да(значение) -> bool:
	# Флаг приходит и булевым из JSON, и строкой из формы.
	return значение in (True, 1, "1", "true")


@frappe.whitelist(methods=["POST"])
@контракт
def accept(age_confirmed=False, documents_accepted=False) -> dict:
	"""Записывает явное согласие: пользователю есть 18 лет, документы он принимает."""
	пользователь = текущий_пользователь()
	if not (_да(age_confirmed) and _да(documents_accepted)):
		raise Отказ(НУЖНЫ_ОТМЕТКИ, "Отметьте обе галочки: возраст и согласие с документами")
	consent.принять(пользователь)
	return {"version": consent.редакция_документов()}
