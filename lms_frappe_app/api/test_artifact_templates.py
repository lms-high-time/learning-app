# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Шаблоны документов и привязка к ним курса (learning-services#370).

Правила правок проверяет `agent_learning/artifacts/test_overlay.py` без базы;
здесь — что они дошли до методов автора: версии шаблона, собранная схема у
ученика та же, что у схемы целиком, закреплённая версия, отвязка схемой
целиком и патч, переводящий готовые документы на шаблоны.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import authoring, student
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
