# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Прогресс записи на курс из релиза — доля пройденных уроков программы (learning-services#522).

Learning считает `LMS Enrollment.progress` в `get_course_progress`: пройденные
уроки курса (`LMS Course Progress` по полю `course`) делятся на уроки
оглавления (`Lesson Reference` глав из `Chapter Reference` курса). Числитель
оглавлением не ограничен. В самом Learning это не ошибка: удаление урока
удаляет и его прогресс. У курса из релиза снятый урок со следами учеников
остаётся записью вне оглавления (`projection.убрать`), его прогресс — тоже, и
Learning его засчитывает: доля выходит выше настоящей и бывает больше 100.
На доле держатся сертификат Learning, программы (средняя доля и порядок
курсов) и всё, что считает курс пройденным по `progress >= 100`.

Здесь доля считается только по урокам оглавления: та же формула и та же
точность, что у Learning, но в числителе — пройденные уроки программы.
Достигнутые 100 публикация не опускает (`целевая`).

`Why:` поправляется запись на курс, а не формула Learning. Функцию, которую
Learning зовёт изнутри, хуком не подменить, а каждую свою долю Learning пишет
через `update_enrollment`, который вызывает `on_update` записи. Хук
`on_update` идёт после контроллера Learning, но раньше уведомлений, вебхуков
и серверных скриптов (`Document.run_method`): они видят уже верную долю.
Правка числителя в форке Learning была бы чище, но тогда приложение
зависело бы от неё.

Learning пересчитывает долю при записи и удалении `LMS Course Progress` и
при вставке и удалении записи урока — последнее задачей, поставленной до
коммита публикации, которая может прочитать прежнее оглавление. Снятый урок
со следами не удаляется, и по нему пересчёта нет. Поэтому после публикации
доли записанных пересчитываются фоном (`пересчитать_курс`).

Чего не делает: курс без действующего релиза не трогает — его состав Learning
и `structure` считают по-своему. Ответ `save_progress` и его событие
`update_lesson_progress` несут долю Learning, а не записанную. Страница урока
урок курса из релиза не закрывает (`browser_progress`), но
`lms.lms.api.mark_lesson_progress` доступен по токену ученика и закрывает
урок через `save_progress` Learning — тогда событие несёт долю Learning,
а запись на курс всё равно получает верную через этот хук.
"""

import frappe
from frappe.utils import cint, flt
from lms.lms.doctype.lms_enrollment.lms_enrollment import update_enrollment, update_program_progress
from rq.timeouts import JobTimeoutException

from lms_frappe_app.agent_learning.constants import ПРОЙДЕН

ТОЧКА = "course_progress_recalc"
ЗАГОЛОВОК_СБОЯ = "Прогресс не пересчитан (learning-services#522)"
ЗАГОЛОВОК_ГОНКИ = "Прогресс не пересчитан: взаимоблокировка и при повторе (learning-services#522)"
#: `frappe.flags`: (ученик, курс), у которых сейчас снимается пройденный урок.
СНИМАЕТСЯ = "lms_progress_removed"
ПОЛНАЯ = 100


def доли(курс: str, ученик: str | None = None) -> dict[str, tuple[float, float]]:
	"""Запись на курс → (записанная доля, доля по урокам программы) — одним запросом.

	`ученик` — только его запись; без него — все записи курса.
	"""
	условие = "and e.member = %(member)s" if ученик else ""
	строки = frappe.db.sql(
		f"""
		select e.name, e.progress,
			(select count(*) from `tabLesson Reference` r
				join `tabChapter Reference` c on c.chapter = r.parent
				where c.parent = %(course)s) as всего,
			(select count(*) from `tabLMS Course Progress` p
				where p.course = %(course)s and p.member = e.member and p.status = %(done)s
					and p.lesson in (
						select r.lesson from `tabLesson Reference` r
						join `tabChapter Reference` c on c.chapter = r.parent
						where c.parent = %(course)s
					)) as пройдено
		from `tabLMS Enrollment` e
		where e.course = %(course)s {условие}
		""",
		{"course": курс, "member": ученик, "done": ПРОЙДЕН},
	)
	точность = cint(frappe.db.get_default("float_precision")) or 3
	return {
		имя: (flt(записано), flt(пройдено / всего * 100, точность) if всего else 0)
		for имя, записано, всего, пройдено in строки
	}


def целевая(прежняя: float | None, по_программе: float) -> float:
	"""Доля, которую пишем: по программе, но достигнутые 100 не опускаются.

	`прежняя` — записанная доля до этой записи; `None` — не в счёт (новая
	запись на курс или снимается пройденный урок). Доля по программе больше 100
	не бывает, поэтому завышенная запись приходит к 100, а заниженная — к доле
	по программе.

	`Why:` прохождение курса снимает только сам ученик, а не публикация автора
	(решение владельца, learning-services#522). Новый урок в релизе иначе
	опускал бы прошедших курс ниже 100: сертификат и следующий курс программы
	закрывались бы задним числом.

	«Был на 100» — по записанной доле. Отдельного признака завершения у записи
	на курс Learning нет, а сертификат (`LMS Enrollment.certificate`) есть
	только у курсов с сертификацией и только после выдачи — у остальных
	прохождение по нему не узнать.
	"""
	if прежняя is not None and flt(прежняя) >= ПОЛНАЯ:
		return ПОЛНАЯ
	return по_программе


def сверить(запись, method=None) -> None:
	"""`on_update` записи на курс: у курса из релиза — доля по урокам программы
	(`целевая`).

	Прежняя доля — до записи Learning: её снимок кладёт `update_enrollment` и
	`save`. Когда у ученика снимается пройденный урок (`отметить_снятие`), 100
	не держится: сброс прогресса и удаление отметки опускают долю.

	Контроллер Learning уже свёл программы ученика по своей доле, поэтому
	после правки они сводятся заново.
	"""
	if not frappe.get_cached_value("LMS Course", запись.course, "active_release"):
		return
	_, по_программе = доли(запись.course, запись.member).get(запись.name, (None, None))
	if по_программе is None:
		return
	до = запись.get_doc_before_save()
	снимается = (запись.member, запись.course) in (frappe.flags.get(СНИМАЕТСЯ) or set())
	доля = целевая(None if до is None or снимается else до.progress, по_программе)
	if flt(запись.progress) == доля:
		return
	frappe.db.set_value("LMS Enrollment", запись.name, "progress", доля, update_modified=False)
	запись.progress = доля
	update_program_progress(запись.member)


def отметить_снятие(прогресс, method=None) -> None:
	"""`on_trash` и `validate` отметки пройденного: снимается ли пройденный урок.

	Отметка держится, пока пересчёт Learning (`after_delete`, `on_update`
	отметки) пишет долю, и снимается следом (`снять_отметку`).
	"""
	if method == "validate":
		до = прогресс.get_doc_before_save()
		if not до or до.status != ПРОЙДЕН or прогресс.status == ПРОЙДЕН:
			return
	frappe.flags.setdefault(СНИМАЕТСЯ, set()).add((прогресс.member, прогресс.course))


def снять_отметку(прогресс, method=None) -> None:
	"""`after_delete` и `on_update` отметки пройденного — после пересчёта Learning."""
	(frappe.flags.get(СНИМАЕТСЯ) or set()).discard((прогресс.member, прогресс.course))


def пересчитать_курс(курс: str) -> int:
	"""Доли записанных на курс из релиза — по его оглавлению (`целевая`); отдаёт,
	сколько поменялось.

	Ставится в фон публикацией релиза (`releases.service.опубликовать`).
	Пишется только расходящаяся доля — через `update_enrollment` Learning, с
	событиями записи, как пишет он сам.

	`Why:` каждая запись — своя транзакция, как у `runs.service.сверить_курс`:
	взаимоблокировка откатывает транзакцию целиком, и одна общая потеряла бы
	весь пересчёт. Соперником бывает и ученик, закрывающий урок, и задача
	пересчёта Learning, и вторая публикация. После взаимоблокировки запись
	повторяется один раз, в новой транзакции и с долей, посчитанной заново:
	соперник мог её поменять. Не прошёл и повтор — доля записи остаётся
	прежней, а в логе ошибок — её имя. Её поправит хук на следующей записи
	Learning в эту запись на курс (пройденный или сброшенный урок) или
	пересчёт, запущенный вручную: `bench --site <сайт> execute
	lms_frappe_app.agent_learning.course_progress.пересчитать_курс --kwargs
	'{"курс": "<курс>"}'`. Таймаут задачи (`JobTimeoutException`) не
	глотается: пойманный, он дал бы задаче дожить до SIGKILL без следа в логе.
	"""
	if not frappe.db.get_value("LMS Course", курс, "active_release"):
		return 0
	изменено = 0
	for имя, (записано, по_программе) in доли(курс).items():
		доля = целевая(записано, по_программе)
		if записано == доля:
			continue
		записана = _записать(имя, доля)
		if записана is None:
			# Взаимоблокировка уже откатила транзакцию целиком — точки сохранения нет.
			frappe.db.rollback()
			записана = _повторить(курс, имя)
		изменено += bool(записана)
		# В тестах — без коммита: тест откатывает свои записи сам.
		if not frappe.in_test:
			frappe.db.commit()
	return изменено


def _записать(имя: str, доля: float, *, повтор: bool = False) -> bool | None:
	"""Доля — в запись на курс под точкой сохранения: `True` — записана, `False` —
	сбой в логе, `None` — взаимоблокировка первой попытки: повторять."""
	frappe.db.savepoint(ТОЧКА)
	try:
		update_enrollment(имя, {"progress": доля})
	except JobTimeoutException:
		raise
	except frappe.QueryDeadlockError:
		if not повтор:
			return None
		frappe.db.rollback()
		frappe.log_error(title=ЗАГОЛОВОК_ГОНКИ, reference_doctype="LMS Enrollment", reference_name=имя)
		return False
	except Exception:
		frappe.db.rollback(save_point=ТОЧКА)
		frappe.log_error(title=ЗАГОЛОВОК_СБОЯ, reference_doctype="LMS Enrollment", reference_name=имя)
		return False
	frappe.db.release_savepoint(ТОЧКА)
	return True


def _повторить(курс: str, имя: str) -> bool:
	"""Повтор после взаимоблокировки: доля — заново, запись — если ещё расходится."""
	ученик = frappe.db.get_value("LMS Enrollment", имя, "member")
	записано, по_программе = доли(курс, ученик).get(имя, (None, None)) if ученик else (None, None)
	if по_программе is None or записано == (доля := целевая(записано, по_программе)):
		return False
	return bool(_записать(имя, доля, повтор=True))
