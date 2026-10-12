# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AgentArtifactBlock(Document):
	"""Блок схемы документа — раздел релиза: ключ, название, описание для
	ученика, урок, `rows` и колонки.

	Наружу ключ зовётся `key`, в схеме — `block_key`: `key` — зарезервированное
	слово SQL, и экранирует его Frappe не везде. Та же причина, что у
	`note_key` в заметках.
	"""
