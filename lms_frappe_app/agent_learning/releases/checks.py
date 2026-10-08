# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Проверки релиза сверх схемы: то, на чём держатся правила сервера (learning-services#500).

Схема проверяет форму; здесь — ключи уникальны в своих пространствах, ссылки
сходятся, у вопроса ровно один верный вариант и он из его вариантов, документ
ложится на движок документов, названия влезают в поля Learning. Коды — как у
валидатора компилятора курса: автор видит одну беду одним именем с обеих
сторон. Содержание текстов не проверяется: это методика, её держит компилятор.
"""

from lms_frappe_app.agent_learning.artifacts.schema import ЗАНЯТЫЕ_КЛЮЧИ_БЛОКОВ, КЛЮЧ

ДЛИНА_DATA = 140
#: Имя главы и урока Learning — `{####} {название}`: на номер и пробел уходит
#: пять знаков.
ДЛИНА_С_НОМЕРОМ = ДЛИНА_DATA - 5


def проблемы(релиз: dict) -> tuple[list[dict], list[dict]]:
	"""(критичные, предупреждения) — списки `{code, where, …}`. Релиз уже прошёл схему."""
	критичные: list[dict] = []
	предупреждения: list[dict] = []
	_ключи(релиз, критичные)
	_ссылки(релиз, критичные)
	_порядок_глав(релиз, критичные)
	_квиз(релиз, критичные, предупреждения)
	_документ(релиз, критичные, предупреждения)
	_длины(релиз, критичные)
	_пустые_тексты(релиз, предупреждения)
	return критичные, предупреждения


def _повторы(ключи: list[str], где: str, найдено: list) -> None:
	видел: set[str] = set()
	for ключ in ключи:
		if ключ in видел:
			найдено.append({"code": "duplicate_key", "where": где, "key": ключ})
		видел.add(ключ)


def _ключи(р: dict, к: list) -> None:
	уроки = р["lessons"]
	_повторы([г["key"] for г in р["chapters"]], "chapters", к)
	_повторы([у["key"] for у in уроки], "lessons", к)
	_повторы([ц["key"] for у in уроки for ц in у["objectives"]], "objectives", к)
	_повторы([в["key"] for у in уроки for в in у["quiz"]["questions"]], "questions", к)
	_повторы([т["key"] for т in р["course"]["glossary"]], "course.glossary", к)
	for у in уроки:
		# Пространство пункта — урок: одно заблуждение бывает пунктом в нескольких уроках.
		_повторы([п["key"] for ц in у["objectives"] for п in ц["goals"]], f"lessons[{у['key']}].goals", к)
		for в in у["quiz"]["questions"]:
			_повторы([о["key"] for о in в["options"]], f"questions[{в['key']}].options", к)
	if р["document"]:
		_повторы([с["key"] for с in р["document"]["sections"]], "document.sections", к)
		for с in р["document"]["sections"]:
			_повторы([кол["key"] for кол in с["columns"]], f"sections[{с['key']}].columns", к)


def _ссылка(к: list, где: str, ключ: str) -> None:
	к.append({"code": "broken_ref", "where": где, "key": ключ})


def _ссылки(р: dict, к: list) -> None:
	главы = {г["key"]: г for г in р["chapters"]}
	разделы = {с["key"]: с for с in (р["document"] or {}).get("sections", [])}
	уроки = {у["key"] for у in р["lessons"]}
	for г in р["chapters"]:
		for ключ in г["lessons"]:
			if ключ not in уроки:
				_ссылка(к, f"chapters[{г['key']}].lessons", ключ)
	for у in р["lessons"]:
		if у["chapter"] not in главы:
			_ссылка(к, f"lessons[{у['key']}].chapter", у["chapter"])
		elif у["key"] not in главы[у["chapter"]]["lessons"]:
			_ссылка(к, f"chapters[{у['chapter']}].lessons", у["key"])
		for раздел in у["sections"]:
			if раздел not in разделы:
				_ссылка(к, f"lessons[{у['key']}].sections", раздел)
		цели = {ц["key"] for ц in у["objectives"]}
		вопросы = {в["key"] for в in у["quiz"]["questions"]}
		for в in у["quiz"]["questions"]:
			if в["objective"] not in цели:
				_ссылка(к, f"questions[{в['key']}].objective", в["objective"])
		for лишний in sorted(set(у["quiz"]["answers"]) - вопросы):
			_ссылка(к, f"lessons[{у['key']}].quiz.answers", лишний)
	for с in разделы.values():
		колонки = {кол["key"] for кол in с["columns"]}
		for кол in с["columns"]:
			if isinstance(кол["required"], dict) and кол["required"]["if_column"] not in колонки:
				_ссылка(
					к, f"sections[{с['key']}].columns[{кол['key']}].required", кол["required"]["if_column"]
				)


def _порядок_глав(р: dict, к: list) -> None:
	по_главам = [ключ for г in р["chapters"] for ключ in г["lessons"]]
	по_урокам = [у["key"] for у in р["lessons"]]
	if по_главам != по_урокам:
		к.append({"code": "chapter_order", "where": "chapters", "expected": по_урокам, "received": по_главам})


def _квиз(р: dict, к: list, п: list) -> None:
	for у in р["lessons"]:
		ответы = у["quiz"]["answers"]
		for в in у["quiz"]["questions"]:
			где = f"questions[{в['key']}]"
			ответ = ответы.get(в["key"])
			if not ответ:
				к.append({"code": "quiz_correct", "where": где, "message": "нет ответа"})
			elif ответ["correct"] not in {о["key"] for о in в["options"]}:
				к.append(
					{"code": "quiz_correct", "where": где, "message": "верный — не вариант этого вопроса"}
				)
			elif not ответ["explanation"].strip():
				п.append({"code": "explanation_missing", "where": где})


def _документ(р: dict, к: list, п: list) -> None:
	"""Документ ложится на движок документов: ключи — `artifacts.schema.КЛЮЧ`,
	поля разделов `one` не совпадают друг с другом и с колонками таблиц."""
	д = р["document"]
	if not д:
		return
	if not КЛЮЧ.match(д["key"]):
		к.append({"code": "document_key", "where": "document.key", "key": д["key"]})
	поля: list[str] = []
	колонки: set[str] = set()
	for с in д["sections"]:
		if not КЛЮЧ.match(с["key"]) or с["key"] in ЗАНЯТЫЕ_КЛЮЧИ_БЛОКОВ:
			к.append({"code": "document_key", "where": f"sections[{с['key']}]", "key": с["key"]})
		for кол in с["columns"]:
			if not КЛЮЧ.match(кол["key"]) or кол["key"] == "id":
				к.append(
					{"code": "document_key", "where": f"sections[{с['key']}].columns", "key": кол["key"]}
				)
			if с["rows"] == "one":
				поля.append(кол["key"])
				if isinstance(кол["required"], dict):
					# Поля движка обязательны только безусловно — условие у раздела «один раз» теряется.
					п.append(
						{
							"code": "required_condition_ignored",
							"where": f"sections[{с['key']}].columns[{кол['key']}]",
						}
					)
			else:
				колонки.add(кол["key"])
	for ключ in sorted({имя for имя in поля if поля.count(имя) > 1} | (set(поля) & колонки)):
		к.append(
			{
				"code": "document_key",
				"where": "document.sections",
				"key": ключ,
				"message": "ключ поля занят другим полем или колонкой",
			}
		)


def _длины(р: dict, к: list) -> None:
	def длинно(текст: str, предел: int, где: str) -> None:
		if len(текст) > предел:
			к.append({"code": "text_too_long", "where": где, "limit": предел, "length": len(текст)})

	длинно(р["course"]["title"], ДЛИНА_DATA, "course.title")
	for г in р["chapters"]:
		длинно(г["title"], ДЛИНА_С_НОМЕРОМ, f"chapters[{г['key']}].title")
	for у in р["lessons"]:
		длинно(у["title"], ДЛИНА_С_НОМЕРОМ, f"lessons[{у['key']}].title")
		if у["homework"]:
			длинно(у["homework"]["title"], ДЛИНА_DATA, f"lessons[{у['key']}].homework.title")
	if р["document"]:
		длинно(р["document"]["title"], ДЛИНА_DATA, "document.title")
		for с in р["document"]["sections"]:
			длинно(с["title"], ДЛИНА_DATA, f"sections[{с['key']}].title")


def _пустые_тексты(р: dict, п: list) -> None:
	def пусто(текст: str, где: str) -> None:
		if not текст.strip():
			п.append({"code": "public_text_empty", "where": где})

	пусто(р["course"]["summary"], "course.summary")
	пусто(р["course"]["description"], "course.description")
	for г in р["chapters"]:
		пусто(г["description"], f"chapters[{г['key']}].description")
	if р["document"]:
		пусто(р["document"]["purpose"], "document.purpose")
		for с in р["document"]["sections"]:
			пусто(с["description"], f"sections[{с['key']}].description")
