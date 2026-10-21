# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Проверки релиза сверх схемы без базы (learning-services#500)."""

import unittest

from lms_frappe_app.agent_learning.releases import checks
from lms_frappe_app.tests.release_sample import пример_релиза


def _коды(релиз) -> list[str]:
	return [п["code"] for п in checks.проблемы(релиз)[0]]


def _ответ_первого_вопроса(р) -> dict:
	квиз = р["lessons"][0]["quiz"]
	return квиз["answers"][квиз["questions"][0]["key"]]


def _раздел_с_ключом(р, ключ: str) -> None:
	р["document"]["sections"][0]["key"] = ключ
	р["lessons"][0]["sections"] = р["lessons"][1]["sections"] = [ключ]


def _без_третьего_урока(р) -> None:
	"""Урок снят из уроков и из главы, а его срез в пакете остался."""
	р["chapters"] = р["chapters"][:1]
	р["lessons"] = р["lessons"][:2]


def _пакет_неполный(р) -> None:
	del р["agent"]["lessons"]["l-1"]
	del р["agent"]["lessons"]["l-2"]["items"]["term:T1"]
	р["agent"]["lessons"]["l-2"]["sections"] = ["не", "объект"]
	р["agent"]["lessons"]["l-3"] = "срез строкой"


class TestПроверкиРелиза(unittest.TestCase):
	def test_релиз_без_проблем(self):
		"""Правки, после которых релиз чист: ни критичных проблем, ни предупреждений."""
		for случай, правка in {
			"пример как есть": lambda р: None,
			# Раздел `rules` в документе есть, урок `l-1` в него не пишет: ссылка сходится.
			"запись раздела документа, который урок не заполняет": lambda р: р["agent"]["lessons"]["l-1"][
				"sections"
			].update(rules="Запись раздела в срезе."),
			# Пакет непрозрачен: полноты сервер не требует, части не-объекты не разбирает.
			"пакет неполный, части иной формы": _пакет_неполный,
			"уроки пакета не объект": lambda р: р["agent"].update(lessons=["не", "объект"]),
			# Ключи с тем же смыслом в тексте или в карте курса — не утечка.
			"answers и correct вне пакета агента": lambda р: р.update(
				agent={**р["agent"], "frame": "answers и correct — слова, а не ключи"},
				map={"answers": {"correct": "V1"}},
			),
			"релиз без пакета агента": lambda р: р.pop("agent"),
		}.items():
			with self.subTest(случай):
				р = пример_релиза()
				правка(р)
				self.assertEqual(checks.проблемы(р), ([], []))
		# term:T1 и refute:M1 стоят в каждом уроке примера — пространство пункта — урок.
		self.assertNotIn("duplicate_key", _коды(пример_релиза()))

	def test_критичная_проблема_по_правке(self):
		for случай, правка, код in (
			("повтор ключа урока", lambda р: р["lessons"][1].update(key="l-1"), "duplicate_key"),
			(
				"вопрос на чужую цель",
				lambda р: р["lessons"][0]["quiz"]["questions"][0].update(objective="l-2-D1"),
				"broken_ref",
			),
			(
				"раздел урока не из документа",
				lambda р: р["lessons"][0].update(sections=["nope"]),
				"broken_ref",
			),
			(
				"уроки глав не в порядке уроков",
				lambda р: р["chapters"][0].update(lessons=["l-2", "l-1"]),
				"chapter_order",
			),
			(
				"верный ответ не из вариантов",
				lambda р: _ответ_первого_вопроса(р).update(correct="V9"),
				"quiz_correct",
			),
			("вопрос без ответа", lambda р: р["lessons"][0]["quiz"].update(answers={}), "quiz_correct"),
			("ключ раздела не ложится на документ", lambda р: _раздел_с_ключом(р, "Log-1"), "document_key"),
			(
				"поле раздела one совпадает с колонкой",
				lambda р: р["document"]["sections"][1]["columns"][0].update(key="topic"),
				"document_key",
			),
			("длинное название урока", lambda р: р["lessons"][0].update(title="у" * 136), "text_too_long"),
			# Имя главы Learning — тоже `{####} {название}`.
			("длинное название главы", lambda р: р["chapters"][0].update(title="г" * 136), "text_too_long"),
		):
			with self.subTest(случай):
				р = пример_релиза()
				правка(р)
				self.assertIn(код, _коды(р))

	def test_битая_ссылка_пакета_агента(self):
		"""Ссылка пакета, которой нет в релизе, — `broken_ref` с местом и ключом."""
		for случай, правка, проблема in (
			(
				"хвост снятого урока в пакете агента",
				_без_третьего_урока,
				{"code": "broken_ref", "where": "agent.lessons", "key": "l-3"},
			),
			(
				# Пункт другого урока — тоже чужой: пространство пункта — урок.
				"пункт пакета не из целей урока",
				lambda р: р["agent"]["lessons"]["l-1"]["items"].update(
					{"l-2-D1/V1": "Подробности пункта другого урока."}
				),
				{"code": "broken_ref", "where": "agent.lessons.l-1.items", "key": "l-2-D1/V1"},
			),
			(
				"запись раздела в срезе не из документа",
				lambda р: р["agent"]["lessons"]["l-3"]["sections"].update(
					nope="Запись раздела, которого нет."
				),
				{"code": "broken_ref", "where": "agent.lessons.l-3.sections", "key": "nope"},
			),
		):
			with self.subTest(случай):
				р = пример_релиза()
				правка(р)
				self.assertEqual(checks.проблемы(р)[0], [проблема])

	def test_запись_раздела_в_релизе_без_документа(self):
		р = пример_релиза()
		р["document"] = None
		for урок in р["lessons"]:
			урок["sections"] = []
		self.assertEqual(
			[(п["where"], п["key"]) for п in checks.проблемы(р)[0]],
			[
				("agent.lessons.l-1.sections", "log"),
				("agent.lessons.l-2.sections", "log"),
				("agent.lessons.l-2.sections", "rules"),
			],
		)

	def test_длинное_название_домашки(self):
		"""Название шаблона `Agent Lesson Homework` — Data, без номера."""
		р = пример_релиза()
		р["lessons"][2]["homework"]["title"] = "д" * 141
		self.assertIn(
			("text_too_long", "lessons[l-3].homework.title"),
			[(п["code"], п["where"]) for п in checks.проблемы(р)[0]],
		)

	def test_запрещённые_в_имени_знаки_в_названиях_глав_и_уроков(self):
		"""Имя главы и урока Learning — номер и название: `<` и `>` Frappe в имени не принимает."""
		р = пример_релиза()
		р["chapters"][0]["title"] = "Глава <первая>"
		р["lessons"][1]["title"] = "Урок a > b"
		self.assertEqual(
			[п for п in checks.проблемы(р)[0] if п["code"] == "title_forbidden_chars"],
			[
				{"code": "title_forbidden_chars", "where": "chapters[ch-1].title", "chars": ["<", ">"]},
				{"code": "title_forbidden_chars", "where": "lessons[l-2].title", "chars": [">"]},
			],
		)

	def test_знаки_в_названиях_домашки_и_документа(self):
		"""Название домашки и документа — `title_field` своего доктайпа: `<` и `>` в
		нём — тоже `title_forbidden_chars`."""
		р = пример_релиза()
		р["lessons"][2]["homework"]["title"] = "Задание <1>"
		р["document"]["title"] = "Тетрадь a < b"
		self.assertEqual(
			[(п["code"], п["where"], п["chars"]) for п in checks.проблемы(р)[0]],
			[
				("title_forbidden_chars", "lessons[l-3].homework.title", ["<", ">"]),
				("title_forbidden_chars", "document.title", ["<"]),
			],
		)

	def test_знаки_в_прочих_названиях_допустимы(self):
		"""Разделы — блоки схемы, а не записи со своим названием; курс называется
		слагом, его карточка — предупреждение `course_card_markup`."""
		р = пример_релиза()
		р["document"]["sections"][0]["title"] = "Раздел > 1"
		р["course"]["title"] = "Курс a < b"
		self.assertEqual(
			checks.проблемы(р),
			([], [{"code": "course_card_markup", "where": "course.title", "chars": ["<"]}]),
		)

	def test_пустые_тексты_ученику_предупреждения(self):
		р = пример_релиза()
		р["course"]["summary"] = ""
		р["chapters"][0]["description"] = ""
		критичные, предупреждения = checks.проблемы(р)
		self.assertEqual(критичные, [])
		self.assertEqual(
			[(п["code"], п["where"]) for п in предупреждения],
			[("public_text_empty", "course.summary"), ("public_text_empty", "chapters[ch-1].description")],
		)

	def test_ответы_квиза_в_пакете_агента(self):
		"""`answers` и `correct` на любой глубине пакета — `agent_leak`, путь — до ключа."""
		р = пример_релиза()
		р["agent"]["lessons"]["l-1"]["answers"] = {"S1/l-1-D1": "V1"}
		р["agent"]["learn_about_student"].append({"key": "x", "text": "т", "changes": [{"correct": "V1"}]})
		self.assertEqual(
			[(п["code"], п["where"]) for п in checks.проблемы(р)[0]],
			[
				("agent_leak", "agent.learn_about_student[2].changes[0].correct"),
				("agent_leak", "agent.lessons.l-1.answers"),
			],
		)

	def test_схемы_уроков(self):
		"""Схема ломается у ученика, а не при публикации: окно закрывает сеть
		(learning-services#644). Коды — как у валидатора компилятора курса."""
		for случай, правка, ожидается in (
			("нет описания", {"description": " "}, "diagram_no_description"),
			("нет названия", {"title": ""}, "diagram_no_title"),
			("длинное название", {"title": "я" * 141}, "text_too_long"),
			("пустой HTML", {"html": ""}, "diagram_empty"),
			("больше предела", {"html": "<p>" + "я" * 50_001 + "</p>"}, "diagram_too_large"),
			("шрифт с CDN", {"html": '<link href="https://fonts.example.com/a.css">'}, "diagram_external"),
			("запрос из скрипта", {"html": "<script>fetch('/x')</script>"}, "diagram_external"),
			("ключ не годится", {"key": "Task Flow"}, "diagram_shape"),
			("лишнее поле", {"extra": 1}, "diagram_shape"),
			("поле не строка", {"when": 7}, "diagram_shape"),
		):
			with self.subTest(случай):
				р = пример_релиза()
				р["agent"]["lessons"]["l-1"]["diagrams"][0].update(правка)
				self.assertIn(ожидается, _коды(р))
		р = пример_релиза()
		схемы = р["agent"]["lessons"]["l-1"]["diagrams"]
		схемы.append(dict(схемы[0]))
		р["agent"]["lessons"]["l-2"]["diagrams"] = "не список"
		схемы.append({**схемы[0], "key": "Bad Key"})
		self.assertEqual(
			sorted((п["code"], п["where"], п.get("index")) for п in checks.проблемы(р)[0]),
			[
				("diagram_shape", "agent.lessons.l-1.diagrams", 2),
				("diagram_shape", "agent.lessons.l-2.diagrams", None),
				("duplicate_key", "agent.lessons.l-1.diagrams", None),
			],
		)
		чисто = '<a href="#step">шаг</a><img src="data:image/png;base64,AA"><div data-step="1"></div>'
		р = пример_релиза()
		р["agent"]["lessons"]["l-1"]["diagrams"][0]["html"] = чисто
		self.assertEqual(checks.проблемы(р), ([], []))
