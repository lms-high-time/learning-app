# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

#: Поля версии, которые после записи не меняются: те, из которых собирается
#: схема, и описание. `note` в их число не входит: пояснение к версии можно
#: уточнить, не трогая того, на что опираются курсы. Родитель наследника и
#: правки к нему — входят: схема версии собрана из них. Как и переименования:
#: по ним курсы переносят данные учеников при переходе. Описание — тоже: по
#: нему агент выбрал шаблон, и правка задним числом подменила бы выбор.
ПОЛЯ_ВЕРСИИ = (
	"template",
	"version",
	"title",
	"description",
	"layout",
	"blocks",
	"canvas",
	"renamed",
	"extends",
	"extends_version",
	"overlay",
)


class AgentArtifactTemplate(Document):
	"""Шаблон документа курса — одна версия (learning-services#370).

	Версия после записи не меняется: курс закрепляет её и собирает из неё
	свою схему, и правка задним числом поменяла бы документ курса, который
	автор не трогал. Новая схема — новая запись со следующим номером.
	"""

	def before_insert(self):
		# Номер всегда считает система: переданный затирается, иначе импорт
		# тихо создал бы вторую «версию 1» того же шаблона.
		прошлые = frappe.get_all(
			self.doctype,
			filters={"template": self.template},
			pluck="version",
			order_by="version desc",
			limit=1,
		)
		self.version = (прошлые[0] if прошлые else 0) + 1

	def validate(self):
		прежний = None if self.is_new() else self.get_doc_before_save()
		if прежний is None:
			return
		изменены = [поле for поле in ПОЛЯ_ВЕРСИИ if str(прежний.get(поле) or "") != str(self.get(поле) or "")]
		if изменены:
			frappe.throw(
				frappe._("Версия шаблона не меняется после записи — заведите новую: {0}").format(
					", ".join(изменены)
				)
			)
