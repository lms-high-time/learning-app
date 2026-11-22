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


class UnitTestВидПравки(UnitTestCase):
	"""Версия растёт только с признаком уровня; остальное — редакторская правка."""

	def test_первый_выпуск_всё_новое(self):
		self.assertEqual(
			checks.правки(None, пример_каталога(С), {}),
			{"added": [К["a"], К["b"], К["whole"]], "substantive": [], "editorial": []},
		)

	def test_вид_правки_по_полю(self):
		прежний = пример_каталога(С)
		версии = {К["a"]: 3, К["b"]: 1, К["whole"]: 1}
		for случай, правка, ожидаемое in (
			("без изменений", lambda к: None, {"added": [], "substantive": [], "editorial": []}),
			(
				"признак уровня",
				lambda к: к["competencies"][0]["levels"].update({"3": "Новый признак"}),
				{"added": [], "substantive": [{"key": К["a"], "version": 4}], "editorial": []},
			),
			*(
				(
					f"редакторская: {поле}",
					правка,
					{"added": [], "substantive": [], "editorial": [К["a"]]},
				)
				for поле, правка in (
					("название", lambda к: к["competencies"][0].update(title="Другое")),
					("описание", lambda к: к["competencies"][0].update(description="Другое")),
					("область", lambda к: к["competencies"][0].update(area="other-area")),
					("похожие", lambda к: к["competencies"][0].pop("similar")),
				)
			),
			(
				"редакторская: составляющие",
				lambda к: к["competencies"][2]["parts"][1].update(from_level=2),
				{"added": [], "substantive": [], "editorial": [К["whole"]]},
			),
			(
				# `3.0` — то же целое, что `3`: схема принимает его за целое.
				"уровень составляющей 3.0",
				lambda к: к["competencies"][2]["parts"][1].update(from_level=3.0),
				{"added": [], "substantive": [], "editorial": []},
			),
			(
				"пустые связи — то же, что их нет",
				lambda к: к["competencies"][1].update(parts=[], similar=[]),
				{"added": [], "substantive": [], "editorial": []},
			),
			(
				"новая рядом с правкой по существу",
				lambda к: к["competencies"].append(компетенция("fresh-one", К["area"]))
				or к["competencies"][1]["levels"].update({"1": "Новый"}),
				{"added": ["fresh-one"], "substantive": [{"key": К["b"], "version": 2}], "editorial": []},
			),
		):
			with self.subTest(случай):
				новый = copy.deepcopy(прежний)
				правка(новый)
				self.assertEqual(checks.правки(прежний, новый, версии), ожидаемое)
