# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Синтетический релиз курса для тестов (learning-services#500).

Тексты — заглушки, `agent` и `map` — непрозрачные пустышки: содержание
настоящих курсов в открытый репозиторий не кладётся (CLAUDE.md §10).
Форма — `agent_learning/releases/release.public.schema.json`.
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
	"agent": {"opaque": True},
	"map": {"opaque": True},
}


def пример_релиза(ключ: str | None = None) -> dict:
	"""Свежая копия: тест правит её как хочет. `ключ` — ключ курса."""
	релиз = copy.deepcopy(_РЕЛИЗ)
	if ключ:
		релиз["course"]["key"] = ключ
	return релиз


def релиз_двух_целей(ключ: str | None = None, *, порог: float = 70, вопросов: int = 2) -> dict:
	"""Курс из одного урока `l-1` с двумя целями — для прохождения и квиза (learning-services#504).

	У цели `l-1-D1` два обязательных пункта (`term:T1`, `exec:E1`) и
	необязательный (`refute:M1`); у `l-1-D2` — только необязательный
	(`return:R1`): она разобрана сразу. Все `вопросов` вопросов — на `l-1-D1`,
	верный вариант у каждого `V1`; `порог` — `pass_percentage` урока. Документа нет.
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
		"agent": {"opaque": True},
		"map": {"opaque": True},
	}
