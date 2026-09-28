# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Документ-таблица через методы ученика и автора (learning-services#330).

Логику таблиц проверяет `agent_learning/test_artifact_tables.py` без базы;
здесь — что она дошла до методов: запись строк и полей, чтение таблиц,
заполненность в перечне, очистка, выгрузка и проверка схемы у автора.
"""

import json
from io import BytesIO

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import authoring, student
from lms_frappe_app.tests.sample_data import зачислить, создать_урок, создать_ученика

БЛОКИ = [
	{"key": "worries", "title": "Первый список тревог"},
	{
		"key": "risks",
		"title": "Риски",
		"spec": {
			"table": "register",
			"prefix": "R",
			"title": "Реестр",
			"columns": [
				{"key": "event", "title": "Событие", "type": "text", "required": True},
			],
		},
	},
	{
		"key": "assessment",
		"title": "Оценка",
		"spec": {
			"table": "register",
			"fields": [{"key": "threshold", "title": "Порог внимания", "type": "number", "required": True}],
			"columns": [
				{"key": "probability", "title": "Вероятность", "type": "scale", "required": True},
				{"key": "impact", "title": "Влияние", "type": "scale", "required": True},
				{"key": "rank", "title": "Ранг", "type": "formula", "formula": "probability * impact"},
				{"key": "in_work", "title": "В работе", "type": "formula", "formula": "rank >= threshold"},
			],
		},
	},
	{
		"key": "responses",
		"title": "Ответы",
		"spec": {
			"table": "register",
			"columns": [{"key": "measure", "title": "Мера", "type": "text", "required": "in_work"}],
		},
	},
]


class IntegrationTestArtifactTables(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"table-{суффикс}@example.com")
		self.курс = зачислить(self.ученик, создать_урок(f"Урок {суффикс}"))
		ответ = authoring.set_course_artifact(
			course=self.курс, artifact="risk_register", title="Реестр рисков", blocks=БЛОКИ
		)
		self.assertTrue(ответ["ok"], ответ)
		frappe.set_user(self.ученик)

	def записать(self, ключ, **параметры):
		return student.update_artifact(self.курс, "risk_register", ключ, **параметры)

	def test_строки_оценка_и_ответ_по_урокам(self):
		ответ = self.записать("risks", rows=[{"event": "Подрядчик уйдёт"}, {"event": "Отпуск Анны"}])
		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(ответ["data"]["created"], ["R1", "R2"])
		self.assertEqual(ответ["data"]["blocks_filled"], 1)

		ответ = self.записать(
			"assessment",
			fields={"threshold": 12},
			rows=[{"id": "R1", "probability": 4, "impact": 4}, {"id": "R2", "probability": 2, "impact": 3}],
		)
		self.assertEqual(ответ["data"]["blocks_filled"], 2)
		self.assertEqual(ответ["data"]["empty_cells"], [])

		документ = student.artifact(self.курс, "risk_register")["data"]
		реестр = документ["tables"]["register"]
		self.assertEqual(
			[(р["id"], р["rank"], р["in_work"]) for р in реестр["rows"]], [("R1", 16, True), ("R2", 6, False)]
		)
		self.assertEqual(документ["fields"], {"threshold": 12})
		ответы = next(б for б in документ["blocks"] if б["key"] == "responses")
		self.assertFalse(ответы["filled"])
		self.assertEqual(ответы["empty_cells"], [{"row": "R1", "column": "measure"}])

		ответ = self.записать(
			"responses", rows=[{"id": "R1", "measure": "Договор с резервным монтажником до 1 марта"}]
		)
		self.assertEqual(ответ["data"]["blocks_filled"], 3)

	def test_отказ_не_меняет_документ(self):
		self.записать("risks", rows=[{"event": "Подрядчик уйдёт"}])
		ответ = self.записать("assessment", rows=[{"id": "R1", "probability": 9}])
		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], "artifact_invalid_value")
		ответ = self.записать("assessment", rows=[{"probability": 3}])
		self.assertEqual(ответ["error"]["code"], "artifact_rows_owner_only")
		реестр = student.artifact(self.курс, "risk_register")["data"]["tables"]["register"]
		self.assertNotIn("probability", реестр["rows"][0])

	def test_прежний_текст_засчитан_пока_таблица_пуста(self):
		self.записать("risks", content="| ID | Событие |\n| --- | --- |\n| R1 | Подрядчик уйдёт |")
		перечень = student.artifact(self.курс)["data"]["artifacts"]
		self.assertEqual(перечень[0]["blocks_filled"], 1)

	def test_очистка_блока_хозяина_уносит_строки(self):
		self.записать("risks", rows=[{"event": "Подрядчик уйдёт"}], content="Заметка")
		self.записать(
			"assessment", fields={"threshold": 10}, rows=[{"id": "R1", "probability": 3, "impact": 3}]
		)
		self.записать("assessment", clear=True)
		документ = student.artifact(self.курс, "risk_register")["data"]
		self.assertEqual(документ["fields"], {"threshold": None})
		self.assertNotIn("probability", документ["tables"]["register"]["rows"][0])

		self.записать("risks", clear=True)
		документ = student.artifact(self.курс, "risk_register")["data"]
		self.assertEqual(документ["tables"]["register"]["rows"], [])
		self.assertEqual(next(б for б in документ["blocks"] if б["key"] == "risks")["content"], "")

	def test_выгрузка_markdown_и_xlsx(self):
		from openpyxl import load_workbook

		from lms_frappe_app.www.artifacts import download

		self.записать("risks", rows=[{"event": "Подрядчик уйдёт"}])
		self.записать(
			"assessment", fields={"threshold": 12}, rows=[{"id": "R1", "probability": 4, "impact": 4}]
		)

		download(self.курс, "risk_register")
		текст = frappe.response["filecontent"]
		self.assertIn("- Порог внимания: 12", текст)
		self.assertIn("| R1 | Подрядчик уйдёт | 4 | 4 | 16 | да |  |", текст)

		download(self.курс, "risk_register", format="xlsx")
		self.assertEqual(frappe.response["filename"], "risk_register.xlsx")
		книга = load_workbook(BytesIO(frappe.response["filecontent"]))
		лист = книга["Реестр"]
		self.assertEqual(лист["E2"].value, "=C2*D2")

	def test_автор_не_сохранит_схему_которую_не_прочесть(self):
		frappe.set_user("Administrator")
		блоки = json.loads(json.dumps(БЛОКИ))
		блоки[2]["spec"]["columns"][2]["formula"] = "probability * weight"
		ответ = authoring.set_course_artifact(
			course=self.курс, artifact="risk_register", title="Реестр рисков", blocks=блоки
		)
		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], "artifact_invalid_spec")

	def test_время_и_версия_документа(self):
		"""Сколько раз документ сохранялся и когда — без самой истории (#342)."""
		пустой = student.artifact(self.курс, "risk_register")["data"]
		self.assertEqual((пустой["modified"], пустой["version"]), (None, 0))

		self.записать("risks", rows=[{"event": "Подрядчик уйдёт"}])
		self.записать("assessment", fields={"threshold": 12})
		документ = student.artifact(self.курс, "risk_register")["data"]

		self.assertTrue(документ["modified"])
		# Журнал `Version` Frappe пишет после коммита, а тест идёт в
		# транзакции без него: здесь проверяется форма, число — на стенде.
		self.assertIsInstance(документ["version"], int)
		self.assertNotIn("versions", документ)

	def test_запись_шлёт_событие_только_ученику(self):
		"""Страница перечитывает документ, когда в него пишет агент (#348)."""
		from unittest.mock import patch

		with patch("frappe.publish_realtime") as publish:
			self.записать("risks", rows=[{"event": "Подрядчик уйдёт"}])
		события = [c for c in publish.call_args_list if c.kwargs.get("event") == "artifact_updated"]
		self.assertEqual(len(события), 1)
		self.assertEqual(события[0].kwargs["user"], self.ученик)
		self.assertEqual(события[0].kwargs["message"]["course"], self.курс)
		self.assertEqual(события[0].kwargs["message"]["artifact"], "risk_register")
		self.assertTrue(события[0].kwargs["after_commit"])
