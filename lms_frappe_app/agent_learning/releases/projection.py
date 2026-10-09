# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Проекция релиза в Frappe Learning: главы, уроки и их порядок (learning-services#500).

Ключ релиза находит свою запись Learning по ключу на самой записи
(`Course Chapter.chapter_key`, `Course Lesson.lesson_key`, learning-services#514):
та же глава и тот же урок переживают правку текста, перестановку и перенос
между главами. Ключ пишется при создании записи и больше не меняется. Снятое
из релиза не удаляется — уходит из порядка (строк-ссылок Learning): на урок
ссылаются следы учеников, а вернувшийся ключ получает ту же запись. Материала
у урока нет: он только агенту (решение владельца, #497).

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

from dataclasses import dataclass, field

import frappe

from lms_frappe_app.agent_learning.releases.course_guard import ИЗ_РЕЛИЗА

ГЛАВА = "Course Chapter"
УРОК = "Course Lesson"
#: Поле ключа релиза на записи Learning.
ПОЛЕ_КЛЮЧА = {ГЛАВА: "chapter_key", УРОК: "lesson_key"}


def _виды() -> dict[str, list[str]]:
	return {"chapters": [], "lessons": []}


def известные(курс: str) -> dict[str, dict[str, str]]:
	"""Ключ → запись Learning по ключам на главах и уроках курса — двумя выборками.

	Снятые из релиза записи — тоже: по ним вернувшийся ключ находит ту же запись.
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
