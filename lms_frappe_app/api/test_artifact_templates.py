# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Шаблоны документов и привязка к ним курса (learning-services#370).

Правила правок проверяет `agent_learning/artifacts/test_overlay.py` без базы;
здесь — что они дошли до методов автора: версии шаблона, собранная схема у
ученика та же, что у схемы целиком, закреплённая версия и отвязка схемой
целиком.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import authoring, student
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

	def test_шаблоны_только_автору(self):
		frappe.set_user(self.ученик)

		with self.assertRaises(frappe.PermissionError):
			authoring.list_artifact_templates()

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
