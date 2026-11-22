# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Проверка каталога компетенций и вид правки — без базы (learning-services#825)."""

import copy

from frappe.tests import UnitTestCase

from lms_frappe_app.agent_learning.catalog import checks
from lms_frappe_app.agent_learning.releases import schema
from lms_frappe_app.agent_learning.releases.test_schema import _слова
from lms_frappe_app.tests.catalog_sample import ключи_примера, компетенция, пример_каталога

С = "s1"
К = ключи_примера(С)


class UnitTestСхемаКаталога(UnitTestCase):
	def test_схема_пользуется_только_поддержанными_словами(self):
		неизвестные = _слова(checks.схема(), set()) - schema.ПОДДЕРЖАНО
		self.assertFalse(неизвестные, f"проверка не понимает: {sorted(неизвестные)}")
		self.assertEqual(checks.схема()["properties"]["format"]["const"], checks.ФОРМАТ)

	def test_образец_проходит_схему_и_проверки(self):
		каталог = пример_каталога(С)
		self.assertEqual(checks.ошибки_схемы(каталог), [])
		self.assertEqual(checks.проблемы(каталог), [])

	def test_схема_отклоняет(self):
		"""Слаг, неполные уровни, лишнее поле, уровень составляющей вне 1–4, пустое описание."""

		def с_правкой(правка):
			каталог = пример_каталога(С)
			правка(каталог["competencies"])
			return каталог

		for случай, правка, путь in (
			("слаг с заглавной", lambda к: к[0].update(key="Base-A"), "$.competencies[0].key"),
			("слаг короче трёх", lambda к: к[0].update(key="ab"), "$.competencies[0].key"),
			("нет уровня 4", lambda к: к[0]["levels"].pop("4"), "$.competencies[0].levels"),
			("уровень 5", lambda к: к[0]["levels"].update({"5": "x"}), "$.competencies[0].levels.5"),
			("пустой признак", lambda к: к[0]["levels"].update({"2": ""}), "$.competencies[0].levels.2"),
			("лишнее поле", lambda к: к[0].update(context="курс"), "$.competencies[0].context"),
			("пустое описание", lambda к: к[0].update(description=""), "$.competencies[0].description"),
			(
				"составляющая с уровня 0",
				lambda к: к[2]["parts"][0].update(from_level=0),
				"$.competencies[2].parts[0].from_level",
			),
		):
			with self.subTest(случай):
				self.assertEqual([о["path"] for о in checks.ошибки_схемы(с_правкой(правка))], [путь])

	def test_проверки_сверх_схемы(self):
		def проблемы(правка) -> list[tuple]:
			каталог = пример_каталога(С)
			правка(каталог)
			return [(п["code"], п["where"]) for п in checks.проблемы(каталог)]

		def добавить(каталог, комп):
			каталог["competencies"].append(комп)

		for случай, правка, ожидаемое in (
			(
				"повтор ключа",
				lambda к: добавить(к, компетенция(К["a"], К["area"])),
				[("duplicate_key", "competencies")],
			),
			(
				"повтор области",
				lambda к: к["areas"].append(dict(к["areas"][0])),
				[("duplicate_key", "areas")],
			),
			(
				"неизвестная область",
				lambda к: к["competencies"][0].update(area="nowhere"),
				[("broken_ref", f"competencies[{К['a']}].area")],
			),
			(
				"похожая в никуда",
				lambda к: к["competencies"][1].update(similar=["nowhere"]),
				[("broken_ref", f"competencies[{К['b']}].similar")],
			),
			(
				"похожа на себя",
				lambda к: к["competencies"][1].update(similar=[К["b"]]),
				[("competency_similar", f"competencies[{К['b']}].similar")],
			),
			(
				"составляющая в никуда",
				lambda к: к["competencies"][2]["parts"].append({"competency": "nowhere", "from_level": 2}),
				[("broken_ref", f"competencies[{К['whole']}].parts")],
			),
			(
				"у базовой составляющие",
				lambda к: к["competencies"][0].update(parts=[{"competency": К["b"], "from_level": 1}]),
				[("competency_parts", f"competencies[{К['a']}].parts")],
			),
			(
				"круг связей",
				lambda к: добавить(
					к,
					компетенция(
						"loop-x",
						К["area"],
						parts=[
							{"competency": "loop-y", "from_level": 1},
							{"competency": К["a"], "from_level": 1},
						],
					),
				)
				or добавить(
					к,
					компетенция(
						"loop-y",
						К["area"],
						parts=[
							{"competency": "loop-x", "from_level": 1},
							{"competency": К["b"], "from_level": 1},
						],
					),
				),
				[("competency_cycle", "competencies")],
			),
		):
			with self.subTest(случай):
				self.assertEqual(проблемы(правка), ожидаемое)


def _правки(added=(), substantive=(), editorial=(), areas_added=(), renamed=()) -> dict:
	return {
		"added": list(added),
		"substantive": list(substantive),
		"editorial": list(editorial),
		"areas": {"added": list(areas_added), "renamed": list(renamed)},
	}


class UnitTestКанон(UnitTestCase):
	def test_перестановка_и_пустые_связи_не_меняют_канон(self):
		"""Порядок компетенций, областей, составляющих и похожих, пустые связи и `3.0` вместо
		`3` — тот же канонический вид, а значит тот же дайджест и снимок."""
		каталог = пример_каталога(С)
		переставленный = copy.deepcopy(каталог)
		переставленный["competencies"].reverse()
		целое = переставленный["competencies"][0]
		целое["parts"].reverse()
		целое["parts"][0]["from_level"] = 3.0
		переставленный["competencies"][1].update(parts=[], similar=[])
		self.assertEqual(checks.канон(переставленный), checks.канон(каталог))
		self.assertEqual(
			[к["key"] for к in checks.канон(каталог)["competencies"]],
			sorted(К[к] for к in ("a", "b", "whole")),
		)
		self.assertNotIn("parts", checks.канон(каталог)["competencies"][1])


class UnitTestВидПравки(UnitTestCase):
	"""Версия растёт только с признаком уровня; остальное — редакторская правка с её полями."""

	def test_первый_выпуск_всё_новое(self):
		self.assertEqual(
			checks.правки(None, checks.канон(пример_каталога(С)), {}, {}),
			_правки(added=[К["a"], К["b"], К["whole"]], areas_added=[К["area"]]),
		)

	def test_новая_по_базе_а_не_по_снимку(self):
		"""Компетенция в базе, но не в прежнем снимке, — не новая: её запись уже есть, и вставка
		упёрлась бы в ключ. Сравнить её не с чем — правка по существу."""
		новый = checks.канон(пример_каталога(С))
		прежний = copy.deepcopy(новый)
		прежний["competencies"] = [к for к in прежний["competencies"] if к["key"] != К["b"]]
		версии = {К["a"]: 1, К["b"]: 2, К["whole"]: 1}
		self.assertEqual(
			checks.правки(прежний, новый, версии, {К["area"]: f"Область {С}"}),
			_правки(substantive=[{"key": К["b"], "version": 3}]),
		)

	def test_вид_правки_по_полю(self):
		прежний = checks.канон(пример_каталога(С))
		версии = {К["a"]: 3, К["b"]: 1, К["whole"]: 1}
		области = {К["area"]: f"Область {С}"}

		def комп(к, ключ):
			return next(к_ for к_ in к["competencies"] if к_["key"] == К[ключ])

		for случай, правка, ожидаемое in (
			("без изменений", lambda к: None, _правки()),
			(
				"признак уровня",
				lambda к: комп(к, "a")["levels"].update({"3": "Новый признак"}),
				_правки(substantive=[{"key": К["a"], "version": 4}]),
			),
			(
				"признак уровня — только пробелы",
				lambda к: комп(к, "a")["levels"].update(
					{"3": "  " + комп(к, "a")["levels"]["3"].replace(" ", "\n ") + " "}
				),
				_правки(editorial=[{"key": К["a"], "fields": ["levels"]}]),
			),
			*(
				(f"редакторская: {поле}", правка, _правки(editorial=[{"key": К["a"], "fields": [поле]}]))
				for поле, правка in (
					("title", lambda к: комп(к, "a").update(title="Другое")),
					("description", lambda к: комп(к, "a").update(description="Другое")),
					("area", lambda к: комп(к, "a").update(area="other-area")),
					("similar", lambda к: комп(к, "a").pop("similar")),
				)
			),
			(
				"редакторская: составляющие",
				lambda к: комп(к, "whole")["parts"][1].update(from_level=2),
				_правки(editorial=[{"key": К["whole"], "fields": ["parts"]}]),
			),
			(
				"редакторская: тип и состав составляющих",
				lambda к: комп(к, "whole").update(kind="base") or комп(к, "whole").pop("parts"),
				_правки(editorial=[{"key": К["whole"], "fields": ["kind", "parts"]}]),
			),
			(
				"новая рядом с правкой по существу",
				lambda к: к["competencies"].append(компетенция("fresh-one", К["area"]))
				or комп(к, "b")["levels"].update({"1": "Новый"}),
				_правки(added=["fresh-one"], substantive=[{"key": К["b"], "version": 2}]),
			),
			(
				"области: новая и переименованная",
				lambda к: к["areas"].append({"key": "fresh-area", "title": "Новая"})
				or к["areas"][0].update(title="Другое название"),
				_правки(areas_added=["fresh-area"], renamed=[К["area"]]),
			),
		):
			with self.subTest(случай):
				новый = copy.deepcopy(прежний)
				правка(новый)
				self.assertEqual(checks.правки(прежний, checks.канон(новый), версии, области), ожидаемое)
