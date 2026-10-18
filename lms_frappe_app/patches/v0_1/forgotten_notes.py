# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Тексты удалённых заметок об ученике — из корзины и истории правок (learning-services#578).

`forget` стирает заметку без копии в корзине и вместе с историей правок. До
этого от удалённых заметок оставались:

- копии в корзине (`Deleted Document`) с полным текстом — их видит
  администратор;
- версии (`Version`) с прежними текстами — у заметок, удалённых в обход
  `delete_doc` или при упавшем воркере очереди.

Патч удаляет и то и другое. `Why:` политика конфиденциальности обещает
удаление и для заметок, стёртых раньше. Версии живых заметок не трогаются.
Повторный запуск ничего не меняет: удалять уже нечего.
"""

import frappe

ЗАМЕТКА = "Agent Student Note"


def execute():
	if not frappe.db.table_exists(ЗАМЕТКА, cached=False):
		print("forgotten_notes: заметок нет — делать нечего")
		return
	копий = _удалить(
		"Deleted Document",
		frappe.get_all("Deleted Document", filters={"deleted_doctype": ЗАМЕТКА}, pluck="name"),
	)
	живые = set(frappe.get_all(ЗАМЕТКА, pluck="name"))
	версии = frappe.get_all("Version", filters={"ref_doctype": ЗАМЕТКА}, fields=["name", "docname"])
	версий = _удалить("Version", [в.name for в in версии if в.docname not in живые])
	print(f"forgotten_notes: копий в корзине удалено {копий}, версий удалённых заметок {версий}")


def _удалить(доктайп: str, имена: list[str]) -> int:
	if имена:
		frappe.db.delete(доктайп, {"name": ("in", имена)})
	return len(имена)
