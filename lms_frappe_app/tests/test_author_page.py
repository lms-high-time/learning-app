# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

from urllib.parse import quote

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import authoring
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	политика_по_умолчанию,
	создать_куратора,
	создать_ученика,
	урок_релиза,
)


class IntegrationTestAuthorPage(IntegrationTestCase):
	"""Кабинет автора: зеркало курса, собранного агентом (#261)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(политика_по_умолчанию)
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"author-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Кабинет {суффикс}", summary="Для зеркала")["data"]["id"]
		первая = authoring.add_chapter(course=self.курс, title="Рамка")["data"]["id"]
		вторая = authoring.add_chapter(course=self.курс, title="Сборка")["data"]["id"]
		self.материал = "## Первый раздел\n\nТекст с **выделением**.\n\n" + "\n\n".join(["абзац. " * 40] * 4)
		self.уроки = [
			authoring.add_lesson(chapter=первая, title="Первый", body=self.материал)["data"]["id"],
			authoring.add_lesson(chapter=первая, title="Второй", body="")["data"]["id"],
			authoring.add_lesson(chapter=вторая, title="Третий", body="# Третий\n\nТекст.")["data"]["id"],
		]
		authoring.set_directive(
			lesson=self.уроки[0],
			teaching_directive="Начни с проекта.",
			objectives="Цель один\nЦель два",
			success_criteria="Ученик сдал квиз",
		)
		authoring.add_quiz(
			lesson=self.уроки[0],
			passing_percentage=60,
			questions=[
				{
					"text": "Что здесь не так?",
					"options": [
						{"text": "Верно", "correct": True, "explanation": "Потому что."},
						{"text": "Неверно"},
					],
				}
			],
		)
		authoring.set_course_artifact(
			course=self.курс,
			artifact="register",
			title="Реестр",
			blocks=[
				{"key": "risks", "title": "Риски", "hint": "Пять записей", "lesson": self.уроки[0]},
				{"key": "review", "title": "Сверка"},
			],
		)

	def сведения_для(self, пользователь: str, **параметры) -> dict:
		from lms_frappe_app.www.author import сведения

		frappe.set_user(пользователь)
		return сведения(пользователь, **параметры)

	def урок_структуры(self, с: dict, урок: str) -> dict:
		return next(у for г in с["course"]["chapters"] for у in г["lessons"] if у["id"] == урок)

	def test_гость_получает_приглашение_войти(self):
		с = self.сведения_для("Guest")

		self.assertTrue(с["is_guest"])
		self.assertIn("redirect-to=/author", с["login_url"])
		self.assertEqual(с["courses"], [])

	def test_ученику_кабинет_закрыт(self):
		ученик = создать_ученика(f"author-s-{frappe.generate_hash(length=6)}@example.com")

		с = self.сведения_для(ученик, course=self.курс)

		self.assertFalse(с["allowed"])
		self.assertIsNone(с["course"])
		self.assertEqual(с["courses"], [])

	def test_куратор_видит_курсы_других_кураторов(self):
		"""Курсы общие: второй автор видит курс, который собирал первый."""
		коллега = создать_куратора(f"author-b-{frappe.generate_hash(length=6)}@example.com")

		с = self.сведения_для(коллега)

		self.assertIn(self.курс, [к["id"] for к in с["courses"]])

	def test_анонс_отмечен_на_экране_курса_и_в_списке(self):
		"""Анонс — не «опубликован»: он в каталоге, но записи нет (learning-services#391)."""
		# Напрямую: курс с уроками без релиза анонс не принимает (`course_has_content`).
		frappe.db.set_value("LMS Course", self.курс, {"published": 1, "upcoming": 1})
		с = self.сведения_для(self.куратор, course=self.курс)
		self.assertTrue(с["course"]["upcoming"])
		с = self.сведения_для(self.куратор)
		self.assertTrue(next(к for к in с["courses"] if к["id"] == self.курс)["upcoming"])

	def test_экран_курса_показывает_наполненность_и_блоки(self):
		с = self.сведения_для(self.куратор, course=self.курс)

		курс = с["course"]
		self.assertEqual(
			курс["counts"], {"lessons": 3, "with_body": 2, "with_directive": 1, "with_quiz": 1}
		)
		self.assertEqual([г["color"] for г in курс["chapters"]], [1, 2])
		первый = self.урок_структуры(с, self.уроки[0])
		self.assertEqual(первый["number"], 1)
		self.assertEqual(первый["blocks"], ["risks"])
		self.assertIn(f"lesson={quote(self.уроки[0])}", первый["url"])
		self.assertEqual(self.урок_структуры(с, self.уроки[2])["number"], 3)
		блоки = курс["artifacts"][0]["blocks"]
		self.assertEqual([(б["key"], б["lesson_title"]) for б in блоки], [("risks", "Первый"), ("review", None)])

	def test_документ_показывает_шаблон_и_правки_курса(self):
		"""Шаблон, его версия у курса, вышедшая новая, база наследника и правки
		курса словами; у схемы целиком — ни шаблона, ни правок (learning-services#384).
		Описание — закреплённой версии, а не последней (learning-services#387)."""
		база = f"base-{frappe.generate_hash(length=6)}"
		authoring.set_artifact_template(
			template=база,
			title="Реестр",
			blocks=[
				{"key": "risks", "title": "Риски", "hint": "Пять записей"},
				{"key": "review", "title": "Сверка"},
				{"key": "extra", "title": "Лишнее"},
			],
		)
		наследник = f"child-{frappe.generate_hash(length=6)}"
		authoring.set_artifact_template(
			template=наследник,
			title="Реестр",
			extends=база,
			overlay={"blocks": {"review": {"hint": "Сверьте"}}},
			description="Реестр со сверкой: подсказка сверки своя",
		)
		привязка = authoring.set_course_artifact_template(
			course=self.курс,
			artifact="journal",
			template=наследник,
			overlay={
				"blocks": {"risks": {"lesson": self.уроки[0], "hint": "Три записи"}, "extra": None},
				"add_blocks": [{"key": "photo", "title": "Фото", "lesson": self.уроки[2]}],
			},
		)
		self.assertTrue(привязка["ok"], привязка)
		authoring.set_artifact_template(
			template=наследник,
			title="Реестр",
			extends=база,
			overlay={},
			note="Без подсказки сверки",
			description="Реестр как в базе",
		)

		с = self.сведения_для(self.куратор, course=self.курс)

		документы = {д["artifact"]: д for д in с["course"]["artifacts"]}
		self.assertIsNone(документы["register"]["binding"])
		self.assertEqual(
			документы["journal"]["binding"],
			{
				"template": наследник,
				"version": 1,
				"description": "Реестр со сверкой: подсказка сверки своя",
				"latest": 2,
				"extends": {"template": база, "version": 1},
				"edits": [
					"Уроки заданы у 2 блоков",
					"Подсказки изменены: «Риски»",
					"Убран блок «Лишнее»",
					"Добавлен блок «Фото»",
				],
			},
		)

	def test_готовность_ссылается_на_урок(self):
		с = self.сведения_для(self.куратор, course=self.курс)

		пустой = [п for п in с["course"]["readiness"]["blocking"] if п.get("lesson") == self.уроки[1]]
		self.assertTrue(пустой, с["course"]["readiness"])
		self.assertEqual(пустой[0]["lesson_title"], "Второй")
		self.assertIn(f"lesson={quote(self.уроки[1])}", пустой[0]["url"])

	def test_экран_урока_целиком(self):
		frappe.db.set_single_value("Agent Learning Settings", "lesson_segment_limit", 300)
		frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")

		с = self.сведения_для(self.куратор, course=self.курс, lesson=self.уроки[0])

		урок = с["lesson"]
		self.assertGreater(len(урок["segments_html"]), 1)
		self.assertIn("<strong>выделением</strong>", урок["segments_html"][0])
		self.assertEqual(урок["facts"]["body_segments"], len(урок["segments_html"]))
		self.assertEqual(урок["directive"]["objectives"], ["Цель один", "Цель два"])
		self.assertEqual(урок["quiz"]["passing_percentage"], 60)
		self.assertEqual(урок["blocks"][0]["key"], "risks")
		self.assertIsNone(урок["prev"])
		self.assertEqual(урок["next"]["id"], self.уроки[1])
		self.assertEqual(урок["chapter_title"], "Рамка")

	def test_соседи_урока_через_границу_главы(self):
		с = self.сведения_для(self.куратор, course=self.курс, lesson=self.уроки[1])

		self.assertEqual(с["lesson"]["prev"]["id"], self.уроки[0])
		self.assertEqual(с["lesson"]["next"]["id"], self.уроки[2])
		self.assertIsNone(с["lesson"]["directive"])
		self.assertEqual(с["lesson"]["segments_html"], [])

	def test_урок_не_из_курса_и_неизвестный_курс_дают_пометку(self):
		чужой_курс = authoring.create_course(title=f"Другой {frappe.generate_hash(length=6)}", summary="к")[
			"data"
		]["id"]

		self.assertTrue(self.сведения_для(self.куратор, course=чужой_курс, lesson=self.уроки[0])["missing"])
		self.assertTrue(self.сведения_для(self.куратор, course="такого-курса-нет")["missing"])


class IntegrationTestAuthorPageMap(IntegrationTestCase):
	"""Вкладка «Карта»: карта декомпозиции против собранного курса
	(lms-high-time/learning-services#264)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"author-map-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Карта {суффикс}", summary="к")["data"]["id"]
		глава = authoring.add_chapter(course=self.курс, title="Рамка")["data"]["id"]
		self.урок = authoring.add_lesson(chapter=глава, title="Первый", body="# Первый")["data"]["id"]
		authoring.set_directive(lesson=self.урок, teaching_directive="Веди", objectives="Цель один")

	def сведения_для(self, **параметры) -> dict:
		from lms_frappe_app.www.author import сведения

		return сведения(self.куратор, course=self.курс, **параметры)

	def test_вкладка_карта_отдаёт_сверку_со_ссылками_на_уроки(self):
		authoring.set_course_map(
			course=self.курс,
			levels=[{"key": "result", "title": "Результат"}, {"key": "thesis", "title": "Тезисы"}],
			nodes=[
				{"id": "R", "level": "result", "text": "Курс собран"},
				{"id": "T1", "level": "thesis", "text": "Цель два", "parents": ["R"], "lesson": "u1", "objective": True},
			],
			lessons=[{"key": "u1", "title": "Первый", "chapter": "Рамка"}],
		)

		с = self.сведения_для(view="map")

		self.assertEqual(с["view"], "map")
		self.assertEqual(с["map_check"]["map"]["version"], 1)
		(урок,) = с["map_check"]["platform"]["lessons"]
		self.assertIn(f"lesson={quote(self.урок)}", урок["url"])
		self.assertEqual(с["course"]["map_discrepancies"], с["map_check"]["counts"]["total"])
		self.assertEqual(с["course"]["map_discrepancies"], 1)

	def test_без_карты_вкладка_знает_что_её_нет(self):
		с = self.сведения_для(view="map")

		self.assertIsNone(с["map_check"]["map"])
		self.assertIsNone(с["course"]["map_discrepancies"])

	def test_без_вкладки_и_с_неизвестной_показывается_сборка(self):
		for вид in (None, "что-то"):
			with self.subTest(вид=вид):
				с = self.сведения_для(view=вид)

				self.assertEqual(с["view"], "build")
				self.assertIsNone(с["map_check"])


def сведения_списка(пользователь: str) -> list[dict]:
	from lms_frappe_app.www.author import сведения

	return сведения(пользователь)["courses"]


class IntegrationTestAuthorPageNotes(IntegrationTestCase):
	"""Заметки в кабинете по ключам релиза: очередь по тому, что ждёт человека,
	заметки урока по местам, счётчики и ссылки на места
	(lms-high-time/learning-services#266, #512)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"author-notes-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.ключ = f"author-notes-{суффикс}"
		self.курс = authoring.publish_release(release=пример_релиза(self.ключ))["data"]["course"]
		self.урок = урок_релиза(self.курс, "l-1")

		self.ждёт_агента = self.добавить("lesson.l-1")
		self.сделано = self.добавить("question.S1/l-1-D1")
		authoring.set_note_status(note=self.сделано, status="done", text="Поправил", via="agent")
		self.вопрос_агента = self.добавить("course", via="agent")
		self.принято = self.добавить("goal.l-1/term:T1")
		authoring.set_note_status(note=self.принято, status="accepted")
		self.на_разделе = self.добавить("section.log")

	def добавить(self, target: str, **правки) -> str:
		ответ = authoring.add_note(course=self.курс, target=target, text="Заметка", **правки)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]["id"]

	def сведения_для(self, **параметры) -> dict:
		from lms_frappe_app.www.author import сведения

		return сведения(self.куратор, course=self.курс, **параметры)

	def по_ид(self, ид: str) -> dict:
		очередь = self.сведения_для(view="notes")["notes_queue"]
		return next(з for записи in очередь.values() for з in записи if з["id"] == ид)

	def test_очередь_по_тому_что_ждёт_человека(self):
		с = self.сведения_для(view="notes")

		self.assertEqual(с["view"], "notes")
		очередь = {группа: [з["id"] for з in записи] for группа, записи in с["notes_queue"].items()}
		self.assertEqual(
			очередь,
			{
				"check": [self.сделано],
				"question": [self.вопрос_агента],
				"agent": [self.ждёт_агента, self.на_разделе],
				"accepted": [self.принято],
			},
		)
		self.assertEqual(с["course"]["notes_attention"], 2)

	def test_ссылки_ведут_на_место(self):
		урок = self.по_ид(self.ждёт_агента)["url"]
		self.assertIn(f"lesson={quote(self.урок)}", урок)
		self.assertTrue(урок.endswith("#note-lesson-l-1"))
		вопрос = self.по_ид(self.сделано)["url"]
		self.assertIn(f"lesson={quote(self.урок)}", вопрос)
		self.assertTrue(вопрос.endswith("#section-notes"))
		self.assertTrue(self.по_ид(self.вопрос_агента)["url"].endswith("#note-course"))
		раздел = self.по_ид(self.на_разделе)["url"]
		self.assertNotIn("lesson=", раздел)
		self.assertTrue(раздел.endswith("#note-section-log"))

	def test_место_которого_нет_ведёт_в_очередь(self):
		ид = self.добавить("lesson.l-3")
		без_третьего = пример_релиза(self.ключ)
		без_третьего["chapters"] = без_третьего["chapters"][:1]
		без_третьего["lessons"] = без_третьего["lessons"][:2]
		authoring.publish_release(release=без_третьего)

		з = self.по_ид(ид)

		self.assertTrue(з["missing"])
		self.assertTrue(з["url"].endswith(f"&view=notes#note-card-{ид}"))

	def test_урок_показывает_свои_заметки_по_местам(self):
		урок = self.сведения_для(lesson=self.урок)["lesson"]

		self.assertEqual(урок["key"], "l-1")
		self.assertEqual(
			{место: [з["id"] for з in записи] for место, записи in урок["notes"].items()},
			{
				"lesson.l-1": [self.ждёт_агента],
				"question.S1/l-1-D1": [self.сделано],
				"goal.l-1/term:T1": [self.принято],
			},
		)
		self.assertEqual(урок["open_notes"], 3)

	def test_в_таблице_структуры_открытые_заметки_урока(self):
		с = self.сведения_для()

		уроки = {у["key"]: у for г in с["course"]["chapters"] for у in г["lessons"]}
		self.assertEqual(с["course"]["release"], frappe.db.get_value("LMS Course", self.курс, "active_release"))
		self.assertEqual((уроки["l-1"]["open_notes"], уроки["l-2"]["open_notes"]), (3, 0))
		self.assertIsNotNone(с["course"]["notes_revision"])
		self.assertEqual(
			{место: [з["id"] for з in записи] for место, записи in с["course"]["notes"].items()},
			{"course": [self.вопрос_агента], "section.log": [self.на_разделе]},
		)

	def test_список_курсов_говорит_куда_идти(self):
		с = self.сведения_для()
		курс = next(к for к in сведения_списка(self.куратор) if к["id"] == self.курс)

		self.assertEqual(курс["notes_attention"], 2)
		self.assertEqual(курс["revision"], с["course"]["revision"])

	def test_указатель_заметок_урока(self):
		урок = self.сведения_для(lesson=self.урок)["lesson"]

		self.assertEqual(
			[з["id"] for з in урок["notes_index"]], [self.сделано, self.ждёт_агента, self.на_разделе, self.принято]
		)
		места = {з["id"]: (з["place"], з["here"]) for з in урок["notes_index"]}
		self.assertEqual(
			места,
			{
				self.ждёт_агента: ("Урок целиком", True),
				self.сделано: ("Вопрос «Ситуация и вопрос»", False),
				self.на_разделе: ("Документ · раздел «Журнал»", True),
				self.принято: ("Пункт «Термин «пример»»", False),
			},
		)

	def test_у_курса_без_релиза_мест_для_заметок_нет(self):
		курс = authoring.create_course(title=f"Без релиза {frappe.generate_hash(length=6)}", summary="к")["data"]["id"]
		глава = authoring.add_chapter(course=курс, title="Рамка")["data"]["id"]
		урок = authoring.add_lesson(chapter=глава, title="Первый", body="# Первый")["data"]["id"]
		from lms_frappe_app.www.author import сведения

		с = сведения(self.куратор, course=курс, lesson=урок)

		self.assertIsNone(с["course"]["release"])
		self.assertIsNone(с["lesson"]["key"])
		self.assertEqual((с["lesson"]["notes"], с["lesson"]["notes_index"]), ({}, []))

	def test_страница_заметок_рисуется(self):
		"""Шаблон кабинета с местами заметок по ключам: экран курса, урок, очередь."""
		from frappe.website.serve import get_response

		self.addCleanup(setattr, frappe.local, "form_dict", frappe.local.form_dict)
		for параметры, якоря in (
			({}, ("note-course", "note-section-log")),
			({"lesson": self.урок}, ("note-lesson-l-1", f"note-card-{self.ждёт_агента}")),
			({"view": "notes"}, (f"note-card-{self.сделано}",)),
		):
			with self.subTest(**параметры):
				frappe.local.form_dict = frappe._dict(course=self.курс, **параметры)
				ответ = get_response("author")
				страница = ответ.get_data(as_text=True)
				self.assertEqual(ответ.status_code, 200, страница[:500])
				for якорь in якоря:
					self.assertIn(якорь, страница)
				self.assertNotIn("data-note-lesson", страница)


class IntegrationTestAuthorPageLessonMap(IntegrationTestCase):
	"""Расхождения урока с картой — в шапке урока и меткой в таблице структуры,
	без перехода на вкладку «Карта» (lms-high-time/learning-services#266)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"author-lmap-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Урок и карта {суффикс}", summary="к")["data"]["id"]
		глава = authoring.add_chapter(course=self.курс, title="Рамка")["data"]["id"]
		self.первый = authoring.add_lesson(chapter=глава, title="Первый", body="# Первый")["data"]["id"]
		self.второй = authoring.add_lesson(chapter=глава, title="Второй", body="# Второй")["data"]["id"]
		authoring.set_directive(lesson=self.первый, teaching_directive="Веди", objectives="Не та цель")
		authoring.set_directive(lesson=self.второй, teaching_directive="Веди", objectives="Цель два")
		authoring.set_course_artifact(
			course=self.курс,
			artifact="register",
			title="Реестр",
			blocks=[{"key": "risks", "title": "Риски", "hint": "Пять записей", "lesson": self.второй}],
		)

	def записать_карту(self):
		authoring.set_course_map(
			course=self.курс,
			levels=[
				{"key": "result", "title": "Результат"},
				{"key": "skill", "title": "Умения", "needs_children": True},
				{"key": "thesis", "title": "Тезисы", "needs_lesson": True},
			],
			nodes=[
				{"id": "R", "level": "result", "text": "Курс"},
				{"id": "S1", "level": "skill", "text": "Умение", "parents": ["R"]},
				{"id": "T1", "level": "thesis", "text": "Цель один", "parents": ["S1"], "lesson": "u1", "objective": True},
				{"id": "T2", "level": "thesis", "text": "Цель два", "parents": ["S1"], "lesson": "u2", "objective": True},
				{"id": "T9", "level": "thesis", "text": "Сирота", "parents": [], "lesson": "u1"},
			],
			lessons=[{"key": "u1", "title": "Первый"}, {"key": "u2", "title": "Второй"}],
			blocks=[{"artifact": "register", "key": "risks", "lesson": "u1"}],
		)

	def сведения_для(self, **параметры) -> dict:
		from lms_frappe_app.www.author import сведения

		return сведения(self.куратор, course=self.курс, **параметры)

	def test_урок_получает_свои_расхождения_и_ссылку_на_карту(self):
		self.записать_карту()

		урок = self.сведения_для(lesson=self.первый)["lesson"]

		коды = sorted((р["group"], р["code"]) for р in урок["map_issues"])
		self.assertEqual(коды, [("blocks", "lesson"), ("integrity", "orphan"), ("objectives", "mismatch")])
		self.assertIn("view=map", урок["map_url"])
		self.assertIn(f"node=lesson%3A{quote(self.первый, safe='')}", урок["map_url"])

	def test_таблица_структуры_метит_уроки_с_расхождениями(self):
		self.записать_карту()

		уроки = {у["id"]: у for г in self.сведения_для()["course"]["chapters"] for у in г["lessons"]}

		self.assertEqual(уроки[self.первый]["map_issues"], 3)
		self.assertEqual(уроки[self.второй]["map_issues"], 1)

	def test_список_курсов_считает_расхождения_с_картой(self):
		self.записать_карту()

		курс = next(к for к in сведения_списка(self.куратор) if к["id"] == self.курс)

		всего = authoring.course_map_check(course=self.курс)["data"]["counts"]["total"]
		self.assertGreater(всего, 0)
		self.assertEqual(курс["map_discrepancies"], всего)

	def test_без_карты_у_урока_расхождений_нет(self):
		урок = self.сведения_для(lesson=self.первый)["lesson"]

		self.assertEqual(урок["map_issues"], [])
		self.assertIsNone(урок["map_url"])


class IntegrationTestAuthorPageVisits(IntegrationTestCase):
	"""Отметки «изменено с вашего прошлого визита»: у урока — что именно, в
	таблице структуры — какие уроки (lms-high-time/learning-services#271)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"author-visit-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Визиты {суффикс}", summary="к")["data"]["id"]
		глава = authoring.add_chapter(course=self.курс, title="Рамка")["data"]["id"]
		self.урок = authoring.add_lesson(chapter=глава, title="Первый", body="# Первый\n\nТекст.")["data"]["id"]
		self.второй = authoring.add_lesson(chapter=глава, title="Второй", body="# Второй")["data"]["id"]
		authoring.set_directive(lesson=self.урок, teaching_directive="Веди")

	def сведения_для(self, **параметры) -> dict:
		from lms_frappe_app.www.author import сведения

		return сведения(self.куратор, course=self.курс, **параметры)

	def отметить(self, урок: str | None = None) -> dict:
		from lms_frappe_app.www.author import mark_lesson_seen

		return mark_lesson_seen(lesson=урок or self.урок)

	def сдвинуть_визит(self, минут: int) -> None:
		"""Визит был `минут` назад — как будто автор ушёл и вернулся."""
		имя = frappe.db.get_value("Agent Author Visit", {"author": self.куратор, "lesson": self.урок})
		когда = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-минут)
		frappe.db.set_value("Agent Author Visit", имя, "last_at", когда, update_modified=False)

	def поправить(self) -> None:
		authoring.update_lesson(lesson=self.урок, body="# Первый\n\nНовый текст.")

	def test_первый_визит_без_отметок(self):
		self.assertTrue(self.отметить()["ok"])

		self.assertIsNone(self.сведения_для(lesson=self.урок)["lesson"]["changes"])

	def test_после_перерыва_видно_что_изменилось(self):
		self.отметить()
		self.сдвинуть_визит(45)
		self.поправить()

		изменения = self.сведения_для(lesson=self.урок)["lesson"]["changes"]

		self.assertEqual(list(изменения["places"]), ["material"])
		self.assertEqual([пункт["text"] for пункт in изменения["summary"]], ["материал"])
		self.assertIsNotNone(изменения["since"])

	def test_внутри_сеанса_отметки_не_сбрасываются(self):
		"""Открыл урок после перерыва и перезагрузил через пять минут —
		отметки те же: база — визит до сеанса."""
		self.отметить()
		self.сдвинуть_визит(45)
		self.поправить()
		self.отметить()
		self.сдвинуть_визит(5)

		изменения = self.сведения_для(lesson=self.урок)["lesson"]["changes"]

		self.assertEqual(list(изменения["places"]), ["material"])

	def test_следующий_сеанс_считается_от_этого_визита(self):
		self.отметить()
		self.сдвинуть_визит(45)
		self.поправить()
		self.отметить()
		self.сдвинуть_визит(45)

		self.assertIsNone(self.сведения_для(lesson=self.урок)["lesson"]["changes"])

	def test_таблица_метит_уроки_изменённые_после_визита(self):
		self.отметить()
		self.поправить()

		уроки = {у["id"]: у for г in self.сведения_для()["course"]["chapters"] for у in г["lessons"]}

		self.assertTrue(уроки[self.урок]["changed_since_visit"])
		self.assertFalse(уроки[self.второй]["changed_since_visit"])

	def test_ученику_отметка_закрыта(self):
		frappe.set_user(создать_ученика(f"author-visit-s-{frappe.generate_hash(length=6)}@example.com"))

		with self.assertRaises(frappe.PermissionError):
			self.отметить()
