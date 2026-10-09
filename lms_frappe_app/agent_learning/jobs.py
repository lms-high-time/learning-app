# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Фоновые задачи, которые ходят по записям всей платформы."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import frappe
from rq.timeouts import JobTimeoutException

ТОЧКА_ЗАПИСИ = "job_record"


@dataclass
class Итог:
	удалась: bool = False


@contextmanager
def по_одной(заголовок: str, doctype: str, name: str) -> Iterator[Итог]:
	"""Запись фоновой задачи под своей точкой сохранения; сбой — в лог, задача дальше.

	`Why:` одна сбойная запись иначе откатывала бы всю задачу, и задача
	переставала бы работать у всей платформы — запуск за запуском.

	Чего не делает: взаимоблокировку (`QueryDeadlockError`) и таймаут задачи
	(`JobTimeoutException`) не глотает. Взаимоблокировка MariaDB откатывает
	транзакцию целиком, точки сохранения после неё нет — откат к ней упал бы
	своей ошибкой. Таймаут, пойманный здесь, задача пережила бы до SIGKILL
	без следа в логе. Обе ошибки уходят наружу и роняют запуск. Не коммитит:
	транзакцию задачи коммитит планировщик, а сбой целого запуска откатывает
	и записи, сделанные до него. Таймаут ожидания блокировки
	(`QueryTimeoutError`) — обычный сбой записи: MariaDB откатывает только
	оператор, точка сохранения остаётся.
	"""
	итог = Итог()
	frappe.db.savepoint(ТОЧКА_ЗАПИСИ)
	try:
		yield итог
	except (frappe.QueryDeadlockError, JobTimeoutException):
		raise
	except Exception:
		frappe.db.rollback(save_point=ТОЧКА_ЗАПИСИ)
		frappe.log_error(title=заголовок, reference_doctype=doctype, reference_name=name)
	else:
		frappe.db.release_savepoint(ТОЧКА_ЗАПИСИ)
		итог.удалась = True
