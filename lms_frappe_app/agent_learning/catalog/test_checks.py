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
		"""Слаг, неполные уровни, лишнее поле, уровень составляющей вне 1–4, пустое описание,
		класс не слагом."""

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
				"ключ схемы не слаг",
				lambda к: к[1]["classes"].update({"Nos": "people-x"}),
				"$.competencies[1].classes.Nos",
			),
			(
				"класс не слаг",
				lambda к: к[1]["classes"].update({К["nos"]: 7}),
				f"$.competencies[1].classes.{К['nos']}",
			),
			(
				"составляющая с уровня 0",
				lambda к: к[2]["parts"][0].update(from_level=0),
				"$.competencies[2].parts[0].from_level",
			),
		):
			with self.subTest(случай):
				self.assertEqual([о["path"] for о in checks.ошибки_схемы(с_правкой(правка))], [путь])

	def test_схема_отклоняет_схему_классификации(self):
		"""Схема без классов, без поля первоисточника, первоисточник не адресом, `required`
		не булево, лишнее поле. Пустой список первоисточников — своя схема — проходит."""
		for случай, правка, путь in (
			("без классов", lambda с: с.update(classes=[]), "$.classifications[0].classes"),
			("нет source", lambda с: с.pop("source"), "$.classifications[0]"),
			(
				"первоисточник не адрес",
				lambda с: с.update(source=["где-то"]),
				"$.classifications[0].source[0]",
			),
			("required строкой", lambda с: с.update(required="yes"), "$.classifications[0].required"),
			("нет required", lambda с: с.pop("required"), "$.classifications[0]"),
			("лишнее поле", lambda с: с.update(checked_by="кто-то"), "$.classifications[0].checked_by"),
			(
				"класс без названия",
				lambda с: с["classes"][0].pop("title"),
				"$.classifications[0].classes[0]",
			),
		):
			with self.subTest(случай):
				каталог = пример_каталога(С)
				правка(каталог["classifications"][0])
				self.assertEqual([о["path"] for о in checks.ошибки_схемы(каталог)], [путь])
		каталог = пример_каталога(С)
		self.assertEqual(каталог["classifications"][0]["source"], [])
		self.assertEqual(checks.ошибки_схемы(каталог), [])

	def test_каталог_без_схем_и_классов_проходит(self):
		каталог = пример_каталога(С)
		каталог["classifications"] = []
		for комп in каталог["competencies"]:
			комп.pop("classes")
		self.assertEqual(checks.ошибки_схемы(каталог), [])
		self.assertEqual(checks.проблемы(каталог), [])

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
				lambda к: добавить(к, компетенция(К["a"], К["classes"])),
				[("duplicate_key", "competencies")],
			),
			(
				"повтор схемы",
				lambda к: к["classifications"].append(copy.deepcopy(к["classifications"][0])),
				[("duplicate_key", "classifications")],
			),
			(
				"повтор класса в схеме",
				lambda к: к["classifications"][1]["classes"].append({"key": К["self"], "title": "Ещё"}),
				[("duplicate_key", f"classifications[{К['nos']}].classes")],
			),
			(
				"повтор первоисточника",
				lambda к: к["classifications"][1]["source"].append(к["classifications"][1]["source"][0]),
				[("duplicate_key", f"classifications[{К['nos']}].source")],
			),
			(
				"неизвестная схема",
				lambda к: к["competencies"][0]["classes"].update({"nowhere": К["x"]}),
				[("broken_ref", f"competencies[{К['a']}].classes")],
			),
			(
				"класс не из своей схемы",
				lambda к: к["competencies"][0]["classes"].update({К["nos"]: К["x"]}),
				[("broken_ref", f"competencies[{К['a']}].classes.{К['nos']}")],
			),
			(
				"нет класса в обязательной схеме",
				lambda к: к["competencies"][1].pop("classes"),
				[("class_required", f"competencies[{К['b']}].classes")],
			),
			(
				"схема стала обязательной",
				lambda к: к["classifications"][1].update(required=True),
				[
					("class_required", f"competencies[{К['b']}].classes"),
					("class_required", f"competencies[{К['whole']}].classes"),
				],
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
				lambda к: (
					добавить(
						к,
						компетенция(
							"loop-x",
							К["classes"],
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
							К["classes"],
							parts=[
								{"competency": "loop-x", "from_level": 1},
								{"competency": К["b"], "from_level": 1},
							],
						),
					)
				),
				[("competency_cycle", "competencies")],
			),
		):
			with self.subTest(случай):
				self.assertEqual(проблемы(правка), ожидаемое)


def _правки(
	added=(), substantive=(), editorial=(), схемы=(), изменённые=(), классы=None, переименованные=None
) -> dict:
	return {
		"added": list(added),
		"substantive": list(substantive),
		"editorial": list(editorial),
		"classifications": {
			"added": list(схемы),
			"changed": list(изменённые),
			"classes": {"added": классы or {}, "renamed": переименованные or {}},
		},
	}


def _опубликованные(каталог: dict) -> dict:
	"""Схемы классификации канонического каталога — как их отдаёт база публикации."""
	return {с["key"]: с for с in каталог["classifications"]}


class UnitTestКанон(UnitTestCase):
	def test_перестановка_и_пустые_связи_не_меняют_канон(self):
		"""Порядок компетенций, схем, их классов, классов компетенции, составляющих и похожих,
		пустые связи и `3.0` вместо `3` — тот же канонический вид, а значит тот же дайджест и
		снимок. Порядок первоисточников — как записан."""
		каталог = пример_каталога(С)
		переставленный = copy.deepcopy(каталог)
		переставленный["classifications"].reverse()
		переставленный["classifications"][0]["classes"].reverse()
		переставленный["competencies"].reverse()
		переставленный["competencies"][2]["classes"] = dict(
			reversed(list(переставленный["competencies"][2]["classes"].items()))
		)
		целое = переставленный["competencies"][0]
		целое["parts"].reverse()
		целое["parts"][0]["from_level"] = 3.0
		переставленный["competencies"][1].update(parts=[], similar=[], classes={})
		без_классов = copy.deepcopy(каталог)
		без_классов["competencies"][1].pop("classes")
		self.assertEqual(checks.канон(переставленный), checks.канон(без_классов))
		self.assertEqual(checks.канон(каталог), каталог, "образец — в каноническом виде")
		источники = copy.deepcopy(каталог)
		источники["classifications"][1]["source"].reverse()
		self.assertNotEqual(checks.канон(источники), checks.канон(каталог))
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
			_правки(
				added=[К["a"], К["b"], К["whole"]],
				схемы=[К["scheme"], К["nos"]],
				классы={К["scheme"]: [К["x"], К["y"]], К["nos"]: [К["people"], К["self"]]},
			),
		)

	def test_новая_по_базе_а_не_по_снимку(self):
		"""Компетенция в базе, но не в прежнем снимке, — не новая: её запись уже есть, и вставка
		упёрлась бы в ключ. Сравнить её не с чем — правка по существу."""
		новый = checks.канон(пример_каталога(С))
		прежний = copy.deepcopy(новый)
		прежний["competencies"] = [к for к in прежний["competencies"] if к["key"] != К["b"]]
		версии = {К["a"]: 1, К["b"]: 2, К["whole"]: 1}
		self.assertEqual(
			checks.правки(прежний, новый, версии, _опубликованные(новый)),
			_правки(substantive=[{"key": К["b"], "version": 3}]),
		)

	def test_вид_правки_по_полю(self):
		прежний = checks.канон(пример_каталога(С))
		версии = {К["a"]: 3, К["b"]: 1, К["whole"]: 1}
		схемы = _опубликованные(прежний)

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
					("classes", lambda к: комп(к, "a")["classes"].update({К["scheme"]: К["y"]})),
					("classes", lambda к: комп(к, "a")["classes"].pop(К["nos"])),
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
				"редакторская: класс в необязательной схеме появился",
				lambda к: комп(к, "b")["classes"].update({К["nos"]: К["self"]}),
				_правки(editorial=[{"key": К["b"], "fields": ["classes"]}]),
			),
			(
				"классы вместе с признаком уровня — по существу",
				lambda к: (
					комп(к, "b")["classes"].update({К["scheme"]: К["x"]})
					or комп(к, "b")["levels"].update({"2": "Новый"})
				),
				_правки(substantive=[{"key": К["b"], "version": 2}]),
			),
			(
				"новая рядом с правкой по существу",
				lambda к: (
					к["competencies"].append(компетенция("fresh-one", К["classes"]))
					or комп(к, "b")["levels"].update({"1": "Новый"})
				),
				_правки(added=["fresh-one"], substantive=[{"key": К["b"], "version": 2}]),
			),
			(
				"схемы: новая и с изменёнными полями",
				lambda к: (
					к["classifications"].append(
						{
							"key": "fresh-scheme",
							"title": "Новая",
							"source": ["https://example.com/fresh"],
							"required": False,
							"classes": [{"key": "fresh-class", "title": "Класс"}],
						}
					)
					or к["classifications"][0].update(
						title="Другое название", source=["https://example.com/other"], required=False
					)
				),
				_правки(
					схемы=["fresh-scheme"],
					изменённые=[{"key": К["scheme"], "fields": ["title", "source", "required"]}],
					классы={"fresh-scheme": ["fresh-class"]},
				),
			),
			(
				"классы: новый и переименованный",
				lambda к: (
					к["classifications"][1]["classes"].append({"key": "fresh-class", "title": "Новый"})
					or к["classifications"][1]["classes"][1].update(title="Другое название")
				),
				_правки(классы={К["nos"]: ["fresh-class"]}, переименованные={К["nos"]: [К["self"]]}),
			),
		):
			with self.subTest(случай):
				новый = copy.deepcopy(прежний)
				правка(новый)
				self.assertEqual(checks.правки(прежний, checks.канон(новый), версии, схемы), ожидаемое)
