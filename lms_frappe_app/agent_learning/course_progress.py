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

`Why:` поправляется запись на курс, а не формула Learning. Функцию, которую
Learning зовёт изнутри, хуком не подменить, а каждую свою долю Learning пишет
через `update_enrollment`, который вызывает `on_update` записи. Хук
`on_update` идёт после контроллера Learning, но раньше уведомлений, вебхуков
и серверных скриптов (`Document.run_method`): они видят уже верную долю.

Публикация меняет оглавление, а Learning пересчитывает долю только при
вставке и удалении записи урока — снятый урок со следами не удаляется. Поэтому
после публикации доли записанных пересчитываются фоном (`пересчитать_курс`).

Чего не делает: курс без действующего релиза не трогает — его состав Learning
и `structure` считают по-своему. Ответ `save_progress` и его событие
`update_lesson_progress` несут долю Learning, а не записанную: у курса из
релиза этим путём урок не закрывается — страница урока его не закрывает
(`browser_progress`), а квиза на странице и SCORM-глав у курса из релиза нет.
"""

import frappe
from frappe.utils import cint, flt
from lms.lms.doctype.lms_enrollment.lms_enrollment import update_enrollment, update_program_progress

from lms_frappe_app.agent_learning.constants import ПРОЙДЕН

ТОЧКА = "course_progress_recalc"


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


def сверить(запись, method=None) -> None:
	"""`on_update` записи на курс: у курса из релиза — доля по урокам программы.

	Контроллер Learning уже свёл программы ученика по прежней доле, поэтому
	после правки они сводятся заново.
	"""
	if not frappe.get_cached_value("LMS Course", запись.course, "active_release"):
		return
	_, верная = доли(запись.course, запись.member).get(запись.name, (None, None))
	if верная is None or flt(запись.progress) == верная:
		return
	frappe.db.set_value("LMS Enrollment", запись.name, "progress", верная, update_modified=False)
	запись.progress = верная
	update_program_progress(запись.member)


def пересчитать_курс(курс: str) -> int:
	"""Доли записанных на курс из релиза — по его оглавлению; отдаёт, сколько поменялось.

	Ставится в фон публикацией релиза (`releases.service.опубликовать`).
	Пишется только расходящаяся доля — через `update_enrollment` Learning, с
	событиями записи, как пишет он сам.

	`Why:` каждая запись — своя транзакция, как у `runs.service.сверить_курс`:
	взаимоблокировка с учеником, закрывающим урок, откатывает транзакцию
	целиком, и одна общая потеряла бы весь пересчёт. Не пересчитанное здесь
	пересчитает Learning на следующем пройденном уроке.
	"""
	if not frappe.db.get_value("LMS Course", курс, "active_release"):
		return 0
	изменено = 0
	for имя, (записано, верная) in доли(курс).items():
		if записано == верная:
			continue
		frappe.db.savepoint(ТОЧКА)
		try:
			update_enrollment(имя, {"progress": верная})
		except frappe.QueryDeadlockError:
			# Взаимоблокировка уже откатила транзакцию целиком — точки сохранения нет.
			frappe.db.rollback()
			frappe.log_error(
				title="Прогресс не пересчитан: гонка с учеником (learning-services#522)",
				reference_doctype="LMS Enrollment",
				reference_name=имя,
			)
		except Exception:
			frappe.db.rollback(save_point=ТОЧКА)
			frappe.log_error(
				title="Прогресс не пересчитан (learning-services#522)",
				reference_doctype="LMS Enrollment",
				reference_name=имя,
			)
		else:
			frappe.db.release_savepoint(ТОЧКА)
			изменено += 1
		# В тестах — без коммита: тест откатывает свои записи сам.
		if not frappe.in_test:
			frappe.db.commit()
	return изменено
