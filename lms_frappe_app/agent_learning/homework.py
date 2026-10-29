# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Домашние задания к урокам (learning-services#439).

Задание — `Agent Lesson Homework`, одно на урок, от автора. Сдача —
`Agent Homework Submission`, одна живая на «задание + ученик + пространство».
Все изменения сдачи идут через этот модуль: он пишет журнал и версии, а схема
доктайпа запись разрешает только System Manager.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, time, timedelta

import frappe
from frappe import _
from frappe.core.api.file import get_max_file_size
from frappe.core.doctype.file.exceptions import MaxFileSizeReachedError
from frappe.utils import get_datetime, getdate, now_datetime

from lms_frappe_app.agent_learning import uploads as файлы_платформы
from lms_frappe_app.agent_learning.constants import (
	ДОМАШКА_ВОЗВРАЩЕНА,
	ДОМАШКА_ВЫДАНА,
	ДОМАШКА_ПРИНЯТА,
	ДОМАШКА_СДАНА,
)
from lms_frappe_app.agent_learning.errors import ЗАНЯТО, Отказ
from lms_frappe_app.agent_learning.structure import уроки_курса, уроки_по_главам
from lms_frappe_app.agent_learning.write_limits import КВОТА_ПРОВЕРЕНА, проверить_квоту

ЗАДАНИЕ = "Agent Lesson Homework"
СДАЧА = "Agent Homework Submission"

#: Предел файлов в одном ответе. Размер файла — `artifact_file_max_mb`.
ПРЕДЕЛ_ФАЙЛОВ = 10
#: Сумма новых файлов одного сохранения. `Why:` тело запроса к методу Frappe
#: режет до `max_file_size` конфигурации сайта, по умолчанию 25 МБ
#: (`frappe/app.py`, `init_request`), а MCP шлёт файлы в base64 (+33%):
#: 18 МБ файлов — около 24 МБ тела. Больше — 413 до метода вместо отказа.
ПРЕДЕЛ_СОХРАНЕНИЯ_МБ = 18

#: Длина текста ответа, знаков. `Why:` каждое сохранение кладёт текст ещё и в
#: версию, а длинный отчёт сдают файлом; два десятка тысяч знаков — около
#: десяти страниц.
ДЛИНА_ОТВЕТА = 20000

ЗАДАНИЯ_НЕТ = "no_homework"
ПУСТОЙ_ОТВЕТ = "empty_answer"
НЕ_ТОТ_ВИД_ОТВЕТА = "answer_mode"
СЛИШКОМ_МНОГО_ФАЙЛОВ = "too_many_files"
ФАЙЛ_ВЕЛИК = "file_too_large"
ОТВЕТ_ВЕЛИК = "answer_too_large"
ДЛИННЫЙ_ОТВЕТ = "answer_text_too_long"
ФАЙЛ_ОТКЛОНЁН = "file_rejected"
ТИП_НЕ_ПРИНЯТ = "file_type_not_allowed"
ФАЙЛ_ПУСТ = "file_missing"
НЕВЕРНЫЕ_ФАЙЛЫ = "invalid_files"
УЖЕ_ПРИНЯТА = "accepted_locked"

ТОЧКА_СОХРАНЕНИЯ = "agent_homework_save"
ТОЧКА_ВСТАВКИ = "agent_homework_insert"

ПОЛЯ_ЗАДАНИЯ = [
	"name",
	"lesson",
	"title",
	"description",
	"answer_mode",
	"due_mode",
	"due_days",
	"due_date",
	"retired",
]


def задание_урока(lesson: str) -> frappe._dict | None:
	return frappe.db.get_value(ЗАДАНИЕ, {"lesson": lesson}, ПОЛЯ_ЗАДАНИЯ, as_dict=True)


def заблокировать_задание(имя: str) -> frappe._dict | None:
	"""Задание под блокировкой — перед вставкой новой сдачи по нему.

	`Why:` публикация релиза удаляет шаблон без сдач и снимает шаблон со
	сдачами (`releases.homework._снять`) под той же блокировкой. Блокирующее
	чтение видит последнее зафиксированное: удалённое или снятое задание новой
	сдачи не получает, и сдача без задания не появляется. При снимочной
	изоляции MariaDB проигравший получает взаимоблокировку или «запись
	изменилась» и откатывается.
	"""
	return frappe.db.get_value(ЗАДАНИЕ, имя, ["name", "retired"], as_dict=True, for_update=True)


def снять_сроки_назначений(задание: str) -> None:
	"""Сроки задания в назначениях курса уходят вместе с заданием (learning-services#452).

	`Why:` строка назначения ссылается на задание, и без уборки удаление
	упало бы `LinkExistsError`. Срок назначения — не след ученика: сдач по
	заданию нет, выставлять его больше некому.
	"""
	frappe.db.delete("Course Allocation Homework Due", {"homework": задание})


def найти_сдачу(
	homework: str, ученик: str, организация: str | None, *, for_update: bool = False
) -> str | None:
	return frappe.db.get_value(
		СДАЧА,
		{"homework": homework, "member": ученик, "organization": организация or ("is", "not set")},
		"name",
		for_update=for_update,
	)


def срок(задание, от: datetime) -> datetime | None:
	"""Срок по правилу — задания или строки назначения. Абсолютный — конец дня
	по времени платформы."""
	if задание.due_mode == "absolute" and задание.due_date:
		return datetime.combine(getdate(задание.due_date), time(23, 59, 59))
	if задание.due_mode == "relative" and задание.due_days:
		return от + timedelta(days=int(задание.due_days))
	return None


def назначения_организации(
	ученик: str, курс: str, организация: str, назначения: list | None = None
) -> list[str]:
	"""Назначения курса ученику, которые организация сделала сама (learning-services#627).

	Назначение, взятое участником из каталога команды (`chosen_by_member`), сюда
	не входит: результат по такому курсу никто не ждёт. `назначения` — все
	назначения ученика, если вызывающий их уже прочитал (`access.назначения_ученика`).
	"""
	# Why: импорт внутри — модуль назначения импортирует этот модуль на уровне
	# модуля, и встречный импорт здесь замкнул бы цикл.
	from lms_frappe_app.agent_learning.doctype.course_allocation.course_allocation import (
		назначения_пользователя,
	)

	if назначения is None:
		назначения = назначения_пользователя(ученик, course=курс)
	return [
		назначение.name
		for назначение in назначения
		if назначение.course == курс
		and назначение.organization == организация
		and not назначение.chosen_by_member
	]


def _действующие_правила(
	задание, ученик: str, организация: str | None, курс: str | None = None, назначения: list | None = None
) -> tuple[bool, list, str | None]:
	"""Обязательна ли сдача, правила её срока и их источник (learning-services#627).

	Обязательная — сдача в пространстве организации, которая сама назначила
	ученику курс: там результат ждут. Её срок — правила этих назначений, иначе
	автора. Всё остальное — практика без срока: личное пространство (и курс,
	назначенный компанией, но пройденный лично) и курс, взятый участником из
	каталога команды самим. `Why:` срок ставит тот, кто ждёт результат, —
	человеку, который зашёл разобраться в теме, срок и «сдай» навязаны.

	`курс` и `назначения` — курс урока задания и назначения ученика, если
	вызывающий их уже прочитал.
	"""
	from lms_frappe_app.agent_learning.doctype.course_allocation.course_allocation import СРОКИ_ДОМАШЕК

	if not организация:
		return False, [], None
	курс = курс or frappe.db.get_value("Course Lesson", задание.lesson, "course")
	назначения = назначения_организации(ученик, курс, организация, назначения) if курс else []
	if not назначения:
		return False, [], None
	правила = frappe.get_all(
		СРОКИ_ДОМАШЕК,
		filters={"parent": ("in", назначения), "parenttype": "Course Allocation", "homework": задание.name},
		fields=["due_mode", "due_days", "due_date"],
	)
	if правила:
		return True, правила, "allocation"
	return True, [задание], "author"


def условия(
	задание,
	ученик: str,
	организация: str | None,
	*,
	обязательна: bool | None = None,
	курс: str | None = None,
	назначения: list | None = None,
) -> tuple[bool, list, str | None]:
	"""Обязательность сдачи, правила срока и их источник — с учётом уже заведённой сдачи.

	`обязательна` — признак, записанный на сдаче: он не пересчитывается задним
	числом. Практика остаётся практикой, а обязательная, у которой назначение
	с тех пор сняли, берёт срок автора. Назначений у ученика бывает несколько —
	из правил берётся ближайший срок, как дедлайн курса в
	`access._условия_по_курсам`.
	"""
	if обязательна is False:
		return False, [], None
	живая, правила, источник = _действующие_правила(задание, ученик, организация, курс, назначения)
	if обязательна and not живая:
		return True, [задание], "author"
	return живая, правила, источник


def _ближайший(правила, от: datetime) -> datetime | None:
	сроки = [с for с in (срок(п, от) for п in правила) if с]
	return min(сроки) if сроки else None


def _организация_записи(запись) -> str | None:
	"""Пространство занятия; у попытки квиза — через её занятие."""
	if запись.doctype == "Agent Quiz Attempt":
		return frappe.db.get_value("Agent Learning Session", запись.session, "organization") or None
	return getattr(запись, "organization", None) or None


def _вставить(документ):
	"""Вставить новую сдачу — или взять ту, что уже вставил параллельный вызов.

	Отдаёт пару «сдача, вставлена ли эта». Дубль ловит уникальный индекс
	(`install.обеспечить_индекс_домашек`); откат к точке снимает только эту
	вставку, и сдача перечитывается с блокировкой.

	`Why:` перечитывание помогает, только когда чужая сдача видна снимку этой
	транзакции — дубль в ней же или сдача, зафиксированная до её начала. При
	настоящей гонке MariaDB стенда (`innodb_snapshot_isolation`) отвечает на
	блокирующее чтение новой строки взаимоблокировкой, и она откатывает
	транзакцию целиком: `submit_homework` отдаёт `busy`, фоновая выдача
	повторяет себя (`выдать_по_записи`).
	"""
	frappe.db.savepoint(ТОЧКА_ВСТАВКИ)
	try:
		документ.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		frappe.db.rollback(save_point=ТОЧКА_ВСТАВКИ)
		имя = найти_сдачу(документ.homework, документ.member, документ.organization, for_update=True)
		if not имя:
			raise
		# Сообщение Frappe «must be unique» ответ не несёт: дубль разрешён.
		frappe.clear_last_message()
		return frappe.get_doc(СДАЧА, имя, for_update=True), False
	frappe.db.release_savepoint(ТОЧКА_ВСТАВКИ)
	return документ, True


def _отметить_выдачу(документ, задание, ученик: str, сейчас: datetime) -> None:
	документ.assigned_at = сейчас
	# Why: признак сдачи, заведённой сохранением до выдачи, не пересчитывается —
	# как и срок: практика не становится обязательной задним числом.
	обязательна, правила, источник = условия(
		задание, ученик, документ.organization, обязательна=bool(документ.required) if документ.name else None
	)
	документ.required = обязательна
	if документ.due_at:
		# Why: срок, выставленный до выдачи, не пересчитывается — и его источник
		# тоже: правило могли снять, и пересчёт приписал бы срок автору.
		источник = _источник_срока(документ)
	else:
		документ.due_at = _ближайший(правила, сейчас)
	документ.append(
		"history",
		{
			"event": "assigned",
			"by_user": ученик,
			"at": сейчас,
			"due_at": документ.due_at,
			"due_source": источник if документ.due_at else None,
		},
	)


def _источник_срока(документ) -> str | None:
	"""Источник выставленного срока — из журнала, где его записали вместе со сроком."""
	for строка in reversed(документ.history):
		if строка.due_source:
			return строка.due_source
	return None


def выдать(запись) -> None:
	"""Выдать домашку урока при его закрытии — занятием или сданным квизом.

	Повторное закрытие и закрытие после сохранения ничего не ломают: уже
	выданная сдача не трогается, а сохранённая до закрытия получает время
	выдачи и, если она обязательная, относительный срок. `Why:` сроки не
	пересчитываются задним числом — ученик не получает просрочку за чужую
	правку правила.

	Сдачу, вставленную параллельным сохранением, выдача берёт и отмечает, а не
	падает. `Why:` выдача идёт внутри закрытия урока и зачёта квиза — они не
	должны срываться из-за домашки.

	Задание, снятое из релиза (`retired`), не выдаётся: его сдачи — только
	уже выданные.
	"""
	задание = задание_урока(запись.lesson)
	if not задание or задание.retired:
		return
	организация = _организация_записи(запись)
	сейчас = now_datetime()
	if имя := найти_сдачу(задание.name, запись.student, организация):
		документ = frappe.get_doc(СДАЧА, имя, for_update=True)
	else:
		заблокированное = заблокировать_задание(задание.name)
		if not заблокированное or заблокированное.retired:
			return
		новая = frappe.get_doc(
			{
				"doctype": СДАЧА,
				"homework": задание.name,
				"lesson": запись.lesson,
				"member": запись.student,
				"organization": организация,
				"status": ДОМАШКА_ВЫДАНА,
				"version": 0,
			}
		)
		_отметить_выдачу(новая, задание, запись.student, сейчас)
		документ, вставлена = _вставить(новая)
		if вставлена:
			return
	if документ.assigned_at:
		return
	_отметить_выдачу(документ, задание, запись.student, сейчас)
	документ.save(ignore_permissions=True)


#: Сколько раз фоновая выдача повторяет себя после гонки за сдачу.
ПОВТОРОВ_ВЫДАЧИ = 2


def выдать_по_записи(doctype: str, name: str) -> None:
	"""Фоновая выдача по занятию или попытке квиза — после коммита их закрытия.

	Гонка за сдачу (параллельное сохранение, второе закрытие) даёт дубль на
	вставке или взаимоблокировку — MariaDB со снимочной изоляцией отдаёт так и
	блокирующее чтение строки, появившейся после снимка. Обе откатывают
	транзакцию: повтор идёт с нового снимка, где чужая сдача уже видна. Не
	вышло и после повторов — в лог: урок закрыт, а сдачу ученик заведёт
	сохранением сам.
	"""
	for попытка in range(ПОВТОРОВ_ВЫДАЧИ + 1):
		try:
			выдать(frappe.get_doc(doctype, name))
			return
		except (frappe.QueryDeadlockError, frappe.UniqueValidationError, frappe.DuplicateEntryError):
			frappe.db.rollback()
			if попытка == ПОВТОРОВ_ВЫДАЧИ:
				frappe.log_error(title="Домашка не выдана", reference_doctype=doctype, reference_name=name)


def _новая_сдача(задание, ученик: str, организация: str | None, сейчас: datetime):
	# Why: абсолютный срок известен сразу, относительный — только при выдаче.
	# Среди действующих правил есть относительное — срок ставит выдача:
	# ближайшим он может оказаться только тогда.
	обязательна, правила, источник = условия(задание, ученик, организация)
	абсолютные = all(п.due_mode == "absolute" for п in правила)
	документ = frappe.get_doc(
		{
			"doctype": СДАЧА,
			"homework": задание.name,
			"lesson": задание.lesson,
			"member": ученик,
			"organization": организация,
			"status": ДОМАШКА_СДАНА,
			"version": 0,
			"required": обязательна,
			"due_at": _ближайший(правила, сейчас) if абсолютные else None,
		}
	)
	# Источник срока пишется в журнал первым сохранением (`сохранить`).
	документ.flags.due_source = источник if документ.due_at else None
	return документ


def _ответ(документ, answer: str | None, новые: Sequence[tuple[str, bytes]], убрать: Sequence[str]):
	"""Текст и прежние файлы нового ответа — или отказ, до первой записи."""
	if документ.status == ДОМАШКА_ПРИНЯТА:
		raise Отказ(УЖЕ_ПРИНЯТА, _("This homework has already been accepted and can no longer be edited"), submission=документ.name)
	текст = (документ.answer or "") if answer is None else answer.strip()
	убираемые = set(убрать)
	файлы = [строка.file for строка in документ.files if строка.file not in убираемые]
	if len(файлы) + len(новые) > ПРЕДЕЛ_ФАЙЛОВ:
		raise Отказ(СЛИШКОМ_МНОГО_ФАЙЛОВ, _("An answer can have at most {0} files").format(ПРЕДЕЛ_ФАЙЛОВ), limit=ПРЕДЕЛ_ФАЙЛОВ)
	if not текст and not файлы and not новые:
		raise Отказ(ПУСТОЙ_ОТВЕТ, _("The answer is empty: add text or a file"))
	return текст, файлы


def сохранить(
	ученик: str,
	lesson: str,
	организация: str | None,
	*,
	answer: str | None = None,
	новые: Sequence[tuple[str, bytes]] = (),
	убрать: Sequence[str] = (),
):
	"""Сохранить ответ ученика: одна версия на вызов, статус — «сдана».

	`answer=None` оставляет текст как был: окно файлов и сдача текста через
	агента меняют каждое своё. Неполный ответ (`text_and_files` без файлов)
	принимается — оценки нет, его видит и возвращает куратор. Доступ к курсу и
	пространство проверяет вызывающий метод.

	Ограничение (learning-services#439): каждое сохранение перезаписывает все
	строки `versions` и `history` — так `save()` Frappe пишет дочерние таблицы,
	и цена растёт с числом версий. `Why:` версий у домашки единицы-десятки, а
	второй путь записи мимо `save()` пришлось бы держать в согласии с журналом.
	Станет дорого — вставлять новые строки `db_insert`, а не через родителя.
	"""
	задание = задание_урока(lesson)
	имя = найти_сдачу(задание.name, ученик, организация) if задание else None
	# Why: задание, снятое из релиза (`retired`), живёт ради уже выданных
	# сдач — новой сдачи по нему не заводят, для ученика без сдачи его нет.
	if not задание or (задание.retired and not имя):
		raise Отказ(ЗАДАНИЯ_НЕТ, _("This lesson has no homework"), lesson=lesson)
	текст_можно = задание.answer_mode in ("text", "text_and_files")
	файлы_можно = задание.answer_mode in ("files", "text_and_files")
	if answer and answer.strip() and not текст_можно:
		raise Отказ(НЕ_ТОТ_ВИД_ОТВЕТА, _("This homework is submitted as files"), answer_mode=задание.answer_mode)
	if новые and not файлы_можно:
		raise Отказ(НЕ_ТОТ_ВИД_ОТВЕТА, _("This homework is submitted as text"), answer_mode=задание.answer_mode)
	if answer and len(answer) > ДЛИНА_ОТВЕТА:
		raise Отказ(
			ДЛИННЫЙ_ОТВЕТ, _("The answer text is too long"), limit=ДЛИНА_ОТВЕТА, length=len(answer)
		)
	предел = файлы_платформы.предел_байт()
	for имя_файла, данные in новые:
		# Why: пустой или битый base64 приходит пустыми байтами — выброшенный
		# молча, файл пропал бы из ответа, а ученик считал бы его сданным.
		if not данные:
			raise Отказ(ФАЙЛ_ПУСТ, _("The file is missing or empty"), file=имя_файла)
		if len(данные) > предел:
			raise Отказ(ФАЙЛ_ВЕЛИК, _("The file is larger than allowed"), file=имя_файла, limit_mb=предел // (1024 * 1024))
		if not файлы_платформы.тип_принят(имя_файла, данные):
			допустимые = sorted(файлы_платформы.ТИПЫ_ДОМАШКИ)
			raise Отказ(
				ТИП_НЕ_ПРИНЯТ,
				_(
					"This file can't be submitted: its type isn't on the list or its content doesn't match the type. Accepted: {0}"
				).format(", ".join(допустимые)),
				file=имя_файла,
				allowed=допустимые,
			)
	if sum(len(данные) for _, данные in новые) > ПРЕДЕЛ_СОХРАНЕНИЯ_МБ * 1024 * 1024:
		raise Отказ(
			ОТВЕТ_ВЕЛИК,
			_("At most {0} MB of files per save").format(ПРЕДЕЛ_СОХРАНЕНИЯ_МБ),
			limit_mb=ПРЕДЕЛ_СОХРАНЕНИЯ_МБ,
		)
	проверить_квоту(ученик, sum(len(данные) for _, данные in новые))

	сейчас = now_datetime()
	if имя:
		документ = frappe.get_doc(СДАЧА, имя, for_update=True)
	else:
		заблокированное = заблокировать_задание(задание.name)
		if not заблокированное or заблокированное.retired:
			raise Отказ(ЗАДАНИЯ_НЕТ, _("This lesson has no homework"), lesson=lesson)
		документ = _новая_сдача(задание, ученик, организация, сейчас)
	новая = not документ.name
	текст, файлы = _ответ(документ, answer, новые, убрать)

	# Все отказы, кроме отказов Frappe на файл (`file_rejected`, `file_too_large`
	# по его пределу), — выше, до первой записи. Отклонённый файл откатывает
	# сохранение целиком. `Why:` `@контракт` отдаёт отказ успешным ответом, и
	# транзакция запроса коммитится — без точки сохранения осталась бы новая
	# сдача без версии и файлы, принятые до отклонённого.
	frappe.db.savepoint(ТОЧКА_СОХРАНЕНИЯ)
	# Why: файл привязывается к записи по имени — вставленный раньше неё остаётся
	# без привязки (#343). Не `is_new()`: у записи из `frappe.get_doc({...})`
	# нет `__islocal`, и проверка отвечает «не новая».
	if not документ.name:
		документ, вставлена = _вставить(документ)
		if not вставлена:
			# Сдачу вставил параллельный вызов: ответ собирается на ней заново.
			текст, файлы = _ответ(документ, answer, новые, убрать)
	вложенные = []
	имя_файла = None
	try:
		for имя_файла, данные in новые:
			файлы.append(_вложить_файл(документ, имя_файла, данные, вложенные).name)
	except Отказ:
		_откатить(вложенные)
		raise
	except MaxFileSizeReachedError as ошибка:
		_откатить(вложенные)
		# Why: предел Frappe (`max_file_size`) бывает меньше нашего.
		raise Отказ(
			ФАЙЛ_ВЕЛИК,
			_("The file is larger than allowed"),
			file=имя_файла,
			limit_mb=get_max_file_size() // (1024 * 1024),
		) from ошибка
	except frappe.ValidationError as ошибка:
		_откатить(вложенные)
		# Why: запрещённый тип Frappe отдаёт 417 без кода — агенту нужен код контракта.
		raise Отказ(ФАЙЛ_ОТКЛОНЁН, _("This file can't be submitted"), file=имя_файла) from ошибка
	frappe.db.release_savepoint(ТОЧКА_СОХРАНЕНИЯ)

	документ.answer = текст
	документ.set("files", [{"file": ф} for ф in файлы])
	документ.version = (документ.version or 0) + 1
	документ.status = ДОМАШКА_СДАНА
	документ.submitted_at = сейчас
	документ.append(
		"versions",
		{"version": документ.version, "saved_at": сейчас, "answer": текст, "files": json.dumps(файлы)},
	)
	строка = {"event": "submitted", "by_user": ученик, "at": сейчас, "version": документ.version}
	if новая and документ.due_at and документ.flags.due_source:
		# Why: срок, выставленный первым сохранением, получает источник в журнале,
		# как у выдачи: выдача потом берёт его отсюда (learning-services#452).
		строка.update(due_at=документ.due_at, due_source=документ.flags.due_source)
	документ.append("history", строка)
	документ.save(ignore_permissions=True)
	return документ


def _вложить_файл(документ, имя: str, данные: bytes, вложенные: list):
	"""Приватный `File`, привязанный к сдаче: права на него Frappe берёт у сдачи.

	Файл попадает в `вложенные` до вставки: `File.before_insert` пишет байты на
	диск раньше, чем вставка может упасть.
	"""
	файл = файлы_платформы.новый_файл(документ, имя or "file", данные)
	# Квоту всего сохранения `сохранить` проверил до первой записи.
	файл.flags[КВОТА_ПРОВЕРЕНА] = True
	вложенные.append(файл)
	return файл.insert(ignore_permissions=True)


def _откатить(вложенные: list) -> None:
	"""Откат сохранения к точке и байты его файлов — с диска.

	`Why:` откат к точке сохранения не зовёт `frappe.db.after_rollback`, а
	`File.before_insert` уже записал байты: без уборки они остались бы на диске
	без записи о них. `on_rollback` удаляет файл, только если на то же
	содержимое не ссылается другой `File` (`File._delete_file_on_disk`), — общий
	файл прежней версии цел. Зовётся после отката: откаченные записи о файлах
	уже не считаются ссылками.
	"""
	frappe.db.rollback(save_point=ТОЧКА_СОХРАНЕНИЯ)
	for файл in вложенные:
		if файл.flags.new_file:
			файл.on_rollback()


# --- проверка куратором (learning-services#452) ---

НЕ_ТОТ_СТАТУС = "wrong_status"
НУЖЕН_КОММЕНТАРИЙ = "comment_required"
ВЕРСИЯ_УСТАРЕЛА = "stale_version"

#: Действие куратора: из статуса, в статус, событие журнала, нужен ли комментарий.
ПЕРЕХОДЫ = {
	"accept": (ДОМАШКА_СДАНА, ДОМАШКА_ПРИНЯТА, "accepted", False),
	"send_back": (ДОМАШКА_СДАНА, ДОМАШКА_ВОЗВРАЩЕНА, "returned", True),
	"reopen": (ДОМАШКА_ПРИНЯТА, ДОМАШКА_ВОЗВРАЩЕНА, "reopened", True),
}
#: События, после которых версия считается проверенной.
СОБЫТИЯ_ПРОВЕРКИ = ("returned", "accepted", "reopened")
#: События с комментарием куратора — что ученику доделать.
СОБЫТИЯ_С_КОММЕНТАРИЕМ = ("returned", "reopened")


def проверить(куратор: str, имя: str, действие: str, version: int, comment: str | None = None):
	"""Принять, вернуть или отменить приём. Права проверяет вызывающий метод.

	`version` — версия, которую куратор видел: ученик успел сохранить новую —
	отказ, а не приём непрочитанного. Все отказы — до первой записи: `@контракт`
	коммитит транзакцию и при отказе.
	"""
	из_статуса, в_статус, событие, нужен = ПЕРЕХОДЫ[действие]
	текст = (comment or "").strip()
	if нужен and not текст:
		raise Отказ(НУЖЕН_КОММЕНТАРИЙ, _("Write what needs to be changed"), submission=имя)
	документ = frappe.get_doc(СДАЧА, имя, for_update=True)
	if not документ.member:
		# Why: сброс прогресса архивирует сдачу, пока куратор на неё смотрит.
		raise Отказ(НЕ_ТОТ_СТАТУС, _("This submission is no longer active"), submission=имя, status=документ.status)
	if int(version) != (документ.version or 0):
		raise Отказ(
			ВЕРСИЯ_УСТАРЕЛА,
			_("The student has saved a new version — take a look at it"),
			submission=имя,
			version=документ.version,
		)
	if документ.status != из_статуса:
		raise Отказ(НЕ_ТОТ_СТАТУС, _("This submission has already been reviewed"), submission=имя, status=документ.status)
	документ.status = в_статус
	документ.append(
		"history",
		{
			"event": событие,
			"by_user": куратор,
			"at": now_datetime(),
			"version": документ.version,
			"comment": текст or None,
		},
	)
	документ.save(ignore_permissions=True)
	return документ


# --- представление ---


def адреса_уроков(курс: str) -> dict[str, str]:
	"""Адреса уроков курса в SPA: `/lms/courses/<курс>/learn/<глава>-<урок>`.

	`Why:` порядок глав и уроков стоит запросов на каждую главу — перечень
	домашек платит его раз на курс, а не на сдачу.
	"""
	адреса: dict[str, str] = {}
	for номер_главы, глава in enumerate(уроки_по_главам(курс), 1):
		for номер_урока, урок in enumerate(глава["lessons"], 1):
			адреса.setdefault(урок, f"/lms/courses/{курс}/learn/{номер_главы}-{номер_урока}")
	return адреса


def адрес_урока(lesson: str, курс: str) -> str | None:
	return адреса_уроков(курс).get(lesson)


def уроки_с_курсом(уроки) -> dict[str, frappe._dict]:
	"""Название и курс каждого урока — одним запросом. Курс — через главу, как
	у `курс_урока`."""
	if not уроки:
		return {}
	урок = frappe.qb.DocType("Course Lesson")
	глава = frappe.qb.DocType("Course Chapter")
	строки = (
		frappe.qb.from_(урок)
		.left_join(глава)
		.on(глава.name == урок.chapter)
		.select(урок.name, урок.title, глава.course)
		.where(урок.name.isin(list(уроки)))
	).run(as_dict=True)
	return {строка.name: строка for строка in строки}


def _последние_события(сдачи: list[str], события: Sequence[str], поле: str) -> dict:
	"""`поле` последней строки журнала с одним из `события` у каждой сдачи — одной выборкой."""
	if not сдачи:
		return {}
	значения: dict = {}
	for строка in frappe.get_all(
		"Agent Homework Event",
		filters={
			"parenttype": СДАЧА,
			"parentfield": "history",
			"parent": ("in", сдачи),
			"event": ("in", list(события)),
		},
		fields=["parent", поле],
		order_by="idx asc",
	):
		значения[строка.parent] = строка[поле]
	return значения


def названия(doctype: str, имена, поле: str = "title") -> dict[str, str]:
	"""`поле` записей по имени — одной выборкой; пустые имена отбрасываются."""
	имена = [и for и in имена if и]
	if not имена:
		return {}
	return dict(frappe.get_all(doctype, filters={"name": ("in", имена)}, fields=["name", поле], as_list=True))


def последние_комментарии(сдачи: list[str]) -> dict[str, str | None]:
	"""Комментарий последнего возврата или отмены приёма каждой сдачи — одной выборкой.

	То же, что `последний_комментарий`, но без чтения сдач целиком.
	"""
	return _последние_события(сдачи, СОБЫТИЯ_С_КОММЕНТАРИЕМ, "comment")


def проверенные_версии(сдачи: list[str]) -> dict[str, int]:
	"""Последняя проверенная версия каждой сдачи — одной выборкой. Не проверяли — сдачи нет в ответе."""
	return _последние_события(сдачи, СОБЫТИЯ_ПРОВЕРКИ, "version")


def _правило_срока(правила, от: datetime):
	"""Правило, которое даёт ближайший срок; ни одно не даёт — `None`."""
	сроки = [(с, п) for п in правила if (с := срок(п, от))]
	return min(сроки, key=lambda пара: пара[0])[1] if сроки else None


def описание_задания(
	задание,
	ученик: str | None,
	организация: str | None,
	*,
	обязательна: bool | None = None,
	курс: str | None = None,
	назначения: list | None = None,
) -> dict:
	"""Задание для ответа: `required` и `due` — то, что действует для этого ученика
	в этом пространстве (`условия`), а не правило автора. У практики `due.mode` —
	`none`. `обязательна` — признак уже заведённой сдачи.

	`Why:` правило автора агент назвал бы и там, где срок дало назначение, и там,
	где срока нет вовсе (learning-services#627).
	"""
	обязательна, правила, _источник = условия(
		задание, ученик, организация, обязательна=обязательна, курс=курс, назначения=назначения
	)
	правило = _правило_срока(правила, now_datetime())
	режим = правило.due_mode if правило else "none"
	return {
		"lesson": задание.lesson,
		"title": задание.title,
		"description": задание.description,
		"answer_mode": задание.answer_mode,
		"required": обязательна,
		"due": {
			"mode": режим,
			"days": int(правило.due_days) if режим == "relative" else None,
			"date": str(getdate(правило.due_date)) if режим == "absolute" else None,
		},
	}


def признак_сдачи(задание: str, ученик: str, организация: str | None) -> bool | None:
	"""`required` сдачи ученика по заданию в пространстве; сдачи нет — `None`."""
	значение = frappe.db.get_value(
		СДАЧА,
		{"homework": задание, "member": ученик, "organization": организация or ("is", "not set")},
		"required",
	)
	return None if значение is None else bool(значение)


def к_закрытию(ученик: str, lesson: str, курс: str, организация: str | None) -> dict | None:
	"""Задание закрытого урока — для ответа о зачёте и закрытии (learning-services#627).

	`Why:` агент озвучивает задание первым шагом закрытия, а ответ старта урока
	к концу урока уже не держит. Снятое из релиза (`retired`) — `None`: новым
	его не выдают.
	"""
	задание = задание_урока(lesson)
	if not задание or задание.retired:
		return None
	return описание_задания(
		задание, ученик, организация, обязательна=признак_сдачи(задание.name, ученик, организация), курс=курс
	)


def _файлы(имена: list[str], сведения: dict[str, dict]) -> list[dict]:
	"""Файлы ответа по порядку: `id` — имя `File` (для `remove_files`), `name` — имя файла."""
	return [{"id": имя, **сведения[имя]} for имя in имена if имя in сведения]


def подпись_куратора() -> str:
	"""Как назвать в журнале куратора без имени в профиле — на языке запроса."""
	return _("Curator")


def подпись_ученика() -> str:
	"""Как назвать в журнале куратора ученика без имени в профиле — на языке
	запроса. Контекст — у `Student` есть перевод Learning («Студент»), и общий
	ключ переопределил бы его по всей платформе."""
	return _("Student", context="Homework history")


def _автор_события(user: str | None, читатель: str, ученик: str | None = None) -> dict:
	"""`by` и `by_name` строки журнала для `читатель`: имя из профиля — у
	события с автором, правило — `автор_события`."""
	имя = frappe.get_cached_value("User", user, "full_name") if user else None
	return автор_события(user, имя, читатель, ученик)


def автор_события(user: str | None, имя: str | None, читатель: str, ученик: str | None = None) -> dict:
	"""`by` и `by_name` строки журнала без базы: `имя` — `full_name` автора.

	`Why:` журнал читает ученик — на странице урока и через агента, и почта
	куратора ему ни к чему (learning-services#439). Почта остаётся только у
	своих событий: по ней SPA пишет «Вы». Остальным — имя, без имени — «Куратор»,
	а события `ученик` сдачи — «Ученик»: журнал читает и куратор, и почту
	ученика ему тоже не отдаём (learning-services#452).
	"""
	if not user:
		return {"by": None, "by_name": None}
	if user == читатель:
		return {"by": user, "by_name": имя or user}
	return {"by": None, "by_name": имя or (подпись_ученика() if user == ученик else подпись_куратора())}


def _время(значение) -> str | None:
	"""Метка времени, как во всём контракте: ISO, без зоны, в поясе сайта."""
	return get_datetime(значение).isoformat() if значение else None


def просрочена(документ) -> bool:
	"""Просрочка — признак, а не статус: срок прошёл, а от ученика ждут действия."""
	return (
		bool(документ.due_at)
		and документ.status in (ДОМАШКА_ВЫДАНА, ДОМАШКА_ВОЗВРАЩЕНА)
		and get_datetime(документ.due_at) < now_datetime()
	)


def описание_сдачи(
	документ, *, полное: bool = True, с_версиями: bool = False, читатель: str | None = None
) -> dict:
	"""Сдача для ответа. Коротко — для старта урока и списка.

	`читатель` — кому отдаётся журнал (по умолчанию пользователь сессии): от
	него зависит, чья почта в `history` видна (`_автор_события`).
	"""
	короткое = {
		"id": документ.name,
		"status": документ.status,
		"required": bool(документ.required),
		"due_at": _время(документ.due_at),
		"overdue": просрочена(документ),
		"version": документ.version,
	}
	if not полное:
		return короткое
	имена = [с.file for с in документ.files]
	ученик = документ.member or документ.archived_student
	версии = [(в, json.loads(в.files or "[]")) for в in документ.versions] if с_версиями else []
	# Файлы ответа и всех версий — одним запросом, а не запросом на версию.
	сведения = файлы_платформы.сведения_о_файлах(list({*имена, *(и for _, файлы in версии for и in файлы)}))
	ответ = {
		**короткое,
		"assigned_at": _время(документ.assigned_at),
		"submitted_at": _время(документ.submitted_at),
		"answer": документ.answer or "",
		"files": _файлы(имена, сведения),
		"history": [
			{
				"event": с.event,
				**_автор_события(с.by_user, читатель or frappe.session.user, ученик),
				"at": _время(с.at),
				"version": с.version or None,
				"comment": с.comment or None,
				"due_at": _время(с.due_at),
			}
			for с in документ.history
		],
	}
	if с_версиями:
		ответ["versions"] = [
			{
				"version": в.version,
				"saved_at": _время(в.saved_at),
				"answer": в.answer or "",
				"files": _файлы(файлы, сведения),
			}
			for в, файлы in версии
		]
	return ответ


def последний_комментарий(документ) -> str | None:
	"""Комментарий последнего возврата на доработку или отмены приёма."""
	for строка in reversed(документ.history):
		if строка.event in СОБЫТИЯ_С_КОММЕНТАРИЕМ:
			return строка.comment
	return None


def проверенная_версия(документ) -> int | None:
	"""Версия, которую куратор проверял последней: с ней карточка сравнивает текущую."""
	for строка in reversed(документ.history):
		if строка.event in СОБЫТИЯ_ПРОВЕРКИ:
			return строка.version
	return None


def задания_курса(курс: str) -> dict[str, frappe._dict]:
	"""Задания уроков курса по уроку — одним запросом, сразу всеми полями."""
	задание = frappe.qb.DocType(ЗАДАНИЕ)
	урок = frappe.qb.DocType("Course Lesson")
	строки = (
		frappe.qb.from_(задание)
		.join(урок)
		.on(урок.name == задание.lesson)
		.select(*(задание[поле] for поле in ПОЛЯ_ЗАДАНИЯ))
		.where(урок.course == курс)
	).run(as_dict=True)
	return {строка.lesson: строка for строка in строки}


def для_старта(
	ученик: str, lesson: str, курс: str, организация: str | None, назначения: list | None = None
) -> dict:
	"""`homework` и `previous_homework` для `start_lesson`; `назначения` — все
	назначения ученика, прочитанные стартом.

	Прошлый урок — предыдущий по порядку курса, независимо от того, есть ли
	задание у текущего. Сдача — в пространстве занятия.
	"""
	# Why: порядок уроков стоит запросов на каждую главу, а на каждом старте его
	# не платим (student.py, `_место_урока`). У курса без заданий старт платит
	# один запрос — узнать, что их нет.
	задания = задания_курса(курс)
	if not задания:
		return {"homework": None, "previous_homework": None}
	текущее = задания.get(lesson)
	признак = признак_сдачи(текущее.name, ученик, организация) if текущее else None
	# Why: снятое из релиза задание (`retired`) видит только ученик, у которого
	# по нему уже есть сдача: новым его не выдают (`выдать`, `сохранить`).
	if текущее and текущее.retired and признак is None:
		текущее = None
	прошлое = None
	уроки = уроки_курса(курс)
	номер = уроки.index(lesson) if lesson in уроки else -1
	задание = задания.get(уроки[номер - 1]) if номер > 0 else None
	имя = найти_сдачу(задание.name, ученик, организация) if задание else None
	# Прошлое снятое задание без сдачи — так же нет: сдать его ученик не сможет.
	if задание and (имя or not задание.retired):
		сдача = frappe.get_doc(СДАЧА, имя) if имя else None
		прошлое = {
			"lesson": задание.lesson,
			"title": задание.title,
			"submission": описание_сдачи(сдача, полное=False, читатель=ученик) if сдача else None,
			"last_comment": последний_комментарий(сдача) if сдача else None,
		}
	# Why: описание задания текущего урока короткое — отдаём целиком. Ответ
	# ученика по прошлому уроку не отдаём: он длинный, его читает
	# `my_homework(lesson=…)`.
	return {
		"homework": (
			описание_задания(
				текущее, ученик, организация, обязательна=признак, курс=курс, назначения=назначения
			)
			if текущее
			else None
		),
		"previous_homework": прошлое,
	}
