# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Прохождение урока: создание, сверка с релизом, статусы (learning-services#504).

Прохождение одно на «ученик, курс, ключ урока» и держит пункты и цели урока
по ключам релиза. Ученик всегда на действующем релизе: прохождение, сверенное
с прошлым, сверяется при обращении и фоном после публикации нового.

Записи — без проверки прав: прохождение не видят ни ученик, ни руководитель,
а пишут его только методы контракта, которые проверяют доступ к курсу сами.
Запись читает прохождение с блокировкой (`for_update`): агенты шлют вызовы
параллельно, и второй `save` упал бы на устаревшем `modified`.
"""

import frappe

from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.releases import index

ПРОХОЖДЕНИЕ = "Agent Lesson Run"
УРОК_НЕ_В_РЕЛИЗЕ = "lesson_not_in_release"
ТОЧКА_ВСТАВКИ = "lesson_run_insert"
ТОЧКА_СВЕРКИ = "lesson_run_reconcile"

ОТКРЫТ = "open"
#: Статусы пункта, которые закрывают его для цели: разобран или отложен на потом.
ЗАКРЫВАЮТ_ЦЕЛЬ = frozenset({"done", "planned"})
ПРОЙДЕН = "passed"


def прохождение(ученик: str, курс: str, ключ_урока: str):
	"""Прохождение урока учеником — найденное или новое, сверенное с действующим релизом.

	Новое заводится только на урок действующего релиза: иначе — отказ
	`lesson_not_in_release`. Уже заведённое читается и тогда, когда урок из
	релиза исчез.
	"""
	if имя := _найти(ученик, курс, ключ_урока):
		run = frappe.get_doc(ПРОХОЖДЕНИЕ, имя, for_update=True)
		сверить(run)
		return run
	релиз = _действующий(курс)
	урок = index.урок(релиз, ключ_урока) if релиз else None
	if not урок:
		raise Отказ(
			УРОК_НЕ_В_РЕЛИЗЕ,
			"Урока нет в действующем релизе курса",
			course=курс,
			lesson_key=ключ_урока,
			release=релиз,
		)
	run = frappe.get_doc(
		{"doctype": ПРОХОЖДЕНИЕ, "student": ученик, "course": курс, "lesson_key": ключ_урока}
	)
	_привести(run, релиз, урок, index.цели_урока(релиз, ключ_урока))
	return _вставить(run)


def сверить(run) -> bool:
	"""Пункты и цели прохождения — по действующему релизу курса; `True` — что-то поменялось.

	Новые пункты — `open`, новые цели — `not_started`. Исчезнувшие получают
	`removed`, отметка остаётся; вернувшийся ключ снимает `removed` с прежней
	отметкой. Цель пункта, вид, обязательность, название и порядок — из
	релиза. Урока в релизе нет — прохождение не трогается, и `release`
	остаётся прежним.

	`Why:` пункт уникален в уроке, а не в цели (формат релиза), поэтому сверка
	идёт по ключу пункта, и пункт может перейти к другой цели. Снятое не
	удаляется: автор, вернувший пункт, вернул бы ученику и его отметку.

	`run` прочитан с `for_update`; изменения сохраняются здесь же.
	"""
	релиз = _действующий(run.course)
	if not релиз or релиз == run.release:
		return False
	урок = index.урок(релиз, run.lesson_key)
	return _сверить(run, релиз, урок, index.цели_урока(релиз, run.lesson_key) if урок else [])


def статусы(run) -> None:
	"""Статусы целей — по пунктам прохождения, урока — по целям и `started_at`.

	Цель разобрана, когда все её обязательные пункты в `done` или `planned`;
	тронута — хотя бы один; иначе не начата. Цель без обязательных пунктов
	разобрана сразу. Снятые пункты и цели не в счёт. Урок не начат, пока пуст
	`started_at`; начатый — разобран, когда разобраны все цели.

	`Why:` «урок начат с первой отметки» — факт истории: `started_at` ставит
	первая отметка и не снимает ничто, и новый релиз, снявший отмеченный пункт,
	урок не «расначинает». `passed` ставит закрытие урока (квиз сдан или квиза
	нет), и пересчёт после нового релиза его не снимает: зачёт уже выдан.
	"""
	пункты = [п for п in run.goals if not п.removed]
	цели = [ц for ц in run.objectives if not ц.removed]
	for цель in цели:
		обязательные = [п for п in пункты if п.required and п.objective_key == цель.objective_key]
		закрыто = sum(п.status in ЗАКРЫВАЮТ_ЦЕЛЬ for п in обязательные)
		if закрыто == len(обязательные):
			цель.status = "covered"
		elif закрыто:
			цель.status = "touched"
		else:
			цель.status = "not_started"
	if run.status == ПРОЙДЕН:
		return
	if not run.started_at:
		run.status = "not_started"
	elif all(ц.status == "covered" for ц in цели):
		run.status = "covered"
	else:
		run.status = "in_progress"


def сверить_курс(курс: str) -> int:
	"""Живые прохождения курса — с действующим релизом; отдаёт, сколько поменялось.

	Ставится в фон публикацией релиза (`releases.service.опубликовать`).
	Архивные прохождения (`student` пуст после сброса) не сверяются: это
	история того, как ученик проходил урок, и новый релиз её не переписывает.
	Урок релиза и его цели читаются один раз на ключ урока.

	`Why:` каждое прохождение — своя транзакция. MariaDB стенда
	(`innodb_snapshot_isolation`) отвечает взаимоблокировкой на блокирующее
	чтение строки, которую после снимка задачи записал агент, и откатывает
	транзакцию целиком: одна общая транзакция теряла бы всю сверку из-за
	одного ученика и копила блокировки всех. Не сверенное здесь — гонка или
	сбой — уходит в лог и сверится при обращении (`прохождение`).

	`Why:` действующий релиз перечитывается перед каждым прохождением. Между
	коммитами могут опубликовать релиз новее, и агент сверит с ним
	прохождение раньше задачи: задача по старому релизу вернула бы его назад.
	Сменился релиз — задача останавливается: новая публикация поставила свою.
	"""
	релиз = _действующий(курс)
	if not релиз:
		return 0
	имена = frappe.get_all(
		ПРОХОЖДЕНИЕ,
		filters={"course": курс, "student": ("is", "set"), "release": ("!=", релиз)},
		pluck="name",
	)
	уроки: dict[str, tuple] = {}
	изменено = 0
	for имя in имена:
		if _действующий(курс) != релиз:
			break
		frappe.db.savepoint(ТОЧКА_СВЕРКИ)
		try:
			run = frappe.get_doc(ПРОХОЖДЕНИЕ, имя, for_update=True)
			if run.lesson_key not in уроки:
				урок = index.урок(релиз, run.lesson_key)
				уроки[run.lesson_key] = (урок, index.цели_урока(релиз, run.lesson_key) if урок else [])
			if run.release != релиз:
				изменено += _сверить(run, релиз, *уроки[run.lesson_key])
		except frappe.QueryDeadlockError:
			# Взаимоблокировка уже откатила транзакцию целиком — точки сохранения нет.
			frappe.db.rollback()
			frappe.log_error(
				title="Прохождение не сверено: гонка с агентом (learning-services#504)",
				reference_doctype=ПРОХОЖДЕНИЕ,
				reference_name=имя,
			)
		except frappe.DoesNotExistError:
			# Прохождение удалили после отбора — сверять нечего.
			frappe.db.rollback(save_point=ТОЧКА_СВЕРКИ)
			frappe.clear_last_message()
		except Exception:
			frappe.db.rollback(save_point=ТОЧКА_СВЕРКИ)
			frappe.log_error(
				title="Прохождение не сверено (learning-services#504)",
				reference_doctype=ПРОХОЖДЕНИЕ,
				reference_name=имя,
			)
		else:
			frappe.db.release_savepoint(ТОЧКА_СВЕРКИ)
		# Коммит — на любом исходе: следующее прохождение читает релиз новым снимком.
		# В тестах — без коммита: тест откатывает свои записи сам.
		if not frappe.in_test:
			frappe.db.commit()
	return изменено


def _найти(ученик: str, курс: str, ключ_урока: str, *, for_update: bool = False) -> str | None:
	return frappe.db.get_value(
		ПРОХОЖДЕНИЕ,
		{"student": ученик, "course": курс, "lesson_key": ключ_урока},
		"name",
		for_update=for_update,
	)


def _действующий(курс: str) -> str | None:
	return frappe.db.get_value("LMS Course", курс, "active_release")


def _вставить(run):
	"""Вставить новое прохождение — или взять то, что уже вставил параллельный вызов.

	Дубль ловит уникальный индекс (`install.обеспечить_индекс_прохождений`);
	откат к точке снимает только эту вставку, и прохождение перечитывается с
	блокировкой — как `homework._вставить`.

	`Why:` перечитывание помогает, только когда чужое прохождение видно снимку
	этой транзакции — дубль в ней же или запись, зафиксированная до её
	начала. При настоящей гонке MariaDB стенда (`innodb_snapshot_isolation`)
	отвечает на блокирующее чтение новой строки взаимоблокировкой
	(`frappe.QueryDeadlockError`), и она откатывает транзакцию целиком: метод
	контракта, вызвавший `прохождение`, отдаёт её агенту как `busy`.
	"""
	frappe.db.savepoint(ТОЧКА_ВСТАВКИ)
	try:
		run.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		frappe.db.rollback(save_point=ТОЧКА_ВСТАВКИ)
		имя = _найти(run.student, run.course, run.lesson_key, for_update=True)
		if not имя:
			raise
		# Сообщение Frappe «must be unique» ответ не несёт: прохождение уже есть.
		frappe.clear_last_message()
		уже = frappe.get_doc(ПРОХОЖДЕНИЕ, имя, for_update=True)
		сверить(уже)
		return уже
	frappe.db.release_savepoint(ТОЧКА_ВСТАВКИ)
	return run


def _сверить(run, релиз: str, урок: dict | None, цели: list[dict]) -> bool:
	"""Сверка с уроком релиза, уже прочитанным: `урок` пуст — урока в релизе нет."""
	if not урок:
		return False
	_привести(run, релиз, урок, цели)
	run.save(ignore_permissions=True)
	return True


def _привести(run, релиз: str, урок: dict, цели: list[dict]) -> None:
	"""Строки прохождения — по уроку релиза, статусы пересчитаны; без сохранения."""
	_сверить_пункты(run, цели)
	_сверить_цели(run, цели)
	run.release = релиз
	run.lesson = урок["lesson"]
	статусы(run)


def _сверить_пункты(run, цели: list[dict]) -> None:
	прежние = {п.goal_key: п for п in run.goals}
	порядок: dict[str, int] = {}
	for цель in цели:
		for п in цель["goals"]:
			порядок[п["key"]] = len(порядок)
			поля = {
				"objective_key": цель["key"],
				"kind": п["kind"],
				"required": int(п["required"]),
				"title": п["title"],
				"removed": 0,
			}
			if строка := прежние.get(п["key"]):
				строка.update(поля)
			else:
				run.append("goals", {"goal_key": п["key"], "status": ОТКРЫТ, **поля})
	for строка in run.goals:
		if строка.goal_key not in порядок:
			строка.removed = 1
	_упорядочить(run.goals, порядок, "goal_key")


def _сверить_цели(run, цели: list[dict]) -> None:
	прежние = {ц.objective_key: ц for ц in run.objectives}
	порядок = {ц["key"]: номер for номер, ц in enumerate(цели)}
	for ключ in порядок:
		if строка := прежние.get(ключ):
			строка.removed = 0
		else:
			run.append("objectives", {"objective_key": ключ, "status": "not_started"})
	for строка in run.objectives:
		if строка.objective_key not in порядок:
			строка.removed = 1
	_упорядочить(run.objectives, порядок, "objective_key")


def _упорядочить(строки: list, порядок: dict[str, int], поле: str) -> None:
	"""Порядок релиза; снятые — в конце, в прежнем порядке."""
	строки.sort(key=lambda с: порядок.get(с.get(поле), len(порядок)))
	for номер, строка in enumerate(строки, start=1):
		строка.idx = номер
