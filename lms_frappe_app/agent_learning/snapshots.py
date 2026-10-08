# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Снимки уроков — «как было» для отметок «изменено» в кабинете автора.

Снимок места — его текст в форме, удобной для сравнения, и режим разбиения:
`{"text", "mode"}`, где режим — абзацы или строки (`agent_learning.diffs`).
Снимок урока — снимки всех его мест: `{"places": {место: снимок}}`, в
порядке, в каком их показывает кабинет.

Кабинет запоминает снимок урока, когда автор его открыл, и сравнивает с
нынешним в следующий визит (lms-high-time/learning-services#271). `Why:`
снимок показывает ровно то, что видел человек, — не важно, каким путём
агент записал правку и попала ли она в историю платформы.
"""

import json

import frappe

from lms_frappe_app.agent_learning import course_builder, directives, quiz
from lms_frappe_app.agent_learning.diffs import РЕЖИМ_СТРОКИ, РЕЖИМ_ТЕКСТ

#: Поля директивы урока в порядке, в каком их показывает кабинет.
ПОЛЯ_УРОКА = ("teaching_directive", "objectives", "probing_questions", "success_criteria", "common_misconceptions")
#: Поля, которые пишут связным текстом; прочие — по строке на пункт.
ТЕКСТОМ = frozenset({"teaching_directive"})


def снимок_урока(course: str, lesson: str) -> dict | None:
	"""Снимок урока: `{"places": …}`; урока больше нет — `None`."""
	if not frappe.db.exists("Course Lesson", lesson):
		return None
	return {"places": места_урока(course, lesson)}


def места_урока(course: str, lesson: str) -> dict[str, dict]:
	"""Снимки мест урока: материал, заполненные поля директивы, вопросы
	квиза, блоки документа, которые урок собирает."""
	места = {"material": _место(frappe.db.get_value("Course Lesson", lesson, "body"), РЕЖИМ_ТЕКСТ)}
	директива = directives.запись("Agent Lesson Directive", {"lesson": lesson}, ПОЛЯ_УРОКА)
	for поле in ПОЛЯ_УРОКА:
		if директива and (директива.get(поле) or "").strip():
			места[f"directive.{поле}"] = _место(директива.get(поле), _режим_поля(поле))
	квиз = quiz._квиз_урока(lesson)
	for вопрос in frappe.get_all("LMS Quiz Question", filters={"parent": квиз}, pluck="question", order_by="idx asc") if квиз else []:
		места[f"question.{вопрос}"] = _вопрос(вопрос)
	for документ, блок in _блоки_курса(course):
		if блок.lesson == lesson:
			места[f"block.{документ}/{блок.block_key}"] = _место_блока(блок)
	return места


def в_json(снимок_места: dict | None) -> str | None:
	return json.dumps(снимок_места, ensure_ascii=False) if снимок_места is not None else None


def из_json(текст: str | None) -> dict | None:
	return json.loads(текст) if текст else None


def _место(текст: str | None, режим: str) -> dict:
	return {"text": текст or "", "mode": режим}


def _режим_поля(поле: str) -> str:
	return РЕЖИМ_ТЕКСТ if поле in ТЕКСТОМ else РЕЖИМ_СТРОКИ


def _вопрос(вопрос: str) -> dict:
	"""Вопрос строками: текст, варианты («✓» — верный, «·» — нет) с
	пояснениями, образцы ответа."""
	документ = frappe.get_doc("LMS Question", вопрос)
	строки = [документ.question or ""]
	for номер, текст in course_builder.заполненные(документ, "option"):
		знак = "✓" if документ.get(f"is_correct_{номер}") else "·"
		пояснение = документ.get(f"explanation_{номер}") or ""
		строки.append(f"{знак} {текст}" + (f" — {пояснение}" if пояснение else ""))
	строки += [f"Образец: {эталон}" for _, эталон in course_builder.заполненные(документ, "possibility")]
	return _место("\n".join(строки), РЕЖИМ_СТРОКИ)


def _блоки_курса(course: str) -> list[tuple[str, frappe._dict]]:
	"""Блоки действующих схем документов курса — парами «документ, блок»."""
	блоки = []
	for документ in frappe.get_all(
		"Agent Course Artifact", filters={"course": course, "is_active": 1}, fields=["name", "slug"], order_by="creation asc"
	):
		for блок in frappe.get_all(
			"Agent Artifact Block",
			filters={"parent": документ.name},
			fields=["block_key", "title", "hint", "lesson"],
			order_by="idx asc",
		):
			блоки.append((документ.slug, блок))
	return блоки


def _место_блока(блок) -> dict:
	return _место("\n\n".join(часть for часть in (блок.title, блок.hint) if (часть or "").strip()), РЕЖИМ_ТЕКСТ)
