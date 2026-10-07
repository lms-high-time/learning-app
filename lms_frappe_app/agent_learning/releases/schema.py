# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Проверка релиза по публичной схеме (learning-services#500).

Схема — `release.public.schema.json` рядом; копия схемы — у компилятора
курса. Проверяется подмножество JSON Schema 2020-12, которым схема
пользуется; его состав — `ПОДДЕРЖАНО`, и тест валит прогон на слове вне него.

`Why:` библиотеки `jsonschema` в образе bench нет, а новая зависимость
приложения едет только пересборкой образа и деплоем (`deploy/README.md`
learning-services, «Как код попадает на стенд»). Релиз до публикации уже
проверен полной схемой у компилятора; здесь — граница от прямого вызова
метода мимо компилятора: правило сервера держится в открытом коде
(CLAUDE.md §10).
"""

import json
import re
from functools import cache
from pathlib import Path

СХЕМА = Path(__file__).with_name("release.public.schema.json")
ФОРМАТ = "lms-release/1"
#: Больше двух десятков расхождений автор всё равно не разберёт за раз.
ОШИБОК_НЕ_БОЛЬШЕ = 20

#: Слова схемы, которые понимает проверка. Аннотации ничего не проверяют и
#: стоят здесь, чтобы тест на состав их не ловил.
ПОДДЕРЖАНО = frozenset(
	{
		"$schema",
		"$id",
		"title",
		"description",
		"$defs",
		"$ref",
		"type",
		"properties",
		"required",
		"additionalProperties",
		"items",
		"minItems",
		"enum",
		"const",
		"pattern",
		"minLength",
		"maxLength",
		"minimum",
		"maximum",
		"anyOf",
		"propertyNames",
	}
)

ТИПЫ = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


@cache
def схема() -> dict:
	return json.loads(СХЕМА.read_text(encoding="utf-8"))


def ошибки(значение, узел: dict | None = None, путь: str = "$") -> list[dict]:
	"""Расхождения значения со схемой — `[{path, message}]`; пусто — подходит."""
	узел = схема() if узел is None else узел
	if "$ref" in узел:
		return ошибки(значение, _по_ссылке(узел["$ref"]), путь)
	найдено: list[dict] = []
	if "anyOf" in узел and all(ошибки(значение, вариант, путь) for вариант in узел["anyOf"]):
		найдено.append(_ошибка(путь, "не подходит ни под один вариант"))
	if "const" in узел and значение != узел["const"]:
		return [*найдено, _ошибка(путь, f"ожидается {узел['const']!r}")]
	if "enum" in узел and значение not in узел["enum"]:
		return [*найдено, _ошибка(путь, "одно из: " + ", ".join(map(str, узел["enum"])))]
	if "type" in узел and not _тип_подходит(узел["type"], значение):
		return [*найдено, _ошибка(путь, f"тип {узел['type']}")]
	if isinstance(значение, str):
		найдено += _строка(узел, значение, путь)
	elif isinstance(значение, int | float) and not isinstance(значение, bool):
		найдено += _число(узел, значение, путь)
	elif isinstance(значение, list):
		найдено += _список(узел, значение, путь)
	elif isinstance(значение, dict):
		найдено += _объект(узел, значение, путь)
	return найдено


def _по_ссылке(ссылка: str) -> dict:
	префикс = "#/$defs/"
	if not ссылка.startswith(префикс):
		raise ValueError(f"ссылка схемы вне #/$defs: {ссылка}")
	return схема()["$defs"][ссылка[len(префикс) :]]


def _тип_подходит(тип, значение) -> bool:
	if isinstance(тип, list):
		return any(_тип_подходит(т, значение) for т in тип)
	if тип == "integer":
		return isinstance(значение, int) and not isinstance(значение, bool)
	if тип == "number":
		return isinstance(значение, int | float) and not isinstance(значение, bool)
	return isinstance(значение, ТИПЫ[тип])


@cache
def _шаблон(шаблон: str) -> re.Pattern:
	# `$` в Python совпадает и перед конечным `\n`; в JSON Schema (ECMA-262) — нет.
	return re.compile(шаблон[:-1] + r"\Z" if шаблон.endswith("$") else шаблон)


def _строка(узел: dict, значение: str, путь: str) -> list[dict]:
	найдено = []
	if len(значение) < узел.get("minLength", 0):
		найдено.append(_ошибка(путь, "пустая строка" if not значение else "строка короче допустимого"))
	if "maxLength" in узел and len(значение) > узел["maxLength"]:
		найдено.append(_ошибка(путь, f"строка длиннее {узел['maxLength']}"))
	if "pattern" in узел and not _шаблон(узел["pattern"]).search(значение):
		найдено.append(_ошибка(путь, f"не по шаблону {узел['pattern']}"))
	return найдено


def _число(узел: dict, значение, путь: str) -> list[dict]:
	if "minimum" in узел and значение < узел["minimum"]:
		return [_ошибка(путь, f"меньше {узел['minimum']}")]
	if "maximum" in узел and значение > узел["maximum"]:
		return [_ошибка(путь, f"больше {узел['maximum']}")]
	return []


def _список(узел: dict, значение: list, путь: str) -> list[dict]:
	найдено = []
	if len(значение) < узел.get("minItems", 0):
		найдено.append(_ошибка(путь, f"элементов меньше {узел['minItems']}"))
	if "items" in узел:
		for номер, элемент in enumerate(значение):
			найдено += ошибки(элемент, узел["items"], f"{путь}[{номер}]")
	return найдено


def _объект(узел: dict, значение: dict, путь: str) -> list[dict]:
	найдено = [_ошибка(путь, f"нет поля «{имя}»") for имя in узел.get("required", []) if имя not in значение]
	свойства = узел.get("properties", {})
	лишние = узел.get("additionalProperties", True)
	for имя, вложенное in значение.items():
		куда = f"{путь}.{имя}"
		if "propertyNames" in узел:
			найдено += ошибки(имя, узел["propertyNames"], куда)
		if имя in свойства:
			найдено += ошибки(вложенное, свойства[имя], куда)
		elif лишние is False:
			найдено.append(_ошибка(куда, "лишнее поле"))
		elif isinstance(лишние, dict):
			найдено += ошибки(вложенное, лишние, куда)
	return найдено


def _ошибка(путь: str, текст: str) -> dict:
	return {"path": путь, "message": текст}
