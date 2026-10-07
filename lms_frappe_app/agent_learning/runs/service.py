# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Прохождение урока: создание, сверка с релизом, отметки, статусы (learning-services#504).

Прохождение одно на «ученик, курс, ключ урока» и держит пункты и цели урока
по ключам релиза. Ученик всегда на действующем релизе: прохождение, сверенное
с прошлым, сверяется при обращении и фоном после публикации нового.

Записи — без проверки прав: прохождение не видят ни ученик, ни руководитель,
а пишут его только методы контракта, которые проверяют доступ к курсу сами.
Запись читает прохождение с блокировкой (`for_update`): агенты шлют вызовы
параллельно, и второй `save` упал бы на устаревшем `modified`.
"""

import frappe
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.errors import ЧУЖОЕ_ЗАНЯТИЕ, Отказ
from lms_frappe_app.agent_learning.releases import index

ПРОХОЖДЕНИЕ = "Agent Lesson Run"
УРОК_НЕ_В_РЕЛИЗЕ = "lesson_not_in_release"
ТОЧКА_ВСТАВКИ = "lesson_run_insert"
ТОЧКА_СВЕРКИ = "lesson_run_reconcile"

ПУНКТ_НЕИЗВЕСТЕН = "goal_unknown"
ПУНКТ_СНЯТ = "goal_removed"
СТАТУС_НЕИЗВЕСТЕН = "goal_status_unknown"
НЕ_НУЖЕН_ОБЯЗАТЕЛЬНОМУ = "not_needed_required"
НУЖНО_СВИДЕТЕЛЬСТВО = "evidence_required"
ДЛИННОЕ_СВИДЕТЕЛЬСТВО = "evidence_too_long"

ОТКРЫТ = "open"
НЕ_НУЖЕН = "not_needed"
СТАТУСЫ_ПУНКТА = (ОТКРЫТ, "done", "planned", НЕ_НУЖЕН)
#: Статусы пункта, которые закрывают его для цели: разобран или отложен на потом.
ЗАКРЫВАЮТ_ЦЕЛЬ = frozenset({"done", "planned"})
ПРЕДЕЛ_СВИДЕТЕЛЬСТВА = 500
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


def отметить(
	имя_прохождения: str,
	ключ_пункта: str,
	статус: str,
	свидетельство: str | None,
	занятие: str | None = None,
) -> dict:
	"""Отметка пункта прохождения; отдаёт остаток по цели пункта и следующий шаг урока.

	Прохождение перечитывается с блокировкой и сверяется с действующим
	релизом до отметки. Строка пункта получает статус, свидетельство, время
	и занятие этой отметки; первая отметка не в `open` начинает урок
	(`started_at`). Вернуть пункт в `open` можно без свидетельства.

	Отказ — только на форме: неизвестный статус или пункт, снятый пункт,
	`not_needed` у обязательного, закрывающий статус без свидетельства,
	свидетельство длиннее предела, занятие другого ученика или урока, урок,
	снятый из релиза.

	`Why:` отметка — запись с предупреждением, а не отказ (#497): пункт,
	отмеченный «не по порядку», пишется, а в ответе — что по цели ещё открыто
	и что дальше. В журнал занятия отметки не пишутся: его читают ученик и
	руководитель, а пункты им не показываются; история — `track_changes`
	прохождения и поля строки.
	"""
	if статус not in СТАТУСЫ_ПУНКТА:
		raise Отказ(
			СТАТУС_НЕИЗВЕСТЕН, "Неизвестный статус пункта", status=статус, allowed=list(СТАТУСЫ_ПУНКТА)
		)
	свидетельство = (свидетельство or "").strip() or None
	if статус != ОТКРЫТ and not свидетельство:
		raise Отказ(НУЖНО_СВИДЕТЕЛЬСТВО, "Отметка пункта требует свидетельства", status=статус)
	if свидетельство and len(свидетельство) > ПРЕДЕЛ_СВИДЕТЕЛЬСТВА:
		raise Отказ(
			ДЛИННОЕ_СВИДЕТЕЛЬСТВО,
			"Свидетельство длиннее предела",
			limit=ПРЕДЕЛ_СВИДЕТЕЛЬСТВА,
			length=len(свидетельство),
		)
	run = frappe.get_doc(ПРОХОЖДЕНИЕ, имя_прохождения, for_update=True)
	сверить(run)
	if run.release != _действующий(run.course):
		raise Отказ(
			УРОК_НЕ_В_РЕЛИЗЕ,
			"Урока нет в действующем релизе курса",
			course=run.course,
			lesson_key=run.lesson_key,
		)
	if занятие:
		_проверить_занятие(run, занятие)
	строка = next((п for п in run.goals if п.goal_key == ключ_пункта), None)
	if not строка:
		raise Отказ(ПУНКТ_НЕИЗВЕСТЕН, "В уроке нет такого пункта", goal=ключ_пункта)
	if строка.removed:
		raise Отказ(ПУНКТ_СНЯТ, "Пункт снят из урока новым релизом", goal=ключ_пункта)
	if статус == НЕ_НУЖЕН and строка.required:
		raise Отказ(НЕ_НУЖЕН_ОБЯЗАТЕЛЬНОМУ, "Обязательный пункт нельзя отметить ненужным", goal=ключ_пункта)

	строка.status, строка.evidence = статус, свидетельство
	строка.marked_at, строка.session = now_datetime(), занятие
	if статус != ОТКРЫТ and not run.started_at:
		run.started_at = строка.marked_at
	статусы(run)
	run.save(ignore_permissions=True)

	[цель] = [ц for ц in run.objectives if ц.objective_key == строка.objective_key]
	открытые = открытые_обязательные(run)
	return {
		"goal": строка.goal_key,
		"status": строка.status,
		"objective": {
			"key": цель.objective_key,
			"status": цель.status,
			"open": [п["goal"] for п in открытые if п["objective"] == цель.objective_key],
		},
		"lesson": {"status": run.status},
		"next": открытые[0] if открытые else None,
	}


def открытые_обязательные(run) -> list[dict]:
	"""Обязательные пункты, не закрытые для цели, — по порядку релиза: `{objective, goal, title}`.

	Ворота квиза (этап 3) и остаток в ответе `отметить`. Снятые не в счёт.

	`Why:` «открыт» — не закрыт для цели (не `done` и не `planned`), а не
	только `open`: пункт в `not_needed`, который новый релиз сделал
	обязательным, цель не закрывает (`статусы`), и ворота квиза его тоже
	должны видеть.
	"""
	return [
		{"objective": п.objective_key, "goal": п.goal_key, "title": п.title}
		for п in run.goals
		if п.required and not п.removed and п.status not in ЗАКРЫВАЮТ_ЦЕЛЬ
	]


def следующий(run) -> dict | None:
	"""Следующий шаг урока — первый открытый обязательный пункт; всё закрыто — `None`."""
	открытые = открытые_обязательные(run)
	return открытые[0] if открытые else None


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


def _проверить_занятие(run, занятие: str) -> None:
	"""Занятие отметки — того же ученика и урока, что и прохождение; нет такого — тоже чужое."""
	своё = frappe.db.get_value("Agent Learning Session", занятие, ["student", "lesson"], as_dict=True)
	if not своё or (своё.student, своё.lesson) != (run.student, run.lesson):
		raise Отказ(ЧУЖОЕ_ЗАНЯТИЕ, "Занятие другого ученика или другого урока", session=занятие)


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
