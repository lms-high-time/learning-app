# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Индекс релиза по ключам: запись и чтение (learning-services#500).

Индекс — дочерние таблицы релиза. Читается `frappe.get_all` и
`frappe.db.get_value` по строкам с фильтром `parent`, никогда не
`frappe.get_doc` релиза: он потянул бы все строки разом (у большого курса —
сотни). Ответы квиза — только с явным `с_ответами`: ими сервер сверяет
ответ, к ученику они не уходят (CLAUDE.md §10).

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


def в_json(значение) -> str:
	"""Значение JSON-поля индекса — строкой, как его пишет `строки`."""
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
				"section_keys": в_json(у["sections"]),
				"homework": в_json(у["homework"]) if у["homework"] else None,
				"agent": в_json(срезы_уроков[у["key"]]),
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
					"option_list": в_json(в["options"]),
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
				"columns": в_json(с["columns"]),
			}
		)
	таблицы["agent_frame"] = в_json(рамка_курса)
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


def главы(релиз: str) -> list[dict]:
	"""Главы релиза по порядку: `{key, title, description}`."""
	return [
		{"key": г.chapter_key, "title": г.title, "description": г.description}
		for г in frappe.get_all(
			ГЛАВА,
			filters={"parenttype": РЕЛИЗ, "parent": релиз},
			fields=["chapter_key", "title", "description"],
			order_by="idx asc",
		)
	]


def уроки(релиз: str) -> list[dict]:
	"""Уроки релиза по порядку — те же поля, что у `урок`, одной выборкой."""
	return frappe.get_all(
		УРОК, filters={"parenttype": РЕЛИЗ, "parent": релиз}, fields=ПОЛЯ_УРОКА, order_by="idx asc"
	)


def цели(релиз: str) -> dict[str, list[dict]]:
	"""Цели всех уроков релиза без пунктов: ключ урока → `[{key, text}]` по порядку."""
	итог: dict[str, list[dict]] = {}
	for ц in frappe.get_all(
		ЦЕЛЬ,
		filters={"parenttype": РЕЛИЗ, "parent": релиз},
		fields=["lesson_key", "objective_key", "text"],
		order_by="idx asc",
	):
		итог.setdefault(ц.lesson_key, []).append({"key": ц.objective_key, "text": ц.text})
	return итог


def разделы_документа(релиз: str) -> list[dict]:
	"""Разделы документа релиза по порядку — как в релизе: `{key, title, description, rows, columns}`."""
	return [
		{
			"key": р.section_key,
			"title": р.title,
			"description": р.description,
			"rows": р.rows,
			"columns": json.loads(р.columns) if р.columns else [],
		}
		for р in frappe.get_all(
			РАЗДЕЛ,
			filters={"parenttype": РЕЛИЗ, "parent": релиз},
			fields=["section_key", "title", "description", "rows", "columns"],
			order_by="idx asc",
		)
	]


def разделы(релиз: str) -> dict[str, dict]:
	"""Разделы документа релиза: ключ → `{key, title, description}`."""
	return {
		р.section_key: {"key": р.section_key, "title": р.title, "description": р.description}
		for р in frappe.get_all(
			РАЗДЕЛ,
			filters={"parenttype": РЕЛИЗ, "parent": релиз},
			fields=["section_key", "title", "description"],
			order_by="idx asc",
		)
	}


#: Поля урока релиза, которые отдают `урок`, `урок_по_записи` и `уроки`.
ПОЛЯ_УРОКА = [
	"lesson_key",
	"chapter_key",
	"title",
	"hook",
	"lesson",
	"pass_percentage",
	"section_keys",
	"homework",
]


def урок(релиз: str, ключ: str) -> dict | None:
	"""Урок релиза по ключу: его запись Learning, глава, порог квиза, разделы, домашка."""
	return _урок(релиз, {"lesson_key": ключ})


def урок_по_записи(релиз: str, lesson: str) -> dict | None:
	"""Урок релиза по записи `Course Lesson` — те же поля, что у `урок`, с ключом."""
	return _урок(релиз, {"lesson": lesson})


def _урок(релиз: str, отбор: dict) -> dict | None:
	найдено = frappe.get_all(
		УРОК, filters={"parenttype": РЕЛИЗ, "parent": релиз, **отбор}, fields=ПОЛЯ_УРОКА, limit=1
	)
	return найдено[0] if найдено else None


def ключ_урока(релиз: str, lesson: str) -> str | None:
	"""Ключ урока релиза по записи `Course Lesson` — обратное к `урок`."""
	return frappe.db.get_value(УРОК, {"parenttype": РЕЛИЗ, "parent": релиз, "lesson": lesson}, "lesson_key")


def ключи_уроков(релизы: list[str]) -> dict[tuple[str, str], str]:
	"""Ключи уроков нескольких релизов: (релиз, запись `Course Lesson`) → ключ.
	Одной выборкой на все релизы, а не на урок."""
	if not релизы:
		return {}
	return {
		(у.parent, у.lesson): у.lesson_key
		for у in frappe.get_all(
			УРОК,
			filters={"parenttype": РЕЛИЗ, "parent": ("in", list(set(релизы)))},
			fields=["parent", "lesson", "lesson_key"],
		)
	}


def цели_урока(релиз: str, ключ: str) -> list[dict]:
	"""Цели урока с пунктами целей агента — по порядку релиза."""
	return цели_с_пунктами(релиз, ключ).get(ключ, [])


def цели_с_пунктами(релиз: str, ключ: str | None = None) -> dict[str, list[dict]]:
	"""Цели уроков релиза с пунктами целей агента: ключ урока → `[{key, text, goals}]`
	по порядку релиза; `ключ` — только этого урока. Цели и пункты — каждые одной
	выборкой на релиз, не на урок."""
	фильтр = {"parenttype": РЕЛИЗ, "parent": релиз, **({} if ключ is None else {"lesson_key": ключ})}
	итог: dict[str, list[dict]] = {}
	по_ключу: dict[tuple[str, str], dict] = {}
	for ц in frappe.get_all(
		ЦЕЛЬ, filters=фильтр, fields=["lesson_key", "objective_key", "text"], order_by="idx asc"
	):
		цель = {"key": ц.objective_key, "text": ц.text, "goals": []}
		итог.setdefault(ц.lesson_key, []).append(цель)
		по_ключу[(ц.lesson_key, ц.objective_key)] = цель
	for п in frappe.get_all(
		ПУНКТ,
		filters=фильтр,
		fields=["lesson_key", "objective_key", "goal_key", "kind", "required", "title"],
		order_by="idx asc",
	):
		по_ключу[(п.lesson_key, п.objective_key)]["goals"].append(
			{"key": п.goal_key, "kind": п.kind, "required": bool(п.required), "title": п.title}
		)
	return итог


def тексты_целей(релиз: str, ключ: str) -> dict[str, str]:
	"""Ключ цели урока релиза → её текст."""
	return dict(
		frappe.get_all(
			ЦЕЛЬ,
			filters={"parenttype": РЕЛИЗ, "parent": релиз, "lesson_key": ключ},
			fields=["objective_key", "text"],
			as_list=True,
		)
	)


def название_главы(релиз: str, ключ: str) -> str | None:
	"""Название главы релиза по ключу; нет главы — `None`."""
	return frappe.db.get_value(ГЛАВА, {"parenttype": РЕЛИЗ, "parent": релиз, "chapter_key": ключ}, "title")


def вопросы_урока(релиз: str, ключ: str, *, с_ответами: bool = False) -> list[dict]:
	"""Квиз урока по порядку; верный вариант и пояснение — только с `с_ответами`."""
	return вопросы(релиз, ключ, с_ответами=с_ответами).get(ключ, [])


def вопросы(релиз: str, ключ: str | None = None, *, с_ответами: bool = False) -> dict[str, list[dict]]:
	"""Квизы уроков релиза: ключ урока → вопросы по порядку; `ключ` — только этого урока.
	Верный вариант и пояснение — только с `с_ответами`. Одной выборкой на релиз, не на урок."""
	поля = ["lesson_key", "question_key", "objective_key", "text", "option_list"] + (
		["correct", "explanation"] if с_ответами else []
	)
	итог: dict[str, list[dict]] = {}
	for в in frappe.get_all(
		ВОПРОС,
		filters={"parenttype": РЕЛИЗ, "parent": релиз, **({} if ключ is None else {"lesson_key": ключ})},
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
		итог.setdefault(в.lesson_key, []).append(вопрос)
	return итог


def есть_вопросы(релиз: str, ключ: str) -> bool:
	"""Есть ли у урока релиза квиз — хоть один вопрос."""
	return bool(frappe.db.exists(ВОПРОС, {"parenttype": РЕЛИЗ, "parent": релиз, "lesson_key": ключ}))


def есть_вопрос(релиз: str, ключ_урока: str, ключ_вопроса: str) -> bool:
	"""Есть ли в квизе урока релиза вопрос с этим ключом."""
	return bool(
		frappe.db.exists(
			ВОПРОС,
			{"parenttype": РЕЛИЗ, "parent": релиз, "lesson_key": ключ_урока, "question_key": ключ_вопроса},
		)
	)


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


def пакеты_уроков(релиз: str, ключи: list[str]) -> dict[str, dict]:
	"""Срезы пакета агента уроков `ключи` релиза — одной выборкой; нет урока — нет и ключа."""
	if not ключи:
		return {}
	return {
		у.lesson_key: _из_json(у.agent)
		for у in frappe.get_all(
			УРОК,
			filters={"parenttype": РЕЛИЗ, "parent": релиз, "lesson_key": ("in", list(set(ключи)))},
			fields=["lesson_key", "agent"],
		)
	}


def рамка(релиз: str) -> dict:
	"""Рамка пакета агента релиза: `frame`, `learn_about_student`, какие есть; нет — `{}`."""
	return _из_json(frappe.db.get_value(РЕЛИЗ, релиз, "agent_frame"))


def ключи_выяснять(релиз: str) -> list[str]:
	"""Ключи «что выяснять об ученике» (`learn_about_student[].key`) по порядку пакета.

	Список не тот — пусто; элемент без строкового ключа пропускается: пакет
	собирает компилятор, и его форму приложение не проверяет.
	"""
	return ключи_рамки(рамка(релиз))


def ключи_рамки(рамка_курса: dict) -> list[str]:
	"""`ключи_выяснять` по рамке, уже прочитанной `рамка`."""
	что_выяснять = рамка_курса.get("learn_about_student")
	if not isinstance(что_выяснять, list):
		return []
	return [э["key"] for э in что_выяснять if isinstance(э, dict) and _ключ(э.get("key"))]


def _ключ(значение) -> bool:
	return isinstance(значение, str) and bool(значение)


#: Поля записи релиза, которые отдают `сведения` и `история`.
ПОЛЯ_РЕЛИЗА = ["name", "course_key", "version", "published_at", "published_by", "digest", "document_key"]


def сведения(релиз: str):
	"""Запись релиза без индекса и снимка: версия, кто и когда опубликовал, дайджест, документ."""
	return frappe.db.get_value(РЕЛИЗ, релиз, ПОЛЯ_РЕЛИЗА, as_dict=True)


def история(курс: str) -> list:
	"""Релизы курса — те же поля, что у `сведения`, — свежие вперёд, одной выборкой."""
	return frappe.get_all(РЕЛИЗ, filters={"course": курс}, fields=ПОЛЯ_РЕЛИЗА, order_by="version desc")


def снимок(релиз: str) -> dict:
	"""Релиз целиком, как опубликован: части `agent` и `map` — как есть."""
	return json.loads(frappe.db.get_value(РЕЛИЗ, релиз, "snapshot"))
