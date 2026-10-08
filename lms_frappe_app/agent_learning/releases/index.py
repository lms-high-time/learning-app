# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Индекс релиза по ключам: запись и чтение (learning-services#500).

Индекс — дочерние таблицы релиза. Читается только `frappe.get_all` по
дочерней таблице с фильтром `parent`: `frappe.get_doc` релиза потянул бы все
строки разом (у большого курса — сотни). Ответы квиза — только с явным
`с_ответами`: ими сервер сверяет ответ, наружу они не уходят (CLAUDE.md §10).

Пакет агента раскладывается при записи (learning-services#506): срез урока —
в строку урока, рамка курса — в запись релиза. Внутри срезов приложение
читает только ключи «что выяснять»; остальное хранит как есть. `Why:` снимок
большого курса — мегабайты, и читать его на каждый вызов агента дорого, а
релиз неизменяем: срез, записанный один раз, со снимком не расходится.
"""

import json

import frappe

РЕЛИЗ = "Agent Course Release"
ГЛАВА = "Agent Release Chapter"
УРОК = "Agent Release Lesson"
ЦЕЛЬ = "Agent Release Objective"
ПУНКТ = "Agent Release Goal"
ВОПРОС = "Agent Release Question"
РАЗДЕЛ = "Agent Release Section"


def _json(значение) -> str:
	return json.dumps(значение, ensure_ascii=False)


#: Части пакета агента, которые относятся к курсу целиком, а не к уроку.
ЧАСТИ_РАМКИ = ("frame", "learn_about_student")


def срезы(релиз: dict) -> tuple[dict, dict[str, dict]]:
	"""Пакет агента релиза по частям: (рамка курса, ключ урока → срез урока).

	Рамка — части `ЧАСТИ_РАМКИ`, какие есть; срез — `agent.lessons.<ключ>`
	для каждого урока релиза. Нет пакета, части или среза — `{}` на его месте.
	"""
	пакет = релиз.get("agent")
	пакет = пакет if isinstance(пакет, dict) else {}
	уроки = пакет.get("lessons")
	уроки = уроки if isinstance(уроки, dict) else {}
	рамка_курса = {часть: пакет[часть] for часть in ЧАСТИ_РАМКИ if часть in пакет}
	return рамка_курса, {у["key"]: _объект(уроки.get(у["key"])) for у in релиз["lessons"]}


def _объект(значение) -> dict:
	return значение if isinstance(значение, dict) else {}


def строки(релиз: dict, главы: dict[str, str], уроки: dict[str, str]) -> dict:
	"""Дочерние таблицы релиза — в порядке релиза — и рамка пакета агента (`agent_frame`);
	`главы`, `уроки` — ключ → запись Learning."""
	рамка_курса, срезы_уроков = срезы(релиз)
	таблицы: dict = {
		"chapters": [],
		"lessons": [],
		"objectives": [],
		"goals": [],
		"questions": [],
		"sections": [],
	}
	for г in релиз["chapters"]:
		таблицы["chapters"].append(
			{
				"chapter_key": г["key"],
				"title": г["title"],
				"description": г["description"],
				"chapter": главы[г["key"]],
			}
		)
	for у in релиз["lessons"]:
		таблицы["lessons"].append(
			{
				"lesson_key": у["key"],
				"chapter_key": у["chapter"],
				"title": у["title"],
				"hook": у["hook"],
				"lesson": уроки[у["key"]],
				"pass_percentage": у["quiz"]["pass_percentage"],
				"section_keys": _json(у["sections"]),
				"homework": _json(у["homework"]) if у["homework"] else None,
				"agent": _json(срезы_уроков[у["key"]]),
			}
		)
		for ц in у["objectives"]:
			таблицы["objectives"].append(
				{"lesson_key": у["key"], "objective_key": ц["key"], "text": ц["text"]}
			)
			for п in ц["goals"]:
				таблицы["goals"].append(
					{
						"lesson_key": у["key"],
						"objective_key": ц["key"],
						"goal_key": п["key"],
						"kind": п["kind"],
						"required": int(п["required"]),
						"title": п["title"],
					}
				)
		for в in у["quiz"]["questions"]:
			ответ = у["quiz"]["answers"][в["key"]]
			таблицы["questions"].append(
				{
					"lesson_key": у["key"],
					"question_key": в["key"],
					"objective_key": в["objective"],
					"text": в["text"],
					"option_list": _json(в["options"]),
					"correct": ответ["correct"],
					"explanation": ответ["explanation"],
				}
			)
	for с in (релиз["document"] or {}).get("sections", []):
		таблицы["sections"].append(
			{
				"section_key": с["key"],
				"title": с["title"],
				"description": с["description"],
				"rows": с["rows"],
				"columns": _json(с["columns"]),
			}
		)
	таблицы["agent_frame"] = _json(рамка_курса)
	return таблицы


def известные(курс: str) -> dict[str, dict[str, str]]:
	"""Ключ → запись Learning по всей истории релизов курса; свежий релиз главнее."""
	версии = {
		р.name: р.version for р in frappe.get_all(РЕЛИЗ, filters={"course": курс}, fields=["name", "version"])
	}
	итог: dict[str, dict[str, str]] = {"chapters": {}, "lessons": {}}
	if not версии:
		return итог
	for вид, doctype, ключ, запись in (
		("chapters", ГЛАВА, "chapter_key", "chapter"),
		("lessons", УРОК, "lesson_key", "lesson"),
	):
		найдено = frappe.get_all(
			doctype,
			filters={"parenttype": РЕЛИЗ, "parent": ("in", list(версии))},
			fields=["parent", ключ, запись],
		)
		for строка in sorted(найдено, key=lambda с: версии[с.parent]):
			итог[вид][строка[ключ]] = строка[запись]
	return итог


def ключи(релиз: str | None) -> dict[str, list[str]]:
	"""Ключи глав и уроков релиза по порядку; нет релиза — пусто."""
	if not релиз:
		return {"chapters": [], "lessons": []}
	фильтр = {"parenttype": РЕЛИЗ, "parent": релиз}
	return {
		"chapters": frappe.get_all(ГЛАВА, filters=фильтр, pluck="chapter_key", order_by="idx asc"),
		"lessons": frappe.get_all(УРОК, filters=фильтр, pluck="lesson_key", order_by="idx asc"),
	}


def уроки_глав(релиз: str) -> dict[str, list[str]]:
	"""Ключи уроков по главам релиза: глава → её уроки; главы по порядку релиза."""
	фильтр = {"parenttype": РЕЛИЗ, "parent": релиз}
	главы: dict[str, list[str]] = {
		к: [] for к in frappe.get_all(ГЛАВА, filters=фильтр, pluck="chapter_key", order_by="idx asc")
	}
	for у in frappe.get_all(УРОК, filters=фильтр, fields=["lesson_key", "chapter_key"], order_by="idx asc"):
		главы[у.chapter_key].append(у.lesson_key)
	return главы


def урок(релиз: str, ключ: str) -> dict | None:
	"""Урок релиза по ключу: его запись Learning, глава, порог квиза, разделы, домашка."""
	найдено = frappe.get_all(
		УРОК,
		filters={"parenttype": РЕЛИЗ, "parent": релиз, "lesson_key": ключ},
		fields=[
			"lesson_key",
			"chapter_key",
			"title",
			"hook",
			"lesson",
			"pass_percentage",
			"section_keys",
			"homework",
		],
		limit=1,
	)
	return найдено[0] if найдено else None


def ключ_урока(релиз: str, lesson: str) -> str | None:
	"""Ключ урока релиза по записи `Course Lesson` — обратное к `урок`."""
	return frappe.db.get_value(УРОК, {"parenttype": РЕЛИЗ, "parent": релиз, "lesson": lesson}, "lesson_key")


def цели_урока(релиз: str, ключ: str) -> list[dict]:
	"""Цели урока с пунктами целей агента — по порядку релиза."""
	фильтр = {"parenttype": РЕЛИЗ, "parent": релиз, "lesson_key": ключ}
	цели = [
		{"key": ц.objective_key, "text": ц.text, "goals": []}
		for ц in frappe.get_all(ЦЕЛЬ, filters=фильтр, fields=["objective_key", "text"], order_by="idx asc")
	]
	по_ключу = {ц["key"]: ц for ц in цели}
	for п in frappe.get_all(
		ПУНКТ,
		filters=фильтр,
		fields=["objective_key", "goal_key", "kind", "required", "title"],
		order_by="idx asc",
	):
		по_ключу[п.objective_key]["goals"].append(
			{"key": п.goal_key, "kind": п.kind, "required": bool(п.required), "title": п.title}
		)
	return цели


def вопросы_урока(релиз: str, ключ: str, *, с_ответами: bool = False) -> list[dict]:
	"""Квиз урока по порядку; верный вариант и пояснение — только с `с_ответами`."""
	поля = ["question_key", "objective_key", "text", "option_list"] + (
		["correct", "explanation"] if с_ответами else []
	)
	итог = []
	for в in frappe.get_all(
		ВОПРОС,
		filters={"parenttype": РЕЛИЗ, "parent": релиз, "lesson_key": ключ},
		fields=поля,
		order_by="idx asc",
	):
		вопрос = {
			"key": в.question_key,
			"objective": в.objective_key,
			"text": в.text,
			"options": json.loads(в.option_list),
		}
		if с_ответами:
			вопрос.update(correct=в.correct, explanation=в.explanation)
		итог.append(вопрос)
	return итог


def есть_вопросы(релиз: str, ключ: str) -> bool:
	"""Есть ли у урока релиза квиз — хоть один вопрос."""
	return bool(frappe.db.exists(ВОПРОС, {"parenttype": РЕЛИЗ, "parent": релиз, "lesson_key": ключ}))


def эталон(релиз: str, ключ_урока: str, ключ_вопроса: str) -> dict | None:
	"""Верный вариант и пояснение вопроса урока: `{correct, explanation}`; нет вопроса — `None`."""
	найдено = frappe.get_all(
		ВОПРОС,
		filters={"parenttype": РЕЛИЗ, "parent": релиз, "lesson_key": ключ_урока, "question_key": ключ_вопроса},
		fields=["correct", "explanation"],
		limit=1,
	)
	return найдено[0] if найдено else None


def _из_json(значение) -> dict:
	"""Значение JSON-поля индекса — объект; пусто или не объект — `{}`."""
	if isinstance(значение, str):
		значение = json.loads(значение) if значение else None
	return _объект(значение)


def пакет_урока(релиз: str, ключ: str) -> dict:
	"""Срез пакета агента для урока релиза (`agent.lessons.<ключ>`); нет среза или урока — `{}`."""
	return _из_json(
		frappe.db.get_value(УРОК, {"parenttype": РЕЛИЗ, "parent": релиз, "lesson_key": ключ}, "agent")
	)


def рамка(релиз: str) -> dict:
	"""Рамка пакета агента релиза: `frame`, `learn_about_student`, какие есть; нет — `{}`."""
	return _из_json(frappe.db.get_value(РЕЛИЗ, релиз, "agent_frame"))


def ключи_выяснять(релиз: str) -> list[str]:
	"""Ключи «что выяснять об ученике» (`learn_about_student[].key`) по порядку пакета.

	Список не тот — пусто; элемент без строкового ключа пропускается: пакет
	собирает компилятор, и его форму приложение не проверяет.
	"""
	что_выяснять = рамка(релиз).get("learn_about_student")
	if not isinstance(что_выяснять, list):
		return []
	return [э["key"] for э in что_выяснять if isinstance(э, dict) and _ключ(э.get("key"))]


def _ключ(значение) -> bool:
	return isinstance(значение, str) and bool(значение)


def снимок(релиз: str) -> dict:
	"""Релиз целиком, как опубликован: части `agent` и `map` — как есть."""
	return json.loads(frappe.db.get_value(РЕЛИЗ, релиз, "snapshot"))
