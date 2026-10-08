# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Заметки автора по ключам релиза (learning-services#512).

`Why:` курс правится только новым релизом, и место заметки — ключ релиза:
урок, цель, пункт, вопрос, раздел документа, пакет агента, узел карты.
Заметка помнит релиз, к которому написана, а «места больше нет» значит, что
ключа нет в действующем релизе.
"""

import io
from contextlib import redirect_stdout
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning.releases import index, places
from lms_frappe_app.api import authoring
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import создать_куратора, создать_курс, создать_ученика, урок_релиза

#: Карта релиза в тестах — заглушка: узлы на разной глубине, в том числе в списке.
КАРТА = {
	"goals": {"G1": {"text": "Цель курса"}},
	"decisions": {"l-2-D1": {"text": "Решение", "variants": {"V1": {"text": "Первый вариант"}}}},
	"lessons": {"L2": {"title": "Урок карты"}},
	"document": {"sections": [{"key": "log", "fields": {"F1": {"text": "Поле в списке"}}}]},
}

#: Место → (подпись, ключ урока места) в релизе `пример_релиза` с картой `КАРТА`.
МЕСТА = {
	"course": ("Курс", None),
	"chapter.ch-2": ("Глава 2 «Глава вторая»", None),
	"lesson.l-2": ("Урок 2 «Урок второй»", "l-2"),
	"objective.l-2-D1": ("Урок 2 «Урок второй» · цель «Цель урока «Урок второй»»", "l-2"),
	"goal.l-2/l-2-D1/V1": ("Урок 2 «Урок второй» · пункт «Выбор: «первый»»", "l-2"),
	"question.S1/l-2-D1": ("Урок 2 «Урок второй» · вопрос «Ситуация и вопрос»", "l-2"),
	"section.rules": ("Документ · раздел «Правила»", None),
	"agent.frame": ("Пакет агента · рамка курса", None),
	"agent.lesson.l-2": ("Урок 2 «Урок второй» · пакет агента", "l-2"),
	"agent.item.l-2/l-2-D1/V1": ("Урок 2 «Урок второй» · пакет агента · пункт «Выбор: «первый»»", "l-2"),
	"map.V1": ("Карта · «Первый вариант»", None),
}

ПОЛЯ_ЗАМЕТКИ = {
	"id",
	"target",
	"release",
	"version",
	"lesson_key",
	"label",
	"missing",
	"quote",
	"text",
	"via",
	"author",
	"author_name",
	"status",
	"waiting_on",
	"created_at",
	"updated_at",
	"replies",
}


class IntegrationTestAuthorNotes(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"notes-{суффикс}@example.com")
		self.ключ = f"notes-{суффикс}"
		frappe.set_user(self.куратор)
		первый = self.опубликовать(self.релиз())
		self.курс, self.первый = первый["course"], первый["release"]

	def релиз(self) -> dict:
		релиз = пример_релиза(self.ключ)
		релиз["map"] = КАРТА
		return релиз

	def опубликовать(self, релиз: dict) -> dict:
		ответ = authoring.publish_release(release=релиз)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]

	def заметка(self, target: str = "lesson.l-1", **правки) -> dict:
		return authoring.add_note(**{"course": self.курс, "target": target, "text": "Слишком длинно", **правки})

	def записать(self, target: str = "lesson.l-1", **правки) -> str:
		ответ = self.заметка(target, **правки)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]["id"]

	def очередь(self, **фильтры) -> list[dict]:
		ответ = authoring.list_notes(course=self.курс, **фильтры)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]["notes"]

	def код(self, ответ: dict) -> str | None:
		return None if ответ["ok"] else ответ["error"]["code"]

	# --- места ---

	def test_каждое_место_пишется_и_подписывается_по_релизу(self):
		for target, (подпись, урок) in МЕСТА.items():
			with self.subTest(target=target):
				ответ = self.заметка(target)
				self.assertTrue(ответ["ok"], ответ)
				self.assertEqual(
					ответ["data"],
					{
						"id": ответ["data"]["id"],
						"course": self.курс,
						"target": target,
						"release": self.первый,
						"version": 1,
						"lesson_key": урок,
						"label": подпись,
						"missing": False,
						"status": "open",
						"waiting_on": "agent",
					},
				)

		заметки = self.очередь()

		self.assertEqual(set(заметки[0]), ПОЛЯ_ЗАМЕТКИ)
		self.assertEqual(
			{з["target"]: (з["label"], з["lesson_key"], з["missing"], з["release"], з["version"]) for з in заметки},
			{
				target: (подпись, урок, False, self.первый, 1)
				for target, (подпись, урок) in МЕСТА.items()
			},
		)

	def test_место_исчезло_после_нового_релиза(self):
		"""Ключа нет в действующем релизе — место исчезло; заметка помнит свой
		релиз, а урок места находится по истории релизов курса."""
		исчезнут = {
			"chapter.ch-2": ("Глава ch-2", None),
			"lesson.l-3": ("Урок l-3", "l-3"),
			"objective.l-3-D1": ("Цель l-3-D1", "l-3"),
			"goal.l-1/refute:M1": ("Пункт l-1/refute:M1", "l-1"),
			"question.S1/l-1-D1": ("Вопрос S1/l-1-D1", "l-1"),
			"section.rules": ("Раздел документа rules", None),
			"agent.frame": ("Пакет агента · рамка курса", None),
			"agent.lesson.l-3": ("Пакет агента · урок l-3", "l-3"),
			"agent.item.l-1/refute:M1": ("Пакет агента · пункт l-1/refute:M1", "l-1"),
			"map.V1": ("Карта · узел V1", None),
		}
		for target in исчезнут:
			self.записать(target)
		остаётся = self.записать("lesson.l-2")

		второй = self.опубликовать(self.урезанный())["release"]

		заметки = {з["target"]: з for з in self.очередь()}
		for target, (подпись, урок) in исчезнут.items():
			with self.subTest(target=target):
				з = заметки[target]
				self.assertEqual(
					(з["missing"], з["label"], з["lesson_key"], з["release"], з["version"]),
					(True, подпись, урок, self.первый, 1),
				)
		self.assertFalse(заметки["lesson.l-2"]["missing"])
		self.assertEqual(заметки["lesson.l-2"]["id"], остаётся)
		# В новый релиз на исчезнувшее место не пишется; на оставшееся — с новой версией.
		for target in исчезнут:
			with self.subTest(новая=target):
				ответ = self.заметка(target)
				self.assertEqual(self.код(ответ), "note_target_unknown")
				self.assertEqual(ответ["error"]["target"], target)
		новая = self.заметка("lesson.l-2")["data"]
		self.assertEqual((новая["release"], новая["version"]), (второй, 2))

	def урезанный(self) -> dict:
		"""Релиз без второй главы и урока `l-3`, без раздела `rules`, рамки пакета,
		пункта `refute:M1` урока `l-1`, узла карты `V1`; вопрос `l-1` — с новым ключом."""
		релиз = self.релиз()
		релиз["chapters"] = релиз["chapters"][:1]
		релиз["lessons"] = релиз["lessons"][:2]
		релиз["lessons"][1]["sections"] = ["log"]
		релиз["document"]["sections"] = релиз["document"]["sections"][:1]
		del релиз["agent"]["frame"], релиз["agent"]["learn_about_student"]
		del релиз["agent"]["lessons"]["l-3"]
		первый = релиз["lessons"][0]
		первый["objectives"][0]["goals"] = первый["objectives"][0]["goals"][:2]
		del релиз["agent"]["lessons"]["l-1"]["items"]["refute:M1"]
		квиз = первый["quiz"]
		квиз["questions"][0]["key"] = "S2/l-1-D1"
		квиз["answers"] = {"S2/l-1-D1": квиз["answers"]["S1/l-1-D1"]}
		релиз["map"] = {"goals": КАРТА["goals"]}
		return релиз

	def test_неизвестное_место_отклоняется(self):
		for target in (
			"chapter.ch-9",
			"lesson.l-9",
			"objective.l-9-D1",
			"goal.l-1/нет",
			"goal.l-9/term:T1",
			"question.S9/l-1-D1",
			"section.нет",
			"agent.lesson.l-9",
			"agent.item.l-1/нет",
			"map.нет",
		):
			with self.subTest(target=target):
				ответ = self.заметка(target)
				self.assertEqual(self.код(ответ), "note_target_unknown")
				self.assertEqual(
					(ответ["error"]["target"], ответ["error"]["release"]), (target, self.первый)
				)
		self.assertEqual(self.очередь(), [])

	def test_неверный_адрес_и_пустой_текст(self):
		for правки, код, где in (
			({"target": "directive.teaching_directive"}, "invalid_target", "target"),
			({"target": "lesson"}, "invalid_target", "target"),
			({"text": "  "}, "invalid_note", "text"),
			({"via": "robot"}, "invalid_note", "via"),
		):
			with self.subTest(**правки):
				ответ = self.заметка(**правки)
				self.assertEqual((self.код(ответ), ответ["error"]["where"]), (код, где))
		self.assertEqual(self.очередь(), [])

	def test_курс_без_релиза(self):
		курс = authoring.create_course(title=f"Анонс {frappe.generate_hash(length=6)}", summary="к")["data"]["id"]

		ответ = authoring.add_note(course=курс, target="course", text="Пример")

		self.assertEqual((self.код(ответ), ответ["error"]["course"]), ("course_not_released", курс))
		self.assertEqual(authoring.list_notes(course=курс)["data"]["notes"], [])

	def test_несуществующий_курс(self):
		self.assertEqual(self.код(authoring.add_note(course="нет-курса", target="course", text="…")), "course_not_found")
		self.assertEqual(self.код(authoring.list_notes(course="нет-курса")), "course_not_found")

	# --- фильтры ---

	def test_фильтр_по_ключу_урока_и_статусу(self):
		урок = self.записать("lesson.l-1")
		пункт = self.записать("agent.item.l-1/term:T1")
		вопрос = self.записать("question.S1/l-1-D1")
		self.записать("lesson.l-2")
		курс = self.записать("course")
		authoring.set_note_status(note=урок, status="accepted")

		self.assertEqual([з["id"] for з in self.очередь(lesson="l-1")], [урок, пункт, вопрос])
		self.assertEqual([з["id"] for з in self.очередь(lesson="l-1", status="open")], [пункт, вопрос])
		self.assertEqual(self.очередь(lesson="l-9"), [])
		self.assertIn(курс, [з["id"] for з in self.очередь(status="open")])
		ответ = authoring.list_notes(course=self.курс, status="closed")
		self.assertEqual((self.код(ответ), ответ["error"]["where"]), ("invalid_note", "status"))

	def test_фильтр_по_уроку_снятому_из_релиза(self):
		ид = self.записать("objective.l-3-D1")
		self.опубликовать(self.урезанный())

		(з,) = self.очередь(lesson="l-3")

		self.assertEqual((з["id"], з["missing"], з["lesson_key"]), (ид, True, "l-3"))

	# --- цикл ---

	def test_цикл_сделано_принято_и_возврат(self):
		ид = self.записать()

		сделано = authoring.set_note_status(note=ид, status="done", text="Сократил зачин", via="agent")["data"]
		self.assertEqual(сделано, {"id": ид, "status": "done", "waiting_on": "author", "replies": 1})

		возврат = authoring.set_note_status(note=ид, status="open", text="Всё ещё длинно")["data"]
		self.assertEqual((возврат["status"], возврат["waiting_on"]), ("open", "agent"))

		authoring.set_note_status(note=ид, status="done", text="Убрал второй абзац", via="agent")
		принято = authoring.set_note_status(note=ид, status="accepted")["data"]
		self.assertEqual((принято["status"], принято["waiting_on"]), ("accepted", None))

		(з,) = self.очередь()
		self.assertEqual(
			[(о["via"], о["text"]) for о in з["replies"]],
			[("agent", "Сократил зачин"), ("author", "Всё ещё длинно"), ("agent", "Убрал второй абзац")],
		)

	def test_агент_не_принимает_автор_не_отмечает_сделанным(self):
		ид = self.записать()

		for параметры in (
			{"status": "accepted", "via": "agent"},
			{"status": "done", "text": "Сам поправил"},
			{"status": "done", "via": "agent"},
		):
			with self.subTest(**параметры):
				self.assertEqual(
					self.код(authoring.set_note_status(note=ид, **параметры)), "invalid_transition"
				)

	def test_вопрос_агента_ждёт_автора_а_ответ_возвращает_ход(self):
		ид = self.записать(via="agent", text="Какой пример взять?")
		self.assertEqual(self.очередь()[0]["waiting_on"], "author")

		ответ = authoring.reply_note(note=ид, text="Возьми склад")["data"]

		self.assertEqual(ответ, {"id": ид, "status": "open", "waiting_on": "agent", "replies": 1})

	def test_заметки_нет(self):
		self.assertEqual(self.код(authoring.reply_note(note="AAN-нет", text="…")), "note_not_found")
		self.assertEqual(self.код(authoring.set_note_status(note="AAN-нет", status="accepted")), "note_not_found")

	def test_заметки_двигают_свою_метку_а_не_метку_курса(self):
		до = authoring.course_revision(course=self.курс)["data"]
		self.assertIsNone(до["notes_revision"])

		ид = self.записать()
		после_записи = authoring.course_revision(course=self.курс)["data"]
		authoring.reply_note(note=ид, text="Уточню: шаг 3")
		после_ответа = authoring.course_revision(course=self.курс)["data"]

		self.assertEqual(после_записи["revision"], до["revision"])
		self.assertIsNotNone(после_записи["notes_revision"])
		self.assertGreater(после_ответа["notes_revision"], после_записи["notes_revision"])

	def test_ученику_заметки_недоступны(self):
		ид = self.записать()
		frappe.set_user(создать_ученика(f"notes-s-{frappe.generate_hash(length=6)}@example.com"))

		for вызов in (
			lambda: authoring.list_notes(course=self.курс),
			lambda: authoring.add_note(course=self.курс, target="course", text="Я ученик"),
			lambda: authoring.reply_note(note=ид, text="Я ученик"),
			lambda: authoring.set_note_status(note=ид, status="accepted"),
		):
			with self.assertRaises(frappe.PermissionError):
				вызов()

	# --- карта ---

	def test_узлы_карты_читаются_из_снимка_один_раз_на_релиз(self):
		frappe.cache.delete_value(f"{places.КЭШ_УЗЛОВ}:{self.первый}")
		with patch.object(index, "снимок", wraps=index.снимок) as снимок:
			self.записать("map.G1")
			self.записать("map.F1")
			self.очередь()
			self.очередь()

		снимок.assert_called_once_with(self.первый)
		self.assertEqual(
			places.узлы_карты(self.первый),
			{
				"G1": "Цель курса",
				"l-2-D1": "Решение",
				"V1": "Первый вариант",
				"L2": "Урок карты",
				"F1": "Поле в списке",
			},
		)


class IntegrationTestПереносЗаметок(IntegrationTestCase):
	"""Патч `note_release_keys`: старые места заметок — на ключи релиза (learning-services#512)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		ответ = authoring.publish_release(release=пример_релиза(f"mv-notes-{суффикс}"))["data"]
		self.курс, self.релиз = ответ["course"], ответ["release"]
		self.первый, self.второй = урок_релиза(self.курс, "l-1"), урок_релиза(self.курс, "l-2")
		self.без_релиза = создать_курс(f"Без релиза {суффикс}")
		self.удалённый = f"удалённый-курс-{суффикс}"

	def старая(self, курс: str, target: str, lesson: str | None = None, status: str = "open", ответов: int = 0) -> str:
		"""Заметка со старым местом — как её оставила прежняя версия приложения."""
		документ = frappe.get_doc(
			{
				"doctype": "Agent Author Note",
				"course": курс,
				"lesson": lesson,
				"target": target,
				"status": status,
				"via": "author",
				"text": "Старая заметка",
				"replies": [
					{"via": "agent", "author": "Administrator", "text": f"Ответ {номер}"} for номер in range(ответов)
				],
			}
		)
		документ.insert(ignore_links=True, ignore_permissions=True)
		return документ.name

	def запись(self, имя: str) -> dict | None:
		запись = frappe.db.get_value(
			"Agent Author Note", имя, ["target", "lesson", "release", "status"], as_dict=True
		)
		if запись:
			запись["replies"] = frappe.get_all(
				"Agent Note Reply", filters={"parent": имя}, fields=["via", "text"], order_by="idx asc"
			)
		return запись

	def выполнить(self) -> str:
		from lms_frappe_app.patches.v0_1 import note_release_keys

		вывод = io.StringIO()
		with redirect_stdout(вывод):
			note_release_keys.execute()
		return вывод.getvalue()

	def test_старые_места_переносятся_архивируются_и_удаляются(self):
		from lms_frappe_app.patches.v0_1.note_release_keys import ОТВЕТ_АРХИВА

		перенос = {
			self.старая(self.курс, "course"): ("course", None),
			self.старая(self.курс, "lesson", self.второй, ответов=1): ("lesson.l-2", self.второй),
			self.старая(self.курс, "block.notebook/log", self.первый): ("section.log", None),
		}
		архив = [
			self.старая(self.курс, "block.register/risks"),
			self.старая(self.курс, "material", self.первый),
			self.старая(self.курс, "directive.teaching_directive", self.первый, ответов=2),
			self.старая(self.курс, "course_directive.glossary"),
			self.старая(self.курс, "question.QTS-00001", self.первый),
			self.старая(self.курс, "map.T1"),
			self.старая(self.курс, "material", self.первый, status="done"),
			self.старая(self.без_релиза, "lesson", создать_урок_без_релиза(self.без_релиза)),
			self.старая(self.без_релиза, "course"),
		]
		принятая = self.старая(self.курс, "material", self.первый, status="accepted", ответов=1)
		удалить = [self.старая(self.удалённый, "course", ответов=1), self.старая(self.удалённый, "lesson")]

		вывод = self.выполнить()
		повтор = self.выполнить()

		for имя, (место, урок) in перенос.items():
			with self.subTest(место=место):
				запись = self.запись(имя)
				self.assertEqual(
					(запись["target"], запись["lesson"], запись["release"]), (место, урок, self.релиз)
				)
				self.assertNotIn(ОТВЕТ_АРХИВА, [о.text for о in запись["replies"]])
		for имя in архив:
			запись = self.запись(имя)
			with self.subTest(место=запись["target"]):
				self.assertEqual((запись["status"], запись["release"]), ("accepted", None))
				self.assertEqual([(о.via, о.text) for о in запись["replies"]][-1], ("agent", ОТВЕТ_АРХИВА))
				self.assertEqual([о.text for о in запись["replies"]].count(ОТВЕТ_АРХИВА), 1)
		self.assertEqual(self.запись(принятая)["replies"], [{"via": "agent", "text": "Ответ 0"}])
		for имя in удалить:
			self.assertIsNone(self.запись(имя))
			self.assertFalse(frappe.db.exists("Agent Note Reply", {"parent": имя}))
		self.assertIn(f"note_release_keys: {self.курс} — перенесено 3, в архиве 7, удалено 0", вывод)
		self.assertIn(f"note_release_keys: {self.без_релиза} — перенесено 0, в архиве 2, удалено 0", вывод)
		self.assertIn(f"note_release_keys: {self.удалённый} — перенесено 0, в архиве 0, удалено 2", вывод)
		self.assertIn(f"note_release_keys: {self.курс} — перенесено 0, в архиве 0, удалено 0", повтор)
		# Перенесённая заметка читается по ключам: место есть в релизе.
		заметки = {з["id"]: з for з in authoring.list_notes(course=self.курс)["data"]["notes"]}
		урок = заметки[next(имя for имя, (место, _) in перенос.items() if место == "lesson.l-2")]
		self.assertEqual(
			(урок["label"], урок["missing"], урок["lesson_key"], урок["version"]),
			("Урок 2 «Урок второй»", False, "l-2", 1),
		)
		# Архивная — прежнее место без релиза: ключом релиза не читается, даже похожая на него.
		карта = заметки[архив[5]]
		self.assertEqual(
			(карта["target"], карта["label"], карта["missing"], карта["release"], карта["version"]),
			("map.T1", "map.T1", True, None, None),
		)

	def test_без_таблицы_заметок_ничего_не_делает(self):
		with patch.object(frappe.db, "table_exists", return_value=False):
			вывод = self.выполнить()

		self.assertIn("note_release_keys: заметок нет", вывод)

	def test_без_колонки_урока_и_таблицы_ответов(self):
		"""Колонки `lesson` нет — место «урок» не переносится, а уходит в архив;
		таблицы ответов нет — архив без ответа, удаление без нитей."""
		from lms_frappe_app.patches.v0_1 import note_release_keys

		урок = self.старая(self.курс, "lesson", self.второй)
		блок = self.старая(self.курс, "block.notebook/log")
		удалить = self.старая(self.удалённый, "course")
		колонки = frappe.db.get_table_columns
		таблица = frappe.db.table_exists

		with (
			patch.object(
				frappe.db,
				"get_table_columns",
				side_effect=lambda doctype: [
					к for к in колонки(doctype) if not (doctype == note_release_keys.ЗАМЕТКА and к == "lesson")
				],
			),
			patch.object(
				frappe.db,
				"table_exists",
				side_effect=lambda doctype, cached=True: doctype != note_release_keys.ОТВЕТ and таблица(doctype, cached),
			),
		):
			self.выполнить()

		self.assertEqual((self.запись(урок)["status"], self.запись(урок)["replies"]), ("accepted", []))
		self.assertEqual(self.запись(блок)["target"], "section.log")
		self.assertIsNone(self.запись(удалить))


def создать_урок_без_релиза(курс: str) -> str:
	"""Урок курса без релиза — главой и уроком Learning."""
	глава = frappe.get_doc({"doctype": "Course Chapter", "title": "Глава", "course": курс}).insert(
		ignore_permissions=True
	)
	return frappe.get_doc(
		{"doctype": "Course Lesson", "title": "Урок", "chapter": глава.name, "course": курс}
	).insert(ignore_permissions=True).name
