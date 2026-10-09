# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Проекция релиза в Frappe Learning: главы, уроки и их порядок (learning-services#500).

Ключ релиза находит свою запись Learning по ключу на самой записи
(`Course Chapter.chapter_key`, `Course Lesson.lesson_key`, learning-services#514):
та же глава и тот же урок переживают правку текста, перестановку и перенос
между главами. Ключ пишется при создании записи и больше не меняется. Снятое
из релиза уходит из порядка (строк-ссылок Learning), а затем уборка
(`убрать`) удаляет запись, на которую ничего не ссылается; запись со ссылками
остаётся вне оглавления, и вернувшийся ключ получает её же. Материала у урока
нет: он только агенту (решение владельца, #497).

`Why:` ключ на записи, а не в истории индексов релизов: содержимое прежних
версий не хранится, а соответствие «ключ → запись» нужно и после них. Тот же
приём, что `LMS Course.course_key`.

Главы и уроки пишутся без проверки прав, как и сам релиз (`service._записать_релиз`).
`Why:` Learning даёт Course Creator запись `Course Lesson` только своих
(`if_owner`) — иначе куратор не переопубликовал бы курс, который опубликовал
другой куратор или Administrator. Метод публикации закрыт авторскими ролями.

Каждая запись глав, уроков и порядка глав курса помечена `ИЗ_РЕЛИЗА`: правку
структуры курса из релиза мимо публикации отклоняет `course_guard`.
"""

from collections import Counter
from dataclasses import dataclass, field

import frappe
from frappe.model.delete_doc import get_dynamic_linked_docs, get_linked_docs

from lms_frappe_app.agent_learning.releases import index
from lms_frappe_app.agent_learning.releases.course_guard import ИЗ_РЕЛИЗА

ГЛАВА = "Course Chapter"
УРОК = "Course Lesson"
#: Поле ключа релиза на записи Learning.
ПОЛЕ_КЛЮЧА = {ГЛАВА: "chapter_key", УРОК: "lesson_key"}


def _виды() -> dict[str, list[str]]:
	return {"chapters": [], "lessons": []}


def известные(курс: str) -> dict[str, dict[str, str]]:
	"""Ключ → запись Learning по ключам на главах и уроках курса — двумя выборками.

	Снятые из релиза записи, оставленные уборкой, — тоже: по ним вернувшийся
	ключ находит ту же запись.
	"""
	return {"chapters": _по_ключам(ГЛАВА, курс), "lessons": известные_уроки(курс)}


def известные_уроки(курс: str) -> dict[str, str]:
	"""Ключ урока → запись урока курса (`известные`, только уроки) — одной выборкой."""
	return _по_ключам(УРОК, курс)


def _по_ключам(doctype: str, курс: str) -> dict[str, str]:
	поле = ПОЛЕ_КЛЮЧА[doctype]
	return dict(
		frappe.get_all(
			doctype, filters={"course": курс, поле: ("is", "set")}, fields=[поле, "name"], as_list=True
		)
	)


def ключ_только_из_релиза(doc, method=None) -> None:
	"""Хук `validate` у `Course Chapter` и `Course Lesson`: ключ меняет только проекция.

	Без флага `ИЗ_РЕЛИЗА` ключ остаётся прежним (у новой записи — `None`), что
	бы ни прислал клиент: импорт курса в Learning переносит поля записи через
	`doc.update`, а `frappe.client.insert` и `save` пишут любые поля записи
	курса без релиза. Пустой ключ — NULL. Ключ пишут только `insert` и `save`
	проекции и разовый патч `release_record_keys`; иных `db.set_value` и
	`db_set` ключа мимо хука в коде нет.

	`Why:` соответствие «ключ → запись» держит только ключ на записи
	(`известные`): ключ, поставленный мимо публикации, — ложное соответствие,
	и публикация в этот курс взяла бы под ключ запись, которую не заводила.
	Уникальный индекс `(course, ключ)` (`install.обеспечить_индекс_ключей_проекции`)
	различает NULL-ы, но не `''`: вторая запись без ключа в одном курсе упала
	бы на индексе.

	`Why:` тихий пропуск, а не отказ, как у `course_key`
	(`course_guard.проверить_курс`): импорт Learning переносит поля глав и
	уроков целиком, вместе с ключом, и отказ сломал бы импорт.
	"""
	поле = ПОЛЕ_КЛЮЧА[doc.doctype]
	if not doc.flags.get(ИЗ_РЕЛИЗА):
		прежний = doc.get_doc_before_save()
		doc.set(поле, прежний.get(поле) if прежний else None)
	if not (doc.get(поле) or "").strip():
		doc.set(поле, None)


@dataclass
class Итог:
	главы: dict[str, str] = field(default_factory=dict)  # ключ → Course Chapter
	уроки: dict[str, str] = field(default_factory=dict)  # ключ → Course Lesson
	создано: dict[str, list[str]] = field(default_factory=_виды)
	обновлено: dict[str, list[str]] = field(default_factory=_виды)
	снято: dict[str, list[str]] = field(default_factory=_виды)
	#: Записи Learning ключей `снято` — их уборка (`убрать`).
	снятые_записи: dict[str, list[str]] = field(default_factory=_виды)
	#: Ключи, которых не было в действующем релизе, а запись с этим ключом нашлась.
	возвращено: dict[str, list[str]] = field(default_factory=_виды)


def спроецировать(
	курс: str, релиз: dict, известные: dict[str, dict[str, str]], прежние: dict[str, list[str]]
) -> Итог:
	"""Главы и уроки курса — по релизу.

	`известные` — `{"chapters": {ключ: имя}, "lessons": {…}}`, записи курса с
	ключами (`известные(курс)`); `прежние` — ключи глав и уроков действующего
	релиза: по ним считается `снято`.
	"""
	итог = Итог()
	for глава in релиз["chapters"]:
		итог.главы[глава["key"]] = _записать(
			ГЛАВА,
			известные["chapters"].get(глава["key"]),
			{"course": курс, "title": глава["title"], "chapter_description": глава["description"] or None},
			глава["key"],
			"chapters",
			итог,
		)
	for урок in релиз["lessons"]:
		итог.уроки[урок["key"]] = _записать(
			УРОК,
			известные["lessons"].get(урок["key"]),
			{
				"course": курс,
				"chapter": итог.главы[урок["chapter"]],
				"title": урок["title"],
				"lesson_hook": урок["hook"] or None,
			},
			урок["key"],
			"lessons",
			итог,
		)
	_порядок(
		курс,
		[итог.главы[г["key"]] for г in релиз["chapters"]],
		{итог.главы[г["key"]]: [итог.уроки[у] for у in г["lessons"]] for г in релиз["chapters"]},
	)
	есть = {"chapters": list(итог.главы), "lessons": list(итог.уроки)}
	for вид in ("chapters", "lessons"):
		были = set(прежние.get(вид, []))
		итог.снято[вид] = [ключ for ключ in прежние.get(вид, []) if ключ not in есть[вид]]
		итог.снятые_записи[вид] = [известные[вид][ключ] for ключ in итог.снято[вид]]
		итог.возвращено[вид] = [
			ключ for ключ in есть[вид] if ключ not in были and ключ not in итог.создано[вид]
		]
	return итог


def _записать(doctype: str, имя: str | None, поля: dict, ключ: str, вид: str, итог: Итог) -> str:
	"""Запись Learning под ключ: прежняя, если она есть, иначе новая — с ключом.

	Ключ прежней записи не переписывается: он тот же по построению `известные`.
	"""
	if имя:
		документ = frappe.get_doc(doctype, имя)
		if any((документ.get(поле) or None) != (значение or None) for поле, значение in поля.items()):
			документ.update(поля)
			документ.flags[ИЗ_РЕЛИЗА] = True
			документ.save(ignore_permissions=True)
			итог.обновлено[вид].append(ключ)
		return имя
	новое = {"doctype": doctype, **поля, ПОЛЕ_КЛЮЧА[doctype]: ключ}
	if doctype == УРОК:
		новое["body"] = ""
	итог.создано[вид].append(ключ)
	документ = frappe.get_doc(новое)
	документ.flags[ИЗ_РЕЛИЗА] = True
	return документ.insert(ignore_permissions=True).name


def _порядок(курс: str, главы: list[str], уроки_глав: dict[str, list[str]]) -> None:
	"""Строки порядка Learning — ровно как в релизе; у снятых глав строк не остаётся.

	`Why:` Learning ищет место урока по `Lesson Reference`, и строка в снятой
	главе вела бы его не туда, куда урок переехал.
	"""
	курс_документ = frappe.get_doc("LMS Course", курс)
	было = [строка.chapter for строка in курс_документ.chapters]
	if было != главы:
		курс_документ.set("chapters", [{"chapter": глава} for глава in главы])
		курс_документ.flags[ИЗ_РЕЛИЗА] = True
		курс_документ.save()
	for глава in dict.fromkeys(было + главы):
		нужно = уроки_глав.get(глава, [])
		документ = frappe.get_doc(ГЛАВА, глава)
		if [строка.lesson for строка in документ.lessons] != нужно:
			документ.set("lessons", [{"lesson": урок} for урок in нужно])
			документ.flags[ИЗ_РЕЛИЗА] = True
			документ.save(ignore_permissions=True)


@dataclass
class Уборка:
	удалено: dict[str, list[str]] = field(default_factory=_виды)
	#: Записи, на которые ссылаются: остались вне оглавления.
	оставлено: dict[str, list[str]] = field(default_factory=_виды)
	#: Оставленная запись → чем держится: доктайп ссылающихся записей → их число;
	#: ссылки Dynamic Link — только у записи без ссылок Link.
	держат: dict[str, dict[str, int]] = field(default_factory=dict)


def убрать(записи: dict[str, list[str]]) -> Уборка:
	"""Снятые из релиза записи Learning: без ссылок — удалить, со ссылками — оставить.

	`записи` — `{"chapters": [имя], "lessons": [имя]}`, записи вне оглавления
	курса. Уроки — раньше глав: оставленный урок держит свою главу
	(`Course Lesson.chapter`).

	Ссылки ищет Frappe (`get_linked_docs`, `get_dynamic_linked_docs` — их же
	зовут `check_if_doc_is_linked` и `check_if_doc_is_dynamically_linked`) по
	всем полям Link и Dynamic Link сайта, включая дочерние таблицы, — до
	удаления, в снимке транзакции публикации. Блокировка записи защищает
	только от правки самой записи, а не от новой ссылки на неё — см.
	«Принято» у `_удалить`. Держат запись следы учеников
	и автора: занятия, прохождения, попытки квиза (и аннулированные), события
	квиза, репорты, сдачи и шаблоны домашки со сдачами, блоки схем документа,
	прогресс и записи на курс Learning. Строки индекса освобождённых версий и
	шаблоны домашки без сдач уже удалены публикацией раньше уборки.

	`Why:` запись без ссылок ничего не хранит, а копится вне оглавления с
	каждой версией; запись со ссылками нужна данным учеников. Вернувшийся ключ
	удалённой записи получает новую — на прежнюю ничего не ссылалось. Тот же
	приём, что у шаблона домашки (`homework._снять`).
	"""
	уборка = Уборка()
	for вид, doctype in (("lessons", УРОК), ("chapters", ГЛАВА)):
		for имя in записи[вид]:
			if держат := _удалить(doctype, имя):
				уборка.оставлено[вид].append(имя)
				уборка.держат[имя] = держат
			else:
				уборка.удалено[вид].append(имя)
	return уборка


def вне_релиза(курс: str) -> dict[str, list[str]]:
	"""Главы и уроки курса, которых нет в его действующем релизе, — с ключом и без;
	у курса без действующего релиза — ничего. По выборке на вид."""
	записи = _виды()
	for вид, doctype, строка, поле in (
		("chapters", ГЛАВА, index.ГЛАВА, "chapter"),
		("lessons", УРОК, index.УРОК, "lesson"),
	):
		записи[вид] = frappe.db.sql_list(
			f"""
			select z.name
			from `tab{doctype}` z
			join `tabLMS Course` c on c.name = z.course
			where z.course = %(course)s and ifnull(c.active_release, '') != ''
				and not exists (
					select 1 from `tab{строка}` r
					where r.parenttype = %(parenttype)s and r.parent = c.active_release
						and r.`{поле}` = z.name
				)
			order by z.creation, z.name
			""",
			{"course": курс, "parenttype": index.РЕЛИЗ},
		)
	return записи


def _удалить(doctype: str, имя: str) -> dict[str, int]:
	"""Удаляет запись без ссылок и отдаёт `{}`; запись со ссылками не трогает и
	отдаёт, чем она держится: доктайп ссылающихся записей → их число.

	Удаление — без `on_trash`: ссылки уже проверены, а `on_trash` урока
	Learning (`cleanup_lesson_backreferences`) обнуляет их запросами по таблицам
	записей на курс, квизов и сдач Learning без индекса по уроку; хук
	`course_guard` отказал бы — главу и урок курса из релиза удаляет только
	проекция. `force` — потому что ссылки только что проверены, и `delete_doc`
	проверил бы их второй раз. Строки дочерней таблицы главы (`Lesson
	Reference`) `delete_doc` удаляет запросом, мимо их хука `on_trash`; у
	снятой главы их уже нет — их сняла проекция.

	Цена: `after_delete` урока Learning ставит пересчёт прогресса записанных
	на курс в очередь сразу, а не после коммита. Задача может начаться раньше
	коммита публикации и посчитать прогресс по прежнему составу курса — и при
	успешной публикации, а при откате она отработает впустую.

	Принято: ссылку, которую другой запрос вставил и зафиксировал, пока шла
	публикация, проверка не видит — она читает снимок транзакции публикации.
	Так бывает при первом касании снимаемого урока во время публикации:
	ученик начинает урок по прежней версии, и его занятие (прохождение,
	попытка) ссылается на урок, который публикация удаляет. Следствие —
	висячая ссылка: такое занятие не сохранить с проверкой Link, и
	`закрыть_брошенные_занятия` закрывает его без неё. Блокировка записи
	здесь не помогает: вставка ссылки строку урока не блокирует.
	"""
	frappe.db.get_value(doctype, имя, "name", for_update=True)
	документ = frappe.get_doc(doctype, имя)
	# Динамические — только без обычных: как `delete_doc`, который до них не доходит.
	if ссылки := get_linked_docs(документ) or get_dynamic_linked_docs(документ):
		return dict(Counter(с["reference_doctype"] for с in ссылки))
	frappe.delete_doc(
		doctype,
		имя,
		force=True,
		ignore_permissions=True,
		ignore_on_trash=True,
		delete_permanently=True,
	)
	return {}
