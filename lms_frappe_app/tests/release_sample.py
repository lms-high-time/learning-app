# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Синтетический релиз курса для тестов (learning-services#500).

Тексты — заглушки, `map` — непрозрачная пустышка: содержание настоящих
курсов в открытый репозиторий не кладётся (CLAUDE.md §10). Форма —
`agent_learning/releases/release.public.schema.json`; пакет агента — по
формату релиза («Пакет агента»): рамка, что выяснять, срез каждого урока.
"""

import copy


def _урок(ключ: str, глава: str, название: str, разделы: list[str], домашка: dict | None = None) -> dict:
	цель = f"{ключ}-D1"
	вопрос = f"S1/{цель}"
	return {
		"key": ключ,
		"chapter": глава,
		"title": название,
		"hook": f"Зачин урока «{название}»",
		"sections": разделы,
		"objectives": [
			{
				"key": цель,
				"text": f"Цель урока «{название}»",
				"goals": [
					{"key": "term:T1", "kind": "term", "required": True, "title": "Термин «пример»"},
					{"key": f"{цель}/V1", "kind": "variant", "required": True, "title": "Выбор: «первый»"},
					{
						"key": "refute:M1",
						"kind": "misconception",
						"required": False,
						"title": "Если проявится: «пример не нужен»",
					},
				],
			}
		],
		"quiz": {
			"pass_percentage": 70,
			"questions": [
				{
					"key": вопрос,
					"objective": цель,
					"text": "Ситуация и вопрос",
					"options": [{"key": "V1", "text": "Первый"}, {"key": "V2", "text": "Второй"}],
				}
			],
			"answers": {вопрос: {"correct": "V1", "explanation": "Потому что так велит условие."}},
		},
		"homework": домашка,
	}


def _срез_урока(ключ: str, разделы: list[str]) -> dict:
	"""Срез пакета агента для урока `_урок`: подробности каждого его пункта и подсказки по разделам."""
	return {
		"directive": f"Директива урока {ключ}: эпизод, решение {ключ}-D1 и его варианты.",
		"material": f"Тезисы урока {ключ} по решению {ключ}-D1.",
		"items": {
			"term:T1": "Термин «пример» и его определение.",
			f"{ключ}-D1/V1": "Что сделать ученику и ситуации практики, где ожидается «первый».",
			"refute:M1": "Заблуждение «пример не нужен» и опровергающие тезисы.",
		},
		"sections": {раздел: f"Урок {ключ} заполняет раздел {раздел}." for раздел in разделы},
	}


_РЕЛИЗ = {
	"format": "lms-release/1",
	"course": {
		"key": "sample-course",
		"title": "Пример курса",
		"summary": "Короткая карточка",
		"description": "Описание курса для его страницы.",
		"promise": "К концу курса вы умеете пример.",
		"goal": "Уметь пример",
		"attribution": {
			"name": "Пример",
			"authors": "Авторы",
			"license": "CC BY-SA 4.0",
			"url": "https://example.com",
		},
		"glossary": [{"key": "T1", "term": "пример", "definition": "Пример — то, что показывает."}],
	},
	"chapters": [
		{
			"key": "ch-1",
			"title": "Глава первая",
			"description": "Что изменится после первой главы.",
			"lessons": ["l-1", "l-2"],
		},
		{
			"key": "ch-2",
			"title": "Глава вторая",
			"description": "Что изменится после второй.",
			"lessons": ["l-3"],
		},
	],
	"lessons": [
		_урок("l-1", "ch-1", "Урок первый", ["log"]),
		_урок("l-2", "ch-1", "Урок второй", ["log", "rules"]),
		_урок(
			"l-3",
			"ch-2",
			"Урок третий",
			[],
			{"title": "Задание", "description": "Сделайте пример.", "answer_mode": "text", "due_days": 3},
		),
	],
	"document": {
		"key": "notebook",
		"title": "Тетрадь",
		"purpose": "Зачем ученику тетрадь.",
		"sections": [
			{
				"key": "log",
				"title": "Журнал",
				"description": "Что сюда записывают.",
				"rows": "many",
				"columns": [
					{"key": "topic", "title": "Тема", "required": True},
					{"key": "decision", "title": "Решение", "required": False},
					{"key": "responsible", "title": "Кто отвечает", "required": {"if_column": "decision"}},
				],
			},
			{
				"key": "rules",
				"title": "Правила",
				"description": "Как решаем.",
				"rows": "one",
				"columns": [{"key": "scope", "title": "Что решаем", "required": True}],
			},
		],
	},
	"agent": {
		"frame": "Рамка курса: ученик, граница, контур ведения.",
		"learn_about_student": [
			{"key": "where_applies", "text": "Где ученик применяет курс.", "changes": False},
			{"key": "c-team", "text": "Сколько людей в команде ученика.", "changes": True},
		],
		"lessons": {
			"l-1": _срез_урока("l-1", ["log"]),
			"l-2": _срез_урока("l-2", ["log", "rules"]),
			"l-3": _срез_урока("l-3", []),
		},
	},
	"map": {"opaque": True},
}


def пример_релиза(ключ: str | None = None) -> dict:
	"""Свежая копия: тест правит её как хочет. `ключ` — ключ курса."""
	релиз = copy.deepcopy(_РЕЛИЗ)
	if ключ:
		релиз["course"]["key"] = ключ
	return релиз


def пример_карты(хвост: str = "") -> dict:
	"""Карта курса в форме компилятора — заглушки (learning-services#560).

	Урок `l-1`: решение выбора `D1` (вариант `V1b` «никогда» с ловушкой
	`E1`), решение с результатом `D2` (требование `K1`), ситуация практики
	`S1` с набором решения другого урока `D9` и ситуация зачёта `S2`, цель
	`G1`. Тезисы урока: свой `T4`, `T1` с опорой `T0`, `T2`, `T3` —
	опровержение `M1`, которого нет среди тезисов урока; заблуждения `M1`,
	`M2` — из причин; `M3` только опровергает `T1`, и в урок оно не входит. У `D1` и урока карты —
	поле, которого кабинет не знает (`weight`, `extra_field`). `хвост`
	дописывается к каждому ключу и тексту карты: ссылки при этом сходятся.
	"""

	def к(ключ: str) -> str:
		return ключ + хвост

	def т(текст: str) -> str:
		return f"{текст}{хвост}"

	return {
		"goals": {к("G1"): {"text": т("Цель карты")}},
		"decisions": {
			к("D1"): {
				"text": т("Решение первое"),
				"serves": к("G1"),
				"relies_on": [к("T1")],
				"weight": 3,
				"variants": {
					к("V1a"): {
						"text": т("Вариант годный"),
						"gives": т("Что даёт вариант"),
						"costs": т("Чего стоит вариант"),
						"unfit_when": т("Когда вариант не подходит"),
					},
					к("V1b"): {
						"text": т("Вариант запретный"),
						"never": True,
						"tempts": т("Чем тянет вариант"),
						"costs": т("Цена запретного"),
						"errors": {
							к("E1"): {
								"when": т("Когда спешат"),
								"causes": [{"kind": "thinks_otherwise", "misconception": к("M1")}],
								"consequence": т("Последствие ловушки"),
							}
						},
					},
				},
			},
			к("D2"): {
				"text": т("Решение с результатом"),
				"serves": к("G1"),
				"relies_on": [к("T2")],
				"uses": [{"choice": к("D1")}],
				"result": к("R1"),
			},
			к("D9"): {"text": т("Решение другого урока"), "variants": {к("V9a"): {"text": т("Вариант другого урока")}}},
		},
		"results": {
			к("R1"): {
				"text": т("Результат решения"),
				"sample": т("Образец результата"),
				"requirements": {
					к("K1"): {
						"text": т("Требование к результату"),
						"relies_on": [к("T2")],
						"violation": {
							"text": т("Нарушение требования"),
							"causes": [
								{"kind": "not_know", "thesis": к("T2")},
								{"kind": "thinks_otherwise", "misconception": к("M2")},
							],
							"consequence": т("Последствие нарушения"),
						},
					}
				},
			}
		},
		"theses": {
			к("T0"): {"text": т("Тезис-опора"), "kind": "rule", "origin": {"kind": "own"}},
			к("T1"): {
				"text": т("Тезис первый"),
				"kind": "definition",
				"relies_on": [к("T0")],
				"refutes": [к("M2"), к("M3")],
				"origin": {"kind": "adapted", "page": т("источник-цитаты"), "quote": т("Цитата источника")},
			},
			к("T2"): {"text": т("Тезис второй"), "kind": "rule", "origin": {"kind": "own", "note": т("Заметка о происхождении")}},
			к("T3"): {"text": т("Тезис-опровержение"), "kind": "claim", "refutes": [к("M1")], "origin": {"kind": "own"}},
			к("T4"): {"text": т("Тезис урока"), "kind": "claim", "origin": {"kind": "own"}},
		},
		"misconceptions": {
			к("M1"): {"text": т("Заблуждение первое")},
			к("M2"): {"text": т("Заблуждение второе"), "note": т("Примечание к заблуждению")},
			к("M3"): {"text": т("Заблуждение чужое")},
		},
		"situations": {
			к("S1"): {
				"text": т("Ситуация практики"),
				"role": т("Роль ученика"),
				"conditions": [т("Условие первое"), т("Условие второе")],
				"sets": [
					{"decision": к("D1"), "expected": к("V1a"), "deciding": т("Решающие условия практики")},
					{
						"decision": к("D9"),
						"key": "set-1",
						"about": т("О чём этот набор"),
						"expected": к("V9a"),
						"deciding": т("Решающие условия другого урока"),
					},
				],
				"traps": [к("E1"), к("K1")],
			},
			к("S2"): {
				"text": т("Ситуация зачёта"),
				"role": т("Роль на зачёте"),
				"conditions": [т("Условие зачёта")],
				"sets": [{"decision": к("D1"), "expected": к("V1a"), "deciding": т("Решающие условия зачёта")}],
				"traps": [к("E1")],
			},
		},
		"lessons": {
			"l-1": {
				"title": "Урок первый",
				"decisions": [к("D1"), к("D2")],
				"theses": [к("T4")],
				"situations": [к("S1")],
				"quiz": [к("S2")],
				"extra_field": т("Поле урока карты"),
			}
		},
	}


def релиз_двух_целей(ключ: str | None = None, *, порог: float = 70, вопросов: int = 2) -> dict:
	"""Курс из одного урока `l-1` с двумя целями — для прохождения и квиза (learning-services#504).

	У цели `l-1-D1` два обязательных пункта (`term:T1`, `exec:E1`) и
	необязательный (`refute:M1`); у `l-1-D2` — только необязательный
	(`return:R1`): она разобрана сразу. Все `вопросов` вопросов — на `l-1-D1`,
	верный вариант у каждого `V1`; `порог` — `pass_percentage` урока. Документа нет;
	в пакете агента — только срез урока, без рамки.
	"""
	вопросы = [f"S{номер}/l-1-D1" for номер in range(1, вопросов + 1)]
	return {
		"format": "lms-release/1",
		"course": {
			"key": ключ or "two-objectives",
			"title": "Курс с двумя целями",
			"summary": "Короткая карточка",
			"description": "Описание курса для его страницы.",
			"promise": "",
			"goal": "Уметь пример",
			"attribution": None,
			"glossary": [],
		},
		"chapters": [
			{"key": "ch-1", "title": "Глава", "description": "Что изменится после главы.", "lessons": ["l-1"]}
		],
		"lessons": [
			{
				"key": "l-1",
				"chapter": "ch-1",
				"title": "Урок с двумя целями",
				"hook": "Зачин урока",
				"sections": [],
				"objectives": [
					{
						"key": "l-1-D1",
						"text": "Цель с обязательными пунктами",
						"goals": [
							{"key": "term:T1", "kind": "term", "required": True, "title": "Термин «пример»"},
							{
								"key": "exec:E1",
								"kind": "execution",
								"required": True,
								"title": "Сделать пример",
							},
							{
								"key": "refute:M1",
								"kind": "misconception",
								"required": False,
								"title": "Если проявится: «пример не нужен»",
							},
						],
					},
					{
						"key": "l-1-D2",
						"text": "Цель без обязательных пунктов",
						"goals": [
							{
								"key": "return:R1",
								"kind": "return",
								"required": False,
								"title": "Вернуться к примеру",
							}
						],
					},
				],
				"quiz": {
					"pass_percentage": порог,
					"questions": [
						{
							"key": в,
							"objective": "l-1-D1",
							"text": f"Ситуация и вопрос {номер}",
							"options": [{"key": "V1", "text": "Первый"}, {"key": "V2", "text": "Второй"}],
						}
						for номер, в in enumerate(вопросы, start=1)
					],
					"answers": {
						в: {"correct": "V1", "explanation": "Потому что так велит условие."} for в in вопросы
					},
				},
				"homework": None,
			}
		],
		"document": None,
		"agent": {
			"lessons": {
				"l-1": {
					"directive": "Директива урока l-1: решения l-1-D1 и l-1-D2.",
					"material": "Тезисы урока l-1 по решениям.",
					"items": {
						"term:T1": "Термин «пример» и его определение.",
						"exec:E1": "Что исполняет ученик и ситуации практики.",
						"refute:M1": "Заблуждение «пример не нужен» и опровергающие тезисы.",
						"return:R1": "Ответ участника и следующее решение.",
					},
					"sections": {},
				}
			}
		},
		"map": {"opaque": True},
	}


def добавить_главу(релиз: dict, глава: str, уроки: list[str], домашка: dict | None = None) -> dict:
	"""Глава `глава` с уроками `уроки` — в конец релиза; уроки — по образцу, без
	разделов документа, у каждого домашка `домашка`."""
	релиз["chapters"].append(
		{"key": глава, "title": f"Глава {глава}", "description": "Что изменится.", "lessons": list(уроки)}
	)
	for ключ in уроки:
		релиз["lessons"].append(_урок(ключ, глава, f"Урок {ключ}", [], домашка))
		релиз["agent"]["lessons"][ключ] = _срез_урока(ключ, [])
	return релиз
