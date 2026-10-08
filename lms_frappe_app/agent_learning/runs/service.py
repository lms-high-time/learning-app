# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Прохождение урока: создание, сверка с релизом, отметки, статусы (learning-services#504).

Прохождение одно на «ученик, курс, ключ урока» и держит пункты и цели урока
по ключам релиза. Ученик всегда на действующем релизе: прохождение, сверенное
с прошлым, сверяется при обращении и фоном после публикации нового.

Записи — без проверки прав: прохождение не видят ни ученик, ни руководитель,
а пишут его только методы контракта, которые проверяют доступ к курсу сами.
Запись читает прохождение с блокировкой (`for_update`): агенты шлют вызовы
параллельно, и второй `save` упал бы на устаревшем `modified`. При гонке с
параллельной записью MariaDB стенда (`innodb_snapshot_isolation`) отвечает на
блокирующее чтение `frappe.QueryDeadlockError`: метод контракта откатывает
транзакцию и отдаёт агенту `busy` — как `submit_homework`.
"""

from collections.abc import Collection

import frappe
from frappe.utils import now_datetime

from lms_frappe_app.agent_learning.errors import ПРОХОЖДЕНИЕ_В_АРХИВЕ, УРОК_НЕ_В_РЕЛИЗЕ, ЧУЖОЕ_ЗАНЯТИЕ, Отказ
from lms_frappe_app.agent_learning.releases import index

ПРОХОЖДЕНИЕ = "Agent Lesson Run"
ТОЧКА_ВСТАВКИ = "lesson_run_insert"
ТОЧКА_СВЕРКИ = "lesson_run_reconcile"

ПУНКТ_НЕИЗВЕСТЕН = "goal_unknown"
ПУНКТ_СНЯТ = "goal_removed"
СТАТУС_НЕИЗВЕСТЕН = "goal_status_unknown"
НЕ_НУЖЕН_ОБЯЗАТЕЛЬНОМУ = "not_needed_required"
НУЖНО_СВИДЕТЕЛЬСТВО = "evidence_required"
ДЛИННОЕ_СВИДЕТЕЛЬСТВО = "evidence_too_long"
ДЛИННОЕ_ПРОДОЛЖЕНИЕ = "resume_from_too_long"

ОТКРЫТ = "open"
НЕ_НУЖЕН = "not_needed"
СТАТУСЫ_ПУНКТА = (ОТКРЫТ, "done", "planned", НЕ_НУЖЕН)
#: Статусы пункта, которые закрывают его для цели: разобран или отложен на потом.
ЗАКРЫВАЮТ_ЦЕЛЬ = frozenset({"done", "planned"})
ПРЕДЕЛ_СВИДЕТЕЛЬСТВА = 500
#: Предел заметки «с чего продолжить» — одна фраза, а не конспект занятия.
ПРЕДЕЛ_ПРОДОЛЖЕНИЯ = 500
ПРОЙДЕН = "passed"
#: Статус урока, цели и главы, которых ещё не касались.
НЕ_НАЧАТО = "not_started"
#: Время раньше любого настоящего — подстановка вместо пустого в сравнении времён.
НИКОГДА = "1000-01-01 00:00:00"


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
	релиз = действующий(курс)
	урок = index.урок(релиз, ключ_урока) if релиз else None
	if not урок:
		raise урок_не_в_релизе(курс, ключ_урока, релиз)
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
	return _сверить_с(run, действующий(run.course))


def отметить(
	имя_прохождения: str,
	ключ_пункта: str,
	статус: str,
	свидетельство: str | None,
	занятие: str | None = None,
	*,
	квиз_обязателен: bool = False,
	продолжить: str | None = None,
) -> dict:
	"""Отметка пункта прохождения (`отметить_с_целями`) — ответ без статусов целей урока."""
	return отметить_с_целями(
		имя_прохождения,
		ключ_пункта,
		статус,
		свидетельство,
		занятие,
		квиз_обязателен=квиз_обязателен,
		продолжить=продолжить,
	)[0]


def отметить_с_целями(
	имя_прохождения: str,
	ключ_пункта: str,
	статус: str,
	свидетельство: str | None,
	занятие: str | None = None,
	*,
	квиз_обязателен: bool = False,
	продолжить: str | None = None,
) -> tuple[dict, list[str]]:
	"""Отметка пункта прохождения; отдаёт остаток по цели пункта и следующий шаг урока,
	а вторым — статусы целей урока после отметки (снятые не в счёт).

	Статусы целей нужны сигналу после отметки (`blocks_empty`): прохождение
	уже прочитано, и перечитывать цели урока незачем.

	Прохождение перечитывается с блокировкой и сверяется с действующим
	релизом до отметки. Строка пункта получает статус, свидетельство, время
	и занятие этой отметки; первая отметка не в `open` начинает урок
	(`started_at`). Возврат в `open` свидетельства не требует и стирает
	прежнее: открытый пункт ничем не подтверждён. `продолжить` — заметка «с
	чего продолжить»: непустая пишется в прохождение тем же сохранением, что и
	отметка, пустая прежнюю не трогает. `квиз_обязателен` — для следующего
	шага (`следующий_шаг`): у урока есть вопросы, и политика требует квиз.

	Отказ — только на форме (`форма_отметки`): неизвестный статус, закрывающий
	статус без свидетельства, свидетельство или заметка длиннее предела; и на
	прохождении: неизвестный или снятый пункт, `not_needed` у обязательного,
	занятие другого ученика, урока или прохождения, архивное прохождение
	(`run_archived`), урок, снятый из релиза. Сверка сохраняется до проверки
	пункта и остаётся, даже когда отметке потом отказано: прохождение и так
	должно стоять на действующем релизе.

	Прохождение, не сверенное с действующим релизом, сохраняется дважды — две
	версии `track_changes`: «принят новый релиз» и «отмечен пункт» в истории
	раздельны.

	При гонке с параллельной записью блокирующее чтение падает
	`frappe.QueryDeadlockError` (снимочная изоляция MariaDB стенда): метод
	контракта откатывает транзакцию и отдаёт `busy`, как `submit_homework`.

	`Why:` отметка — запись с предупреждением, а не отказ (#497): пункт,
	отмеченный «не по порядку», пишется, а в ответе — что по цели ещё открыто
	и что дальше. В журнал занятия отметки не пишутся: его читают ученик и
	руководитель, а пункты им не показываются; история — `track_changes`
	прохождения и поля строки.
	"""
	свидетельство, продолжить = форма_отметки(статус, свидетельство, продолжить)
	run = frappe.get_doc(ПРОХОЖДЕНИЕ, имя_прохождения, for_update=True)
	требовать_живое(run)
	релиз = действующий(run.course)
	_сверить_с(run, релиз)
	if run.release != релиз:
		raise урок_не_в_релизе(run.course, run.lesson_key, релиз)
	if занятие:
		проверить_занятие(run, занятие)
	строка = next((п for п in run.goals if п.goal_key == ключ_пункта), None)
	if not строка:
		raise Отказ(
			ПУНКТ_НЕИЗВЕСТЕН,
			"В уроке нет такого пункта",
			goal=ключ_пункта,
			goals=[п.goal_key for п in run.goals if not п.removed],
		)
	if строка.removed:
		raise Отказ(ПУНКТ_СНЯТ, "Пункт снят из урока новым релизом", goal=ключ_пункта)
	if статус == НЕ_НУЖЕН and строка.required:
		raise Отказ(
			НЕ_НУЖЕН_ОБЯЗАТЕЛЬНОМУ,
			"Обязательный пункт нельзя отметить ненужным",
			goal=ключ_пункта,
			allowed=[с for с in СТАТУСЫ_ПУНКТА if с != НЕ_НУЖЕН],
		)

	строка.status, строка.evidence = статус, свидетельство
	строка.marked_at, строка.session = now_datetime(), занятие or None
	if статус != ОТКРЫТ and not run.started_at:
		run.started_at = строка.marked_at
	if продолжить:
		run.resume_from = продолжить
	статусы(run)
	run.save(ignore_permissions=True)

	[цель] = [ц for ц in run.objectives if ц.objective_key == строка.objective_key]
	открытые = открытые_обязательные(run)
	ответ = {
		"goal": строка.goal_key,
		"status": строка.status,
		"objective": {
			"key": цель.objective_key,
			"status": цель.status,
			"open": [п["goal"] for п in открытые if п["objective"] == цель.objective_key],
		},
		"lesson": {"status": run.status},
		"next_step": следующий_шаг(run, квиз_обязателен),
	}
	return ответ, [ц.status for ц in run.objectives if not ц.removed]


def форма_отметки(
	статус: str, свидетельство: str | None, продолжить: str | None
) -> tuple[str | None, str | None]:
	"""Свидетельство и заметка «с чего продолжить» отметки — обрезанные; форма не та — отказ.

	Свидетельство у `open` — `None`, у прочих статусов обязательно. Пустая
	заметка — `None`. Ничего не читает и не пишет: метод контракта зовёт её
	до первой записи, `отметить` — ещё раз.
	"""
	if статус not in СТАТУСЫ_ПУНКТА:
		raise Отказ(
			СТАТУС_НЕИЗВЕСТЕН, "Неизвестный статус пункта", status=статус, allowed=list(СТАТУСЫ_ПУНКТА)
		)
	свидетельство = None if статус == ОТКРЫТ else (свидетельство or "").strip()
	if статус != ОТКРЫТ and not свидетельство:
		raise Отказ(НУЖНО_СВИДЕТЕЛЬСТВО, "Отметка пункта требует свидетельства", status=статус)
	if свидетельство and len(свидетельство) > ПРЕДЕЛ_СВИДЕТЕЛЬСТВА:
		raise Отказ(
			ДЛИННОЕ_СВИДЕТЕЛЬСТВО,
			"Свидетельство длиннее предела",
			limit=ПРЕДЕЛ_СВИДЕТЕЛЬСТВА,
			length=len(свидетельство),
		)
	продолжить = (продолжить or "").strip() or None
	if продолжить and len(продолжить) > ПРЕДЕЛ_ПРОДОЛЖЕНИЯ:
		raise Отказ(
			ДЛИННОЕ_ПРОДОЛЖЕНИЕ,
			"Заметка «с чего продолжить» длиннее предела",
			limit=ПРЕДЕЛ_ПРОДОЛЖЕНИЯ,
			length=len(продолжить),
		)
	return свидетельство, продолжить


def требовать_живое(run) -> None:
	"""Архивное прохождение (`student` пуст после сброса прогресса) — отказ `run_archived`."""
	if not run.student:
		raise Отказ(ПРОХОЖДЕНИЕ_В_АРХИВЕ, "Прохождение в архиве: прогресс ученика сброшен", run=run.name)


def по_имени(имя: str):
	"""Прохождение по имени — с блокировкой, сверенное с действующим релизом; архивное — отказ `run_archived`."""
	run = frappe.get_doc(ПРОХОЖДЕНИЕ, имя, for_update=True)
	требовать_живое(run)
	сверить(run)
	return run


def статус_пункта(ученик: str, курс: str, ключ_урока: str, ключ_пункта: str) -> str:
	"""Сохранённый статус пункта в прохождении ученика — простым чтением; нет прохождения или пункта — `open`.

	`Why:` без блокировки и сверки: это путь чтения, а сверка писала бы на нём
	(см. `главы`).
	"""
	строки = frappe.db.sql(
		"""
		select g.status from `tabAgent Lesson Run Goal` g
		join `tabAgent Lesson Run` r on r.name = g.parent
		where g.parenttype = 'Agent Lesson Run' and r.student = %(student)s
			and r.course = %(course)s and r.lesson_key = %(lesson)s and g.goal_key = %(goal)s
		limit 1
		""",
		{"student": ученик, "course": курс, "lesson": ключ_урока, "goal": ключ_пункта},
	)
	return строки[0][0] if строки else ОТКРЫТ


def отметить_пройденным(
	ученик: str, курс: str, ключ_урока: str, подтверждённые_цели: Collection[str] = ()
) -> None:
	"""Урок пройден: `passed` и `passed_at` у прохождения, «подтверждено квизом» у целей.

	Прохождения нет — оно заводится (`прохождение`): урок, закрытый до первой
	отметки, иначе не шёл бы в счёт глав. Нет ни прохождения, ни урока в
	действующем релизе — отмечать нечего. Повторный зачёт `passed_at` не
	двигает, а «подтверждено квизом» только добавляет: снимает его лишь
	сброс прогресса.

	Цели, снятые новым релизом, подтверждения не получают. Прохождение
	сохраняется, только когда что-то поменялось.

	Прохождение читается с блокировкой (`прохождение`): вызывающий берёт его
	раньше записи занятия — тот же порядок, что у старта квиза.
	"""
	if not _найти(ученик, курс, ключ_урока):
		релиз = действующий(курс)
		# Молча, а не отказом: урок закрывается и без прохождения, а урока вне
		# действующего релиза нет в дереве ученика — пройденным его показывать негде.
		if not релиз or not index.урок(релиз, ключ_урока):
			return
	run = прохождение(ученик, курс, ключ_урока)
	изменено = False
	for цель in run.objectives:
		if not цель.removed and not цель.quiz_confirmed and цель.objective_key in подтверждённые_цели:
			цель.quiz_confirmed, изменено = 1, True
	if run.status != ПРОЙДЕН:
		run.status, run.passed_at, изменено = ПРОЙДЕН, now_datetime(), True
	if изменено:
		run.save(ignore_permissions=True)


def открытые_обязательные(run) -> list[dict]:
	"""Обязательные пункты, не закрытые для цели, — по порядку релиза: `{objective, goal, title}`.

	Ворота квиза в методе контракта и остаток в ответе `отметить`. Снятые не в счёт.

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
	return _первый(открытые_обязательные(run))


def следующий_шаг(run, квиз_обязателен: bool) -> dict | None:
	"""Следующий шаг урока данными: пункт, квиз, закрытие или ничего.

	`{"kind": "goal", objective, goal, title}` — первый открытый обязательный
	пункт по порядку релиза; `{"kind": "quiz"}` — обязательные закрыты, а квиз
	обязателен (у урока есть вопросы, и политика его требует); `{"kind":
	"complete"}` — закрыты, а квиз не обязателен; `None` — урок пройден.

	`Why:` данные, а не фраза: шаг читают и агентский MCP, и веб-чат, и каждый
	складывает свой текст.
	"""
	if run.status == ПРОЙДЕН:
		return None
	if пункт := следующий(run):
		return {"kind": "goal", **пункт}
	return {"kind": "quiz"} if квиз_обязателен else {"kind": "complete"}


def карта(run, тексты: dict[str, str]) -> list[dict]:
	"""Цели урока по порядку релиза — с текстом, статусом и пунктами; снятые не отдаются.

	`тексты` — ключ цели → текст (`index.тексты_целей`); цель без текста — `None`.
	"""
	пункты: dict[str, list[dict]] = {}
	for п in run.goals:
		if not п.removed:
			пункты.setdefault(п.objective_key, []).append(
				{
					"key": п.goal_key,
					"kind": п.kind,
					"required": bool(п.required),
					"title": п.title,
					"status": п.status,
				}
			)
	return [
		{
			"key": ц.objective_key,
			"text": тексты.get(ц.objective_key),
			"status": ц.status,
			"goals": пункты.get(ц.objective_key, []),
		}
		for ц in run.objectives
		if not ц.removed
	]


def история(ученик: str, курс: str, кроме: str, глубина: int) -> list[dict]:
	"""Прошлые уроки ученика по курсу — `глубина` последних начатых или пройденных, кроме урока `кроме`.

	Урок — `{key, title, status, objectives_open}`: название и тексты
	незакрытых целей (не `covered`, по порядку) — из релиза прохождения. Последний — по
	последней отметке пункта, началу или зачёту, что позже. Ничего не пишет и
	прохождений не сверяет: читает сохранённые статусы.

	`Why:` одна выборка уроков и одна целей с текстами на всю глубину — старт
	урока платит за историю постоянное число запросов.
	"""
	if глубина <= 0:
		return []
	уроки = frappe.db.sql(
		"""
		select r.name, r.lesson_key, rl.title, r.status
		from `tabAgent Lesson Run` r
		left join `tabAgent Release Lesson` rl
			on rl.parenttype = 'Agent Course Release' and rl.parent = r.release
			and rl.lesson_key = r.lesson_key
		where r.student = %(student)s and r.course = %(course)s
			and r.lesson_key != %(except)s and r.status != 'not_started'
		order by greatest(
			coalesce(
				(
					select max(g.marked_at) from `tabAgent Lesson Run Goal` g
					where g.parent = r.name and g.parenttype = 'Agent Lesson Run'
				),
				cast(%(never)s as datetime(6))
			),
			coalesce(r.started_at, cast(%(never)s as datetime(6))),
			coalesce(r.passed_at, cast(%(never)s as datetime(6)))
		) desc, r.name
		limit %(depth)s
		""",
		{"student": ученик, "course": курс, "except": кроме, "depth": глубина, "never": НИКОГДА},
		as_dict=True,
	)
	if not уроки:
		return []
	цели = frappe.db.sql(
		"""
		select o.parent, o.objective_key, o.status, t.text
		from `tabAgent Lesson Run Objective` o
		join `tabAgent Lesson Run` r on r.name = o.parent
		left join `tabAgent Release Objective` t
			on t.parenttype = 'Agent Course Release' and t.parent = r.release
			and t.lesson_key = r.lesson_key and t.objective_key = o.objective_key
		where o.parenttype = 'Agent Lesson Run' and o.parent in %(runs)s
			and o.removed = 0 and o.status != 'covered'
		order by o.idx
		""",
		{"runs": tuple(у.name for у in уроки)},
		as_dict=True,
	)
	открытые: dict[str, list[dict]] = {}
	for ц in цели:
		открытые.setdefault(ц.parent, []).append({"key": ц.objective_key, "text": ц.text, "status": ц.status})
	return [
		{"key": у.lesson_key, "title": у.title, "status": у.status, "objectives_open": открытые.get(у.name, [])}
		for у in уроки
	]


def главы(ученик: str, курс: str) -> list[dict]:
	"""Главы действующего релиза с прогрессом ученика по курсу (`прогресс_глав`); нет релиза — пусто.

	Ничего не пишет.
	"""
	релиз = действующий(курс)
	if not релиз:
		return []
	return прогресс_глав(index.уроки_глав(релиз), статусы_уроков(ученик, курс))


def статусы_уроков(ученик: str, курс: str) -> dict[str, str]:
	"""Ключ урока → сохранённый статус прохождения ученика по курсу — простым чтением."""
	return dict(
		frappe.get_all(
			ПРОХОЖДЕНИЕ,
			filters={"student": ученик, "course": курс},
			fields=["lesson_key", "status"],
			as_list=True,
		)
	)


def прогресс_глав(уроки_глав: dict[str, list[str]], статус: dict[str, str]) -> list[dict]:
	"""Прогресс по главам: `{key, status, lessons_total, lessons_started, lessons_passed}`.

	`уроки_глав` — глава → ключи её уроков в действующем релизе, по порядку
	релиза (`index.уроки_глав`); `статус` — ключ урока → статус прохождения
	(`статусы_уроков`). Урок начат, когда статус его прохождения не
	`not_started`, пройден — когда `passed`. Глава пройдена, когда пройдены
	все её уроки; начата — хоть один; иначе не начата. Считаются только уроки
	из `уроки_глав`: прохождение снятого урока в счёт не идёт.

	`Why:` счёт — по хранимому статусу, без сверки прохождений. Сверка не
	меняет деления «не начат / начат / пройден»: `not_started` зависит только
	от `started_at`, который ничто не снимает, `passed` сверкой не снимается,
	а снятые уроки отсекает индекс релиза. Сверка здесь писала бы на пути
	чтения: GET откатывает записи, а блокирующее чтение сразу после
	публикации ловит взаимоблокировку снимочной изоляции.
	"""
	итог = []
	for ключ, уроки in уроки_глав.items():
		начато = sum(статус.get(у, НЕ_НАЧАТО) != НЕ_НАЧАТО for у in уроки)
		пройдено = sum(статус.get(у) == ПРОЙДЕН for у in уроки)
		if уроки and пройдено == len(уроки):
			состояние = ПРОЙДЕН
		elif начато:
			состояние = "in_progress"
		else:
			состояние = НЕ_НАЧАТО
		итог.append(
			{
				"key": ключ,
				"status": состояние,
				"lessons_total": len(уроки),
				"lessons_started": начато,
				"lessons_passed": пройдено,
			}
		)
	return итог


def статусы_целей(ученик: str, курс: str) -> dict[tuple[str, str], str]:
	"""(ключ урока, ключ цели) → сохранённый статус цели в прохождениях ученика по курсу.

	Простым чтением, без блокировки и сверки (см. `прогресс_глав`); снятые
	цели тоже отдаются — читающий берёт цели из действующего релиза, и статус
	цели, которую сверка вернёт, тот же. Одним запросом на курс.
	"""
	return {
		(с[0], с[1]): с[2]
		for с in frappe.db.sql(
			"""
			select r.lesson_key, o.objective_key, o.status
			from `tabAgent Lesson Run Objective` o
			join `tabAgent Lesson Run` r on r.name = o.parent
			where o.parenttype = 'Agent Lesson Run' and r.student = %(student)s and r.course = %(course)s
			""",
			{"student": ученик, "course": курс},
		)
	}


def цели_урока(ученик: str, курс: str, релиз: str, ключ_урока: str) -> list[dict]:
	"""Цели урока действующего релиза `релиз` со статусом из прохождения ученика: `[{key, text, status}]`.

	Порядок и тексты — из релиза, статус — сохранённый в прохождении (нет
	прохождения или цели в нём — `not_started`). Пунктов и свидетельств нет:
	это уровень, который видят ученик и руководитель. Простым чтением, одним
	запросом, без блокировки и сверки (см. `прогресс_глав`).
	"""
	return [
		{"key": с[0], "text": с[1], "status": с[2] or НЕ_НАЧАТО}
		for с in frappe.db.sql(
			"""
			select t.objective_key, t.text, o.status
			from `tabAgent Release Objective` t
			left join `tabAgent Lesson Run` r
				on r.student = %(student)s and r.course = %(course)s and r.lesson_key = t.lesson_key
			left join `tabAgent Lesson Run Objective` o
				on o.parent = r.name and o.parenttype = 'Agent Lesson Run'
				and o.objective_key = t.objective_key
			where t.parenttype = 'Agent Course Release' and t.parent = %(release)s
				and t.lesson_key = %(lesson)s
			order by t.idx
			""",
			{"student": ученик, "course": курс, "release": релиз, "lesson": ключ_урока},
		)
	]


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
			цель.status = НЕ_НАЧАТО
	if run.status == ПРОЙДЕН:
		return
	if not run.started_at:
		run.status = НЕ_НАЧАТО
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
	релиз = действующий(курс)
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
		if действующий(курс) != релиз:
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


def проверить_занятие(run, занятие: str) -> None:
	"""Занятие прохождения — того же ученика и урока и не другого прохождения; нет такого — тоже чужое."""
	своё = frappe.db.get_value("Agent Learning Session", занятие, ["student", "lesson", "run"], as_dict=True)
	if (
		not своё
		or (своё.student, своё.lesson) != (run.student, run.lesson)
		or своё.run not in (None, "", run.name)
	):
		raise Отказ(ЧУЖОЕ_ЗАНЯТИЕ, "Занятие другого ученика, урока или прохождения", session=занятие)


def _первый(открытые: list[dict]) -> dict | None:
	return открытые[0] if открытые else None


def урок_не_в_релизе(курс: str, ключ_урока: str, релиз: str | None) -> Отказ:
	"""Отказ `lesson_not_in_release`: урока нет в действующем релизе курса."""
	return Отказ(
		УРОК_НЕ_В_РЕЛИЗЕ,
		"Урока нет в действующем релизе курса",
		course=курс,
		lesson_key=ключ_урока,
		release=релиз,
	)


def действующий(курс: str) -> str | None:
	"""Действующий релиз курса; у курса без релиза — `None`."""
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


def _сверить_с(run, релиз: str | None) -> bool:
	"""`сверить` с действующим релизом, уже прочитанным: `релиз` пуст — у курса его нет."""
	if not релиз or релиз == run.release:
		return False
	урок = index.урок(релиз, run.lesson_key)
	return _сверить(run, релиз, урок, index.цели_урока(релиз, run.lesson_key) if урок else [])


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
			run.append("objectives", {"objective_key": ключ, "status": НЕ_НАЧАТО})
	for строка in run.objectives:
		if строка.objective_key not in порядок:
			строка.removed = 1
	_упорядочить(run.objectives, порядок, "objective_key")


def _упорядочить(строки: list, порядок: dict[str, int], поле: str) -> None:
	"""Порядок релиза; снятые — в конце, в прежнем порядке."""
	строки.sort(key=lambda с: порядок.get(с.get(поле), len(порядок)))
	for номер, строка in enumerate(строки, start=1):
		строка.idx = номер
