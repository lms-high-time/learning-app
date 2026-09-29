# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Шаблоны документов и привязка к ним курса (learning-services#370).

Правила правок проверяет `agent_learning/artifacts/test_overlay.py` без базы;
здесь — что они дошли до методов автора: версии шаблона, собранная схема у
ученика та же, что у схемы целиком, закреплённая версия, отвязка схемой
целиком и патч, переводящий готовые документы на шаблоны. Дальше —
наследник (#375), переход на новую версию с переименованиями (#376), его
предпросмотр и правки курса в черновике (#383), проверка каталога (#377).
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.artifacts import catalog
from lms_frappe_app.api import authoring, student
from lms_frappe_app.commands import commands
from lms_frappe_app.patches.v0_1 import artifact_templates
from lms_frappe_app.tests.sample_data import зачислить, создать_куратора, создать_урок, создать_ученика

БЛОКИ = [
	{"key": "intro", "title": "Вступление", "hint": "Одной фразой"},
	{
		"key": "items",
		"title": "Записи",
		"hint": "Что случилось",
		"spec": {
			"table": "items",
			"prefix": "I",
			"fields": [{"key": "limit", "title": "Предел", "type": "number"}],
			"columns": [
				{"key": "event", "title": "Событие", "type": "text", "required": True},
				{"key": "source", "title": "Источник", "type": "select", "options": ["a", "b"]},
				{"key": "kind", "title": "Вид", "type": "text"},
			],
		},
	},
	{"key": "outro", "title": "Итог", "kind": "file", "accept": "xlsx,csv"},
]
ХОЛСТ = {"grid": ["intro items", "outro outro"], "labels": {"intro": "Начало"}}


#: Наследник без итога: блок убран, сетка холста — без него.
ПРАВКИ_НАСЛЕДНИКА = {
	"blocks": {
		"items": {"hint": "Что случилось на объекте", "spec": {"columns": {"kind": None}}},
		"outro": None,
	},
	"canvas": {"grid": ["intro items"]},
}


class IntegrationTestArtifactTemplates(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"tpl-{self.суффикс}@example.com")
		self.ученик = создать_ученика(f"tpl-pupil-{self.суффикс}@example.com")
		self.урок = создать_урок(f"Урок {self.суффикс}")
		self.курс = зачислить(self.ученик, self.урок)
		self.ключ = f"journal-{self.суффикс}"
		frappe.set_user(self.куратор)

	def шаблон(self, **поля) -> dict:
		ответ = authoring.set_artifact_template(
			**{"template": self.ключ, "title": "Журнал", "blocks": БЛОКИ, "canvas": ХОЛСТ, **поля}
		)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def код(self, ответ) -> str:
		self.assertFalse(ответ["ok"], ответ)
		return ответ["error"]["code"]

	# --- шаблон ---

	def test_каждый_вызов_новая_версия(self):
		первая = self.шаблон(note="Первая")
		вторая = self.шаблон(blocks=БЛОКИ[:2], canvas=None, note="Без итога")

		self.assertEqual((первая["version"], вторая["version"]), (1, 2))
		self.assertEqual(первая["template"], self.ключ)
		последняя = authoring.artifact_template(template=self.ключ)["data"]
		self.assertEqual(последняя["version"], 2)
		self.assertEqual(последняя["note"], "Без итога")
		self.assertEqual([б["key"] for б in последняя["blocks"]], ["intro", "items"])
		self.assertIsNone(последняя["canvas"])

		прежняя = authoring.artifact_template(template=self.ключ, version=1)["data"]
		self.assertEqual([б["key"] for б in прежняя["blocks"]], ["intro", "items", "outro"])
		self.assertEqual(прежняя["blocks"][2]["accept"], ["xlsx", "csv"])
		self.assertNotIn("lesson", прежняя["blocks"][0])
		self.assertEqual(прежняя["canvas"]["labels"], {"intro": "Начало"})

	def test_версия_после_записи_не_меняется(self):
		документ = frappe.get_doc("Agent Artifact Template", self.шаблон()["id"])
		документ.title = "Другое"

		with self.assertRaises(frappe.ValidationError):
			документ.save()

	def test_отказы_шаблона(self):
		self.assertEqual(
			self.код(authoring.set_artifact_template(template="Плохой ключ", title="Т", blocks=БЛОКИ)),
			"artifact_invalid_template",
		)
		с_уроком = authoring.set_artifact_template(
			template=self.ключ, title="Т", blocks=[{"key": "intro", "title": "В", "lesson": self.урок}]
		)
		self.assertEqual(self.код(с_уроком), "artifact_invalid_spec")
		self.assertEqual(с_уроком["error"]["key"], "intro")
		self.assertEqual(
			self.код(
				authoring.set_artifact_template(
					template=self.ключ, title="Т", blocks=[{"key": "a"}, {"key": "A"}]
				)
			),
			"artifact_invalid_spec",
		)
		self.assertEqual(
			self.код(authoring.set_artifact_template(template=self.ключ, title="Т", blocks=[])),
			"artifact_invalid_spec",
		)
		self.assertEqual(
			self.код(authoring.artifact_template(template=f"nope-{self.суффикс}")),
			"artifact_template_not_found",
		)
		self.шаблон()
		self.assertEqual(
			self.код(authoring.artifact_template(template=self.ключ, version=9)),
			"artifact_template_not_found",
		)
		self.assertFalse(frappe.db.exists("Agent Artifact Template", {"template": "плохой ключ"}))

	def test_ключ_блока_не_занят_видом_страницы(self):
		"""Вид страницы документа и блок делят сегмент адреса (#367): ключ вида
		отклоняется и у схемы целиком, и у шаблона, и у собранной схемы."""
		целиком = authoring.set_course_artifact(
			course=self.курс, artifact="journal", title="Журнал", blocks=[{"key": "Report", "title": "О"}]
		)
		self.assertEqual((self.код(целиком), целиком["error"]["key"]), ("artifact_invalid_spec", "report"))
		шаблон = authoring.set_artifact_template(
			template=self.ключ, title="Т", blocks=[*БЛОКИ, {"key": "table", "title": "Т"}]
		)
		self.assertEqual((self.код(шаблон), шаблон["error"]["key"]), ("artifact_invalid_spec", "table"))
		self.шаблон()
		собранная = authoring.set_course_artifact_template(
			course=self.курс,
			artifact="journal",
			template=self.ключ,
			overlay={"add_blocks": [{"key": "compare", "title": "Сравнение"}]},
		)
		self.assertEqual(
			(self.код(собранная), собранная["error"]["key"]), ("artifact_invalid_spec", "compare")
		)
		self.assertFalse(frappe.db.exists("Agent Course Artifact", {"course": self.курс}))

	def test_шаблоны_только_автору(self):
		frappe.set_user(self.ученик)

		with self.assertRaises(frappe.PermissionError):
			authoring.list_artifact_templates()

	# --- наследник ---

	def наследник(self, **поля) -> dict:
		ответ = authoring.set_artifact_template(
			**{
				"template": f"{self.ключ}-site",
				"title": "Журнал объекта",
				"extends": self.ключ,
				"overlay": ПРАВКИ_НАСЛЕДНИКА,
				**поля,
			}
		)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def test_наследник_хранит_собранную_схему(self):
		self.шаблон()

		записан = self.наследник(overlay=json.dumps(ПРАВКИ_НАСЛЕДНИКА))

		self.assertEqual((записан["template"], записан["version"]), (f"{self.ключ}-site", 1))
		наследник = authoring.artifact_template(template=f"{self.ключ}-site")["data"]
		self.assertEqual(наследник["title"], "Журнал объекта")
		self.assertEqual(наследник["extends"], {"template": self.ключ, "version": 1})
		self.assertEqual(наследник["overlay"], ПРАВКИ_НАСЛЕДНИКА)
		self.assertEqual([б["key"] for б in наследник["blocks"]], ["intro", "items"])
		self.assertEqual(наследник["blocks"][1]["hint"], "Что случилось на объекте")
		self.assertEqual([к["key"] for к in наследник["blocks"][1]["spec"]["columns"]], ["event", "source"])
		self.assertEqual(наследник["canvas"]["grid"], ["intro items"])
		self.assertEqual(наследник["canvas"]["labels"], {"intro": "Начало"})
		родитель = authoring.artifact_template(template=self.ключ)["data"]
		self.assertEqual((родитель["extends"], родитель["overlay"]), (None, None))

		шаблоны = {т["template"]: т for т in authoring.list_artifact_templates()["data"]["templates"]}
		self.assertEqual(шаблоны[f"{self.ключ}-site"]["extends"], {"template": self.ключ, "version": 1})
		self.assertIsNone(шаблоны[self.ключ]["extends"])

	def test_раскладка_наследника_от_родителя_и_правок(self):
		"""`layout` наследника не читается: MCP шлёт его всегда, и наследник
		холста молча стал бы столбцом."""
		self.шаблон(layout="canvas")

		self.наследник(layout="sections")

		self.assertEqual(
			authoring.artifact_template(template=f"{self.ключ}-site")["data"]["layout"], "canvas"
		)
		self.наследник(layout="canvas", overlay={**ПРАВКИ_НАСЛЕДНИКА, "layout": "sections"})
		self.assertEqual(
			authoring.artifact_template(template=f"{self.ключ}-site")["data"]["layout"], "sections"
		)

	def test_наследник_закреплён_за_версией_родителя(self):
		self.шаблон()
		self.наследник()

		self.шаблон(blocks=[*БЛОКИ, {"key": "extra", "title": "Ещё"}], note="Новый блок")

		первая = authoring.artifact_template(template=f"{self.ключ}-site")["data"]
		self.assertEqual(первая["extends"]["version"], 1)
		self.assertNotIn("extra", [б["key"] for б in первая["blocks"]])
		вторая = self.наследник()
		self.assertEqual(вторая["version"], 2)
		последняя = authoring.artifact_template(template=f"{self.ключ}-site")["data"]
		self.assertEqual(последняя["extends"]["version"], 2)
		self.assertEqual([б["key"] for б in последняя["blocks"]], ["intro", "items", "extra"])
		self.наследник(extends_version=1)
		self.assertEqual(
			authoring.artifact_template(template=f"{self.ключ}-site")["data"]["extends"]["version"], 1
		)

	def test_курс_привязывается_к_наследнику(self):
		self.шаблон()
		self.наследник()

		ответ = authoring.set_course_artifact_template(
			course=self.курс,
			artifact="journal",
			template=f"{self.ключ}-site",
			overlay={"blocks": {"items": {"lesson": self.урок}}},
		)

		self.assertEqual(ответ["data"]["template"], f"{self.ключ}-site")
		frappe.set_user(self.ученик)
		документ = student.artifact(self.курс, "journal")["data"]
		self.assertEqual([б["key"] for б in документ["blocks"]], ["intro", "items"])
		self.assertEqual(документ["blocks"][1]["lesson"], self.урок)

	def test_отказы_наследника(self):
		self.шаблон()
		self.наследник()
		внук = f"{self.ключ}-sub"

		def наследовать(**поля):
			return authoring.set_artifact_template(
				**{"template": внук, "title": "Т", "extends": self.ключ, **поля}
			)

		отказ = наследовать(extends=f"{self.ключ}-site")
		self.assertEqual(
			(self.код(отказ), отказ["error"]["extends"]), ("artifact_invalid_template", f"{self.ключ}-site")
		)
		self.assertEqual(self.код(наследовать(template=self.ключ)), "artifact_invalid_template")
		self.assertEqual(self.код(наследовать(blocks=БЛОКИ)), "artifact_invalid_template")
		self.assertEqual(self.код(наследовать(canvas=ХОЛСТ)), "artifact_invalid_template")
		self.assertEqual(
			self.код(
				authoring.set_artifact_template(template=внук, title="Т", blocks=БЛОКИ, extends_version=1)
			),
			"artifact_invalid_template",
		)
		урок = наследовать(overlay={"blocks": {"intro": {"lesson": self.урок}}})
		self.assertEqual((self.код(урок), урок["error"]["key"]), ("artifact_invalid_overlay", "intro"))
		self.assertEqual(
			self.код(наследовать(overlay={"add_blocks": [{"key": "log", "lesson": self.урок}]})),
			"artifact_invalid_overlay",
		)
		self.assertEqual(self.код(наследовать(overlay={"title": "Иначе"})), "artifact_invalid_overlay")
		self.assertEqual(
			self.код(наследовать(overlay={"blocks": {"nope": None}})), "artifact_invalid_overlay"
		)
		self.assertEqual(self.код(наследовать(extends=f"nope-{self.суффикс}")), "artifact_template_not_found")
		self.assertEqual(self.код(наследовать(extends_version=7)), "artifact_template_not_found")
		self.assertFalse(frappe.db.exists("Agent Artifact Template", {"template": внук}))

	# --- привязка ---

	def правки(self) -> dict:
		return {
			"title": "Журнал площадки",
			"blocks": {
				"intro": {"lesson": self.урок},
				"items": {
					"hint": "Что случилось на площадке",
					"spec": {
						"columns": {"source": {"options": ["люди", "погода"]}, "kind": None},
						"add_columns": [{"key": "permit", "title": "Разрешение", "after": "source"}],
					},
				},
			},
			"add_blocks": [{"key": "log", "title": "Журнал", "after": "items"}],
			"canvas": {"labels": {"items": "Записи"}},
		}

	def test_ученик_видит_то_же_что_у_схемы_целиком(self):
		"""Собранная схема у ученика — та же, что записанная целиком: ни
		ученик, ни страница о шаблоне не знают."""
		self.шаблон()
		ответ = authoring.set_course_artifact_template(
			course=self.курс, artifact="journal", template=self.ключ, overlay=json.dumps(self.правки())
		)
		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(
			ответ["data"],
			{
				"id": ответ["data"]["id"],
				"course": self.курс,
				"artifact": "journal",
				"version": 1,
				"template": self.ключ,
				"template_version": 1,
			},
		)

		другой_урок = создать_урок(f"Эталон {self.суффикс}")
		эталон = зачислить(self.ученик, другой_урок)
		ожидаемые = [
			{**БЛОКИ[0], "lesson": другой_урок},
			{
				**БЛОКИ[1],
				"hint": "Что случилось на площадке",
				"spec": {
					**БЛОКИ[1]["spec"],
					"columns": [
						БЛОКИ[1]["spec"]["columns"][0],
						{
							"key": "source",
							"title": "Источник",
							"type": "select",
							"options": ["люди", "погода"],
						},
						{"key": "permit", "title": "Разрешение"},
					],
				},
			},
			{"key": "log", "title": "Журнал"},
			БЛОКИ[2],
		]
		authoring.set_course_artifact(
			course=эталон,
			artifact="journal",
			title="Журнал площадки",
			blocks=ожидаемые,
			canvas={**ХОЛСТ, "labels": {"intro": "Начало", "items": "Записи"}},
		)

		frappe.set_user(self.ученик)
		собранный = student.artifact(self.курс, "journal")["data"]
		целиком = student.artifact(эталон, "journal")["data"]
		self.assertEqual(собранный["blocks"][0]["lesson"], self.урок)
		for документ in (собранный, целиком):
			документ.pop("course")
			документ["blocks"][0]["lesson"] = None
		self.assertEqual(собранный, целиком)
		self.assertEqual([б["key"] for б in собранный["blocks"]], ["intro", "items", "log", "outro"])

	def test_новая_версия_шаблона_курс_не_меняет(self):
		self.шаблон()
		authoring.set_course_artifact_template(
			course=self.курс, artifact="journal", template=self.ключ, overlay=self.правки()
		)
		frappe.set_user(self.ученик)
		до = student.artifact(self.курс, "journal")["data"]
		frappe.set_user(self.куратор)

		self.шаблон(blocks=БЛОКИ[:2], canvas=None, note="Без итога")

		документ = authoring.course_draft(course=self.курс)["data"]["artifacts"][0]
		self.assertEqual(
			(документ["template"], документ["template_version"], документ["template_latest"]),
			(self.ключ, 1, 2),
		)
		frappe.set_user(self.ученик)
		self.assertEqual(student.artifact(self.курс, "journal")["data"], до)

	def test_без_версии_берётся_последняя(self):
		self.шаблон()
		self.шаблон(note="Вторая")

		ответ = authoring.set_course_artifact_template(
			course=self.курс, artifact="journal", template=self.ключ
		)

		self.assertEqual(ответ["data"]["template_version"], 2)
		ответ = authoring.set_course_artifact_template(
			course=self.курс, artifact="journal", template=self.ключ, version=1
		)
		self.assertEqual((ответ["data"]["template_version"], ответ["data"]["version"]), (1, 2))

	def test_схема_целиком_отвязывает_от_шаблона(self):
		self.шаблон()
		authoring.set_course_artifact_template(
			course=self.курс, artifact="journal", template=self.ключ, overlay=self.правки()
		)

		ответ = authoring.set_course_artifact(
			course=self.курс, artifact="journal", title="Журнал", blocks=БЛОКИ[:1]
		)["data"]

		документ = frappe.get_doc("Agent Course Artifact", ответ["id"])
		self.assertEqual((документ.template, документ.template_version, документ.overlay), (None, 0, None))
		черновик = authoring.course_draft(course=self.курс)["data"]["artifacts"][0]
		self.assertEqual(
			(черновик["template"], черновик["template_version"], черновик["template_latest"]),
			(None, None, None),
		)
		self.assertNotIn(
			self.курс,
			[
				к["course"]
				for т in authoring.list_artifact_templates()["data"]["templates"]
				for к in т["courses"]
			],
		)

	def test_перечень_показывает_курсы_на_шаблоне(self):
		self.шаблон(note="Первая")
		authoring.set_course_artifact_template(course=self.курс, artifact="journal", template=self.ключ)
		self.шаблон(note="Вторая")

		шаблоны = {т["template"]: т for т in authoring.list_artifact_templates()["data"]["templates"]}

		наш = шаблоны[self.ключ]
		self.assertEqual((наш["title"], наш["version"], наш["note"]), ("Журнал", 2, "Вторая"))
		self.assertEqual(
			наш["courses"],
			[
				{
					"course": self.курс,
					"course_title": frappe.db.get_value("LMS Course", self.курс, "title"),
					"artifact": "journal",
					"version": 1,
				}
			],
		)

	def test_отказы_привязки(self):
		self.шаблон()

		def привязать(**поля):
			return authoring.set_course_artifact_template(
				**{"course": self.курс, "artifact": "journal", "template": self.ключ, **поля}
			)

		self.assertEqual(self.код(привязать(course="нет-такого-курса")), "course_not_found")
		self.assertEqual(self.код(привязать(template=f"nope-{self.суффикс}")), "artifact_template_not_found")
		опечатка = привязать(overlay={"blocks": {"items": {"spec": {"columns": {"evnt": {"title": "…"}}}}}})
		self.assertEqual(self.код(опечатка), "artifact_invalid_overlay")
		self.assertEqual((опечатка["error"]["key"], опечатка["error"]["column"]), ("items", "evnt"))
		self.assertEqual(self.код(привязать(overlay="не json")), "artifact_invalid_overlay")
		# Урок соседнего курса у ученика этого курса не откроется.
		чужой = создать_урок(f"Чужой {self.суффикс}")
		self.assertEqual(
			self.код(привязать(overlay={"blocks": {"intro": {"lesson": чужой}}})), "lesson_not_found"
		)
		# Убранный блок назван в сетке холста — собранная схема не проходит проверку.
		self.assertEqual(self.код(привязать(overlay={"blocks": {"outro": None}})), "artifact_invalid_spec")
		self.assertFalse(frappe.db.exists("Agent Course Artifact", {"course": self.курс}))

	# --- переход на новую версию ---

	def вторая_версия(self, **поля) -> dict:
		"""Шаблон v2: блок, таблица, поле и колонка переименованы; колонка и
		блок добавлены."""
		блоки = [
			БЛОКИ[0],
			{
				"key": "entries",
				"title": "Записи",
				"hint": "Что случилось",
				"spec": {
					"table": "log",
					"prefix": "I",
					"fields": [{"key": "threshold", "title": "Предел", "type": "number"}],
					"columns": [
						{"key": "what", "title": "Событие", "type": "text", "required": True},
						{"key": "source", "title": "Источник", "type": "select", "options": ["a", "b"]},
						{"key": "kind", "title": "Вид", "type": "text"},
						{"key": "owner", "title": "Кто", "type": "text"},
					],
				},
			},
			БЛОКИ[2],
			{"key": "extra", "title": "Ещё"},
		]
		return self.шаблон(
			**{
				"blocks": блоки,
				"canvas": {"grid": ["intro entries", "outro outro"], "labels": {"intro": "Начало"}},
				"renamed": {
					"blocks": {"items": "entries"},
					"fields": {"limit": "threshold"},
					"tables": {"items": "log"},
					"columns": {"log": {"event": "what"}},
				},
				"note": "Записи стали журналом",
				**поля,
			}
		)

	def курс_с_данными(self):
		"""Курс на v1 с правками и ученик, заполнивший документ."""
		self.шаблон()
		authoring.set_course_artifact_template(
			course=self.курс,
			artifact="journal",
			template=self.ключ,
			overlay={
				"blocks": {
					"intro": {"lesson": self.урок},
					"items": {
						"spec": {
							"fields": {"limit": {"title": "Порог"}},
							"columns": {"source": {"options": ["люди", "погода"]}},
							"add_columns": [{"key": "permit", "title": "Разрешение", "after": "event"}],
						}
					},
				},
				"canvas": {"labels": {"items": "Записи"}},
			},
		)
		frappe.set_user(self.ученик)
		for ключ, поля in (
			("intro", {"content": "Начало проекта"}),
			(
				"items",
				{
					"content": "Заметка к записям",
					"rows": [{"event": "Пожар", "source": "люди", "permit": "есть"}],
					"fields": {"limit": 3},
				},
			),
		):
			ответ = student.update_artifact(self.курс, "journal", ключ, **поля)
			self.assertTrue(ответ["ok"], ответ)
		frappe.set_user(self.куратор)

	def test_переход_переносит_правки_и_данные_учеников(self):
		self.курс_с_данными()
		self.вторая_версия()
		# Третья переименовывает колонку ещё раз: переименования версий складываются.
		третья = authoring.artifact_template(template=self.ключ)["data"]
		третья["blocks"][1]["spec"]["columns"][2]["key"] = "sort"
		self.шаблон(
			blocks=третья["blocks"],
			canvas=третья["canvas"],
			renamed={"columns": {"log": {"kind": "sort"}}},
			note="Вид — сортировка",
		)
		предупреждения = authoring.course_draft(course=self.курс)["data"]["readiness"]["warnings"]
		устарел = [п for п in предупреждения if п["code"] == "artifact_template_outdated"]
		self.assertEqual(
			устарел,
			[
				{
					"code": "artifact_template_outdated",
					"artifact": "journal",
					"template": self.ключ,
					"message": f"Документ «Журнал»: шаблон {self.ключ} вышел в v3, курс на v1. Вид — сортировка",
				}
			],
		)

		ответ = authoring.upgrade_course_artifact(course=self.курс, artifact="journal")

		self.assertTrue(ответ["ok"], ответ)
		данные = ответ["data"]
		self.assertEqual(
			{к: в for к, в in данные.items() if к != "id"},
			{
				"course": self.курс,
				"artifact": "journal",
				"version": 2,
				"template": self.ключ,
				"template_version": 3,
				"from_version": 1,
				"diff": {
					"blocks": {"added": ["extra"], "removed": [], "changed": ["entries"]},
					"fields": {"added": [], "removed": []},
					"columns": {"added": ["log.owner"], "removed": []},
				},
				"students": 1,
			},
		)
		схема = frappe.get_doc("Agent Course Artifact", данные["id"])
		self.assertEqual(
			json.loads(схема.overlay),
			{
				"blocks": {
					"intro": {"lesson": self.урок},
					"entries": {
						"spec": {
							"fields": {"threshold": {"title": "Порог"}},
							"columns": {"source": {"options": ["люди", "погода"]}},
							"add_columns": [{"key": "permit", "title": "Разрешение", "after": "what"}],
						}
					},
				},
				"canvas": {"labels": {"entries": "Записи"}},
			},
		)
		frappe.set_user(self.ученик)
		документ = student.artifact(self.курс, "journal")["data"]
		self.assertEqual([б["key"] for б in документ["blocks"]], ["intro", "entries", "outro", "extra"])
		блоки = {б["key"]: б for б in документ["blocks"]}
		self.assertEqual(блоки["entries"]["content"], "Заметка к записям")
		self.assertEqual(блоки["intro"]["content"], "Начало проекта")
		self.assertEqual(документ["fields"], {"threshold": 3})
		строка = документ["tables"]["log"]["rows"][0]
		self.assertEqual(
			(строка["id"], строка["what"], строка["source"], строка["permit"]),
			("I1", "Пожар", "люди", "есть"),
		)
		self.assertEqual(документ["canvas"]["labels"], {"intro": "Начало", "entries": "Записи"})
		# Следующая строка — со следующим номером: счётчик переехал вместе с таблицей.
		ответ = student.update_artifact(self.курс, "journal", "entries", rows=[{"what": "Потоп"}])
		self.assertEqual(ответ["data"]["created"], ["I2"])
		frappe.set_user(self.куратор)
		предупреждения = authoring.course_draft(course=self.курс)["data"]["readiness"]["warnings"]
		self.assertNotIn("artifact_template_outdated", [п["code"] for п in предупреждения])

	def test_черновик_отдаёт_правки_курса(self):
		"""Перепривязка заменяет правки целиком: агент читает их в черновике."""
		self.курс_с_данными()
		authoring.set_course_artifact(course=self.курс, artifact="plain", title="П", blocks=БЛОКИ[:1])

		документы = {д["artifact"]: д for д in authoring.course_draft(course=self.курс)["data"]["artifacts"]}

		self.assertEqual(
			документы["journal"]["overlay"],
			json.loads(
				frappe.db.get_value(
					"Agent Course Artifact",
					{"course": self.курс, "slug": "journal", "is_active": 1},
					"overlay",
				)
			),
		)
		self.assertEqual(документы["journal"]["overlay"]["blocks"]["intro"], {"lesson": self.урок})
		self.assertIsNone(документы["plain"]["overlay"])
		authoring.set_course_artifact_template(course=self.курс, artifact="plain", template=self.ключ)
		документы = {д["artifact"]: д for д in authoring.course_draft(course=self.курс)["data"]["artifacts"]}
		self.assertEqual(документы["plain"]["overlay"], {})

	def снимок_записей(self) -> dict:
		"""Всё, что переход пишет: версии схемы курса, данные и тексты блоков учеников."""
		документы = frappe.get_all(
			"Agent Student Artifact", filters={"course": self.курс}, fields=["name", "data", "modified"]
		)
		return {
			"схемы": frappe.get_all(
				"Agent Course Artifact",
				filters={"course": self.курс},
				fields=["name", "version", "is_active", "overlay"],
				order_by="version asc",
			),
			"данные": {д.name: (д.data, д.modified) for д in документы},
			"блоки": sorted(
				frappe.get_all(
					"Agent Artifact Content",
					filters={"parent": ("in", [д.name for д in документы])},
					fields=["name", "block_key"],
					as_list=True,
				)
			),
		}

	def test_предпросмотр_перехода_ничего_не_пишет(self):
		self.курс_с_данными()
		self.вторая_версия()
		до = self.снимок_записей()

		предпросмотр = authoring.upgrade_course_artifact(course=self.курс, artifact="journal", dry_run=True)

		self.assertTrue(предпросмотр["ok"], предпросмотр)
		self.assertEqual(self.снимок_записей(), до)
		self.assertEqual(
			предпросмотр["data"],
			{
				"id": None,
				"course": self.курс,
				"artifact": "journal",
				"version": 1,
				"template": self.ключ,
				"template_version": 2,
				"from_version": 1,
				"diff": {
					"blocks": {"added": ["extra"], "removed": [], "changed": ["entries"]},
					"fields": {"added": [], "removed": []},
					"columns": {"added": ["log.owner"], "removed": []},
				},
				"students": 1,
				"dry_run": True,
			},
		)
		# Строкой, как параметр приходит по HTTP, — тоже предпросмотр.
		self.assertTrue(
			authoring.upgrade_course_artifact(course=self.курс, artifact="journal", dry_run="true")["data"][
				"dry_run"
			]
		)
		self.assertEqual(self.снимок_записей(), до)

		# Переход называет то же, что предпросмотр.
		переход = authoring.upgrade_course_artifact(course=self.курс, artifact="journal")["data"]
		self.assertNotIn("dry_run", переход)
		self.assertEqual(
			(переход["diff"], переход["students"], переход["version"]),
			(предпросмотр["data"]["diff"], 1, 2),
		)
		self.assertNotEqual(self.снимок_записей(), до)

	def test_предпросмотр_отказывает_как_переход(self):
		self.курс_с_данными()

		def предпросмотр(**поля):
			return authoring.upgrade_course_artifact(
				**{"course": self.курс, "artifact": "journal", "dry_run": True, **поля}
			)

		self.assertEqual(self.код(предпросмотр()), "artifact_template_same_version")
		self.assertEqual(self.код(предпросмотр(artifact="nope")), "artifact_not_found")
		# Колонку, которую правят правки курса, новая версия убрала.
		v2 = authoring.artifact_template(template=self.ключ)["data"]
		del v2["blocks"][1]["spec"]["columns"][1]
		self.шаблон(blocks=v2["blocks"], canvas=v2["canvas"])
		до = self.снимок_записей()

		ответ = предпросмотр()

		self.assertEqual(self.код(ответ), "artifact_invalid_overlay")
		self.assertEqual((ответ["error"]["key"], ответ["error"]["column"]), ("items", "source"))
		self.assertEqual(self.снимок_записей(), до)

	def test_переход_на_названную_версию(self):
		self.курс_с_данными()
		self.вторая_версия()
		self.шаблон(blocks=БЛОКИ[:2], canvas=None, note="Другая ветка")

		ответ = authoring.upgrade_course_artifact(course=self.курс, artifact="journal", version=2)

		self.assertEqual((ответ["data"]["template_version"], ответ["data"]["from_version"]), (2, 1))

	def test_правки_не_собрались_ничего_не_меняется(self):
		self.курс_с_данными()
		# Колонку, которую правят правки курса, новая версия убрала.
		v2 = authoring.artifact_template(template=self.ключ)["data"]
		del v2["blocks"][1]["spec"]["columns"][1]
		self.шаблон(blocks=v2["blocks"], canvas=v2["canvas"])
		frappe.set_user(self.ученик)
		до = student.artifact(self.курс, "journal")["data"]
		frappe.set_user(self.куратор)

		ответ = authoring.upgrade_course_artifact(course=self.курс, artifact="journal")

		self.assertEqual(self.код(ответ), "artifact_invalid_overlay")
		self.assertEqual((ответ["error"]["key"], ответ["error"]["column"]), ("items", "source"))
		self.assertEqual(frappe.db.count("Agent Course Artifact", {"course": self.курс}), 1)
		frappe.set_user(self.ученик)
		self.assertEqual(student.artifact(self.курс, "journal")["data"], до)

	def test_отказы_перехода(self):
		self.курс_с_данными()

		def перейти(**поля):
			return authoring.upgrade_course_artifact(**{"course": self.курс, "artifact": "journal", **поля})

		self.assertEqual(self.код(перейти(course="нет-такого-курса")), "course_not_found")
		self.assertEqual(self.код(перейти(artifact="nope")), "artifact_not_found")
		тот_же = перейти()
		self.assertEqual(
			(self.код(тот_же), тот_же["error"]["version"]), ("artifact_template_same_version", 1)
		)
		self.assertEqual(self.код(перейти(version=9)), "artifact_template_not_found")
		self.вторая_версия()
		authoring.set_course_artifact_template(
			course=self.курс, artifact="journal", template=self.ключ, version=2
		)
		self.assertEqual(self.код(перейти(version=1)), "artifact_invalid_template")
		authoring.set_course_artifact(course=self.курс, artifact="plain", title="П", blocks=БЛОКИ[:1])
		не_привязан = перейти(artifact="plain")
		self.assertEqual(
			(self.код(не_привязан), не_привязан["error"]["artifact"]), ("artifact_not_bound", "plain")
		)

	def test_переименования_сверяются_с_версиями(self):
		первая = authoring.set_artifact_template(
			template=self.ключ, title="Журнал", blocks=БЛОКИ, renamed={"blocks": {"intro": "start"}}
		)
		self.assertEqual(
			(self.код(первая), первая["error"]["path"]), ("artifact_invalid_template", "renamed")
		)
		self.шаблон()
		for renamed, путь in (
			({"blocks": {"nope": "intro"}}, "renamed.blocks.nope"),
			({"columns": {"items": {"event": "what"}}}, "renamed.columns.items.event"),
			({"tables": {"items": "log"}}, "renamed.tables.items"),
		):
			ответ = authoring.set_artifact_template(
				template=self.ключ, title="Журнал", blocks=БЛОКИ, canvas=ХОЛСТ, renamed=renamed
			)
			self.assertEqual((self.код(ответ), ответ["error"]["path"]), ("artifact_invalid_template", путь))
		self.assertEqual(frappe.db.count("Agent Artifact Template", {"template": self.ключ}), 1)
		self.вторая_версия()
		self.assertEqual(
			authoring.artifact_template(template=self.ключ)["data"]["renamed"]["columns"],
			{"log": {"event": "what"}},
		)
		self.assertIsNone(authoring.artifact_template(template=self.ключ, version=1)["data"]["renamed"])

	# --- патч ---

	def test_патч_переводит_документ_на_шаблон(self):
		frappe.set_user("Administrator")
		ключ = f"log_{self.суффикс}"
		блоки = [{**БЛОКИ[0], "lesson": self.урок}, *БЛОКИ[1:]]
		записано = authoring.set_course_artifact(
			course=self.курс, artifact=ключ, title="Журнал", blocks=блоки, canvas=ХОЛСТ
		)["data"]
		# Тот же ключ документа в другом курсе с другой схемой — свой шаблон.
		соседний = зачислить(self.ученик, создать_урок(f"Сосед {self.суффикс}"))
		authoring.set_course_artifact(course=соседний, artifact=ключ, title="Журнал", blocks=БЛОКИ[:1])
		frappe.set_user(self.ученик)
		до = student.artifact(self.курс, ключ)["data"]
		frappe.set_user("Administrator")

		artifact_templates.привязать_документы({"course": ("in", [self.курс, соседний])})

		документ = frappe.get_doc("Agent Course Artifact", записано["id"])
		шаблон = ключ.replace("_", "-")
		self.assertEqual((документ.template, документ.template_version, документ.version), (шаблон, 1, 1))
		self.assertEqual(json.loads(документ.overlay), {"blocks": {"intro": {"lesson": self.урок}}})
		self.assertEqual(frappe.db.count("Agent Course Artifact", {"course": self.курс, "slug": ключ}), 1)
		исходный = authoring.artifact_template(template=шаблон)["data"]
		self.assertEqual([б["key"] for б in исходный["blocks"]], ["intro", "items", "outro"])
		self.assertNotIn("lesson", исходный["blocks"][0])
		self.assertEqual(
			исходный["note"], "Из курса " + frappe.db.get_value("LMS Course", self.курс, "title")
		)
		сосед = frappe.db.get_value(
			"Agent Course Artifact", {"course": соседний, "slug": ключ, "is_active": 1}, "template"
		)
		self.assertNotEqual(сосед, шаблон)
		self.assertTrue(сосед.startswith(f"{шаблон}-"), сосед)
		frappe.set_user(self.ученик)
		self.assertEqual(student.artifact(self.курс, ключ)["data"], до)

		# Повторный запуск ничего не меняет.
		frappe.set_user("Administrator")
		шаблонов = frappe.db.count("Agent Artifact Template", {"template": ("like", f"{шаблон}%")})
		artifact_templates.привязать_документы({"course": ("in", [self.курс, соседний])})
		self.assertEqual(
			frappe.db.count("Agent Artifact Template", {"template": ("like", f"{шаблон}%")}), шаблонов
		)
		документ.reload()
		self.assertEqual((документ.template, документ.template_version), (шаблон, 1))

	def test_патч_берёт_готовый_шаблон_с_той_же_схемой(self):
		frappe.set_user("Administrator")
		ключ = f"log_{self.суффикс}"
		второй = зачислить(self.ученик, создать_урок(f"Второй {self.суффикс}"))
		for курс in (self.курс, второй):
			authoring.set_course_artifact(course=курс, artifact=ключ, title="Журнал", blocks=БЛОКИ[:2])

		artifact_templates.привязать_документы({"course": ("in", [self.курс, второй])})

		шаблоны = frappe.get_all(
			"Agent Course Artifact",
			filters={"course": ("in", [self.курс, второй]), "slug": ключ},
			pluck="template",
		)
		self.assertEqual(шаблоны, [ключ.replace("_", "-")] * 2)
		self.assertEqual(frappe.db.count("Agent Artifact Template", {"template": ключ.replace("_", "-")}), 1)


class IntegrationTestArtifactCatalog(IntegrationTestCase):
	"""Каталог из базы — через проверку и сборку движка (#377).

	Каталог общий для сайта, и на стенде в нём чужие документы: тест смотрит
	только на беды своих записей.
	"""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(создать_ученика(f"cat-{суффикс}@example.com"), урок)
		self.ключ = f"catalog-{суффикс}"
		self.наследник = f"{self.ключ}-site"
		self.другой = f"{self.ключ}-other"
		for ключ in (self.ключ, self.другой):
			authoring.set_artifact_template(template=ключ, title="Журнал", blocks=БЛОКИ, canvas=ХОЛСТ)
		authoring.set_artifact_template(
			template=self.наследник, title="Журнал объекта", extends=self.ключ, overlay=ПРАВКИ_НАСЛЕДНИКА
		)
		self.привязка = authoring.set_course_artifact_template(
			course=self.курс,
			artifact="journal",
			template=self.наследник,
			overlay={
				"blocks": {"intro": {"lesson": урок}},
				"add_blocks": [{"key": "log", "title": "Журнал"}],
			},
		)["data"]["id"]
		self.целиком = authoring.set_course_artifact(
			course=self.курс, artifact="plain", title="Схема целиком", blocks=БЛОКИ[:2]
		)["data"]["id"]

	def свои(self) -> list[dict]:
		return [
			беда
			for беда in catalog.проверить_каталог()
			if беда.get("template") in (self.ключ, self.наследник, self.другой)
			or беда.get("course") == self.курс
		]

	def test_каталог_в_порядке(self):
		self.assertEqual(self.свои(), [])

	def test_подделанная_запись_видна(self):
		шаблон = frappe.db.get_value("Agent Artifact Template", {"template": self.другой}, "name")
		# Ключ блока, который движок больше не принимает.
		frappe.db.set_value(
			"Agent Artifact Template", шаблон, "blocks", json.dumps([*БЛОКИ[:2], {"key": "report"}])
		)
		наследник = frappe.db.get_value("Agent Artifact Template", {"template": self.наследник}, "name")
		frappe.db.set_value("Agent Artifact Template", наследник, "overlay", json.dumps({"canvas": None}))
		frappe.db.set_value(
			"Agent Artifact Block",
			{"parent": self.привязка, "block_key": "items"},
			"hint",
			"Правка мимо движка",
		)
		frappe.db.set_value(
			"Agent Artifact Block",
			{"parent": self.целиком, "block_key": "items"},
			"spec",
			json.dumps({"columns": [{"key": "id"}]}),
		)

		беды = {(б["doctype"], б["name"]): б for б in self.свои()}

		self.assertEqual(
			{имя: беда["code"] for (_, имя), беда in беды.items()},
			{
				шаблон: "artifact_invalid_spec",
				наследник: "catalog_mismatch",
				self.привязка: "catalog_mismatch",
				self.целиком: "artifact_invalid_spec",
			},
		)
		self.assertEqual(беды[("Agent Artifact Template", шаблон)]["details"], {"key": "report"})
		self.assertEqual(беды[("Agent Course Artifact", self.привязка)]["details"], {"parts": ["blocks"]})
		self.assertEqual(
			беды[("Agent Artifact Template", наследник)]["details"], {"parts": ["blocks", "canvas"]}
		)
		строки: list[str] = []
		self.assertEqual(catalog.отчёт(строки.append), 1)
		self.assertTrue(any(self.привязка in с and "journal" in с for с in строки), строки)
		with self.assertRaises(frappe.ValidationError):
			catalog.после_миграции()

	def test_команда_bench_объявлена(self):
		self.assertIn("check-artifact-catalog", [команда.name for команда in commands])
