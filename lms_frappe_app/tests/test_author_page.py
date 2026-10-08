# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import re
from unittest.mock import patch
from urllib.parse import quote

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import notices
from lms_frappe_app.api import authoring
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import создать_куратора, создать_ученика
from lms_frappe_app.www import author

#: Текст пояснения к ответу квиза образца релиза: его видит только автор.
ПОЯСНЕНИЕ = "Потому что так велит условие."
#: Текст из среза пакета агента урока `l-1` образца.
ПАКЕТ_УРОКА = "Директива урока l-1"
#: Строка, которая без экранирования закрыла бы атрибут и вставила тег.
ВРЕД = 'x"><img src=x onerror=alert(1)>'


def сведения_для(пользователь: str, **параметры) -> dict:
	from lms_frappe_app.www.author import сведения

	frappe.set_user(пользователь)
	return сведения(пользователь, **параметры)


def страница(пользователь: str, **параметры) -> str:
	"""HTML кабинета так, как его отдаёт сайт этому пользователю."""
	from frappe.website.serve import get_response

	прежние = frappe.local.form_dict
	frappe.set_user(пользователь)
	frappe.local.form_dict = frappe._dict(параметры)
	try:
		ответ = get_response("author")
	finally:
		frappe.local.form_dict = прежние
	html = ответ.get_data(as_text=True)
	assert ответ.status_code == 200, html[:500]
	return html


def места_заметок(html: str) -> set[str]:
	"""Адреса мест, у которых на странице есть кнопка заметки."""
	return set(re.findall(r'class="note-add" data-note-target="([^"]+)"', html))


class IntegrationTestAuthorPage(IntegrationTestCase):
	"""Кабинет автора: действующий релиз курса, заметки по его ключам,
	история, тестеры (lms-high-time/learning-services#512)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"author-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.ключ = f"author-{суффикс}"
		self.курс = authoring.publish_release(release=пример_релиза(self.ключ))["data"]["course"]

	def заметка(self, target: str, **правки) -> str:
		frappe.set_user(self.куратор)
		ответ = authoring.add_note(course=self.курс, target=target, text=f"Заметка к {target}", **правки)
		self.assertTrue(ответ["ok"], ответ)
		return ответ["data"]["id"]

	def тестер(self) -> str:
		тестер = создать_ученика(f"author-t-{frappe.generate_hash(length=6)}@example.com")
		with patch("frappe.sendmail"), patch.object(notices, "почта_есть", return_value=True):
			frappe.set_user(self.куратор)
			self.assertTrue(authoring.add_testers(course=self.курс, users=тестер)["data"]["added"])
		return тестер

	# --- права ---

	def test_гость_получает_приглашение_войти(self):
		с = сведения_для("Guest")

		self.assertTrue(с["is_guest"])
		self.assertIn("redirect-to=/author", с["login_url"])
		self.assertEqual(с["courses"], [])

	def test_гостю_страница_только_зовёт_войти(self):
		"""Гостю — то же, что ученику: ни курсов, ни релиза, ни ответов квиза."""
		for параметры in ({}, {"course": self.курс}, {"course": self.курс, "lesson": "l-1"}):
			with self.subTest(**параметры):
				html = страница("Guest", **параметры)
				self.assertIn("чтобы открыть кабинет", html)
				self.assertNotIn(ПОЯСНЕНИЕ, html)
				self.assertNotIn(ПАКЕТ_УРОКА, html)
				self.assertNotIn("Урок первый", html)
				self.assertNotIn("note-add", html)

	def test_ученик_и_тестер_получают_отказ(self):
		"""Кабинет — только авторским ролям: ответов квиза и пакета агента
		не видит ни ученик, ни тестер курса."""
		ученик = создать_ученика(f"author-s-{frappe.generate_hash(length=6)}@example.com")
		for кто in (ученик, self.тестер()):
			for параметры in ({}, {"course": self.курс}, {"course": self.курс, "lesson": "l-1"}):
				with self.subTest(кто=кто, **параметры):
					с = сведения_для(кто, **параметры)
					self.assertFalse(с["allowed"])
					self.assertEqual(
						(с["courses"], с["course"], с["release"], с["lesson"]), ([], None, None, None)
					)
					html = страница(кто, **параметры)
					self.assertIn("Кабинет открыт авторам курсов", html)
					self.assertNotIn(ПОЯСНЕНИЕ, html)
					self.assertNotIn(ПАКЕТ_УРОКА, html)

	# --- список ---

	def test_список_курсов_с_версией_заметками_и_тестерами(self):
		self.заметка("lesson.l-1")
		self.заметка("course", via="agent")
		принятая = self.заметка("section.log")
		authoring.set_note_status(note=принятая, status="accepted")
		self.тестер()
		без_релиза = authoring.create_course(title=f"Без релиза {self.ключ}", summary="к")["data"]["id"]
		коллега = создать_куратора(f"author-b-{frappe.generate_hash(length=6)}@example.com")

		курсы = {к["id"]: к for к in сведения_для(коллега)["courses"]}

		курс = курсы[self.курс]
		self.assertEqual(курс["release"]["version"], 1)
		self.assertEqual((курс["open_notes"], курс["notes_attention"], курс["testers_count"]), (2, 1, 1))
		self.assertEqual(курс["url"], f"/author?course={quote(self.курс)}")
		self.assertIsNone(курсы[без_релиза]["release"])
		self.assertEqual((курсы[без_релиза]["open_notes"], курсы[без_релиза]["testers_count"]), (0, 0))
		html = страница(коллега)
		self.assertIn("релиз v1 от", html)
		self.assertIn("заметок 2, ждут вас 1", html)
		self.assertIn("тестеров 1", html)

	# --- экран курса ---

	def test_экран_курса_показывает_релиз_целиком(self):
		с = сведения_для(self.куратор, course=self.курс)

		self.assertEqual(с["view"], "release")
		релиз = с["release"]
		self.assertEqual((релиз["key"], релиз["version"]), (self.ключ, 1))
		self.assertEqual(
			[(г["key"], г["number"], г["color"]) for г in релиз["chapters"]], [("ch-1", 1, 1), ("ch-2", 2, 2)]
		)
		self.assertEqual(
			[[(у["key"], у["number"]) for у in г["lessons"]] for г in релиз["chapters"]],
			[[("l-1", 1), ("l-2", 2)], [("l-3", 3)]],
		)
		первый = релиз["chapters"][0]["lessons"][0]
		self.assertEqual(первый["url"], f"/author?course={quote(self.курс)}&lesson=l-1")
		self.assertEqual(первый["label"], "Урок 1 «Урок первый»")
		цель = первый["objectives"][0]
		self.assertEqual((цель["target"], цель["text"]), ("objective.l-1-D1", "Цель урока «Урок первый»"))
		self.assertEqual(
			[п["target"] for п in цель["goals"]],
			["goal.l-1/term:T1", "goal.l-1/l-1-D1/V1", "goal.l-1/refute:M1"],
		)
		вопрос = первый["questions"][0]
		self.assertEqual(
			(вопрос["target"], вопрос["correct"], вопрос["answer"], вопрос["explanation"]),
			("question.S1/l-1-D1", "V1", "Первый", ПОЯСНЕНИЕ),
		)
		третий = релиз["chapters"][1]["lessons"][0]
		self.assertEqual(третий["homework"]["title"], "Задание")
		self.assertEqual(третий["homework"]["answer_mode_text"], "текстом")
		документ = релиз["document"]
		self.assertEqual((документ["key"], документ["title"]), ("notebook", "Тетрадь"))
		self.assertEqual(
			[(р["target"], [у["number"] for у in р["lessons"]]) for р in документ["sections"]],
			[("section.log", [1, 2]), ("section.rules", [2])],
		)

		html = страница(self.куратор, course=self.курс)
		for текст in (
			"Глава первая",
			"Урок третий",
			"Цель урока «Урок первый»",
			"Термин «пример»",
			"Ситуация и вопрос",
			ПОЯСНЕНИЕ,
			"Сделайте пример.",
			"Тетрадь",
			"Зачем ученику тетрадь.",
			"Кто отвечает",
			"обязательна, если заполнено «decision»",
		):
			self.assertIn(текст, html)
		# Пакет агента — только на странице урока.
		self.assertNotIn(ПАКЕТ_УРОКА, html)

	def test_урок_с_пакетом_агента_свёрнутым(self):
		с = сведения_для(self.куратор, course=self.курс, lesson="l-2")

		урок = с["lesson"]
		self.assertEqual((урок["key"], урок["number"], урок["chapter_title"]), ("l-2", 2, "Глава первая"))
		self.assertEqual((урок["prev"]["key"], урок["next"]["key"]), ("l-1", "l-3"))
		self.assertEqual([ч["name"] for ч in урок["agent"]], ["directive", "material", "items", "sections"])
		пункты = next(ч for ч in урок["agent"] if ч["name"] == "items")["entries"]
		self.assertEqual(
			[(п["key"], п["title"], п["target"]) for п in пункты],
			[
				("term:T1", "Термин «пример»", "agent.item.l-2/term:T1"),
				("l-2-D1/V1", "Выбор: «первый»", "agent.item.l-2/l-2-D1/V1"),
				("refute:M1", "Если проявится: «пример не нужен»", "agent.item.l-2/refute:M1"),
			],
		)
		разделы = next(ч for ч in урок["agent"] if ч["name"] == "sections")["entries"]
		self.assertEqual([(р["key"], р["title"]) for р in разделы], [("log", "Журнал"), ("rules", "Правила")])
		self.assertEqual([ч["name"] for ч in урок["frame"]], ["frame", "learn_about_student"])

		html = страница(self.куратор, course=self.курс, lesson="l-2")
		self.assertIn(ПОЯСНЕНИЕ, html)
		# Пакет — в свёрнутом блоке: `details` без `open`, текст пакета внутри.
		пакет = re.search(r'<details class="pack"[^>]*>(.*?)</details>', html, re.S)
		self.assertIsNotNone(пакет)
		self.assertNotIn(" open", пакет.group(0).split(">", 1)[0])
		self.assertIn("Директива урока l-2", пакет.group(1))
		self.assertEqual(html.count("Директива урока l-2"), 1)

	def test_ключи_и_тексты_релиза_экранируются(self):
		"""Ключи пакета агента — произвольные строки, названия и заметки — текст
		автора: ни один не выходит в HTML тегом или концом атрибута. Названия
		курса и урока — имена записей Learning, `<` и `>` в них Frappe не
		пускает: в названии урока — кавычка, тег — в карточке курса и зачине."""
		релиз = пример_релиза(f"xss-{self.ключ}")
		релиз["course"]["summary"] = f"Карточка {ВРЕД}"
		релиз["lessons"][0]["title"] = 'Урок x" data-x="1'
		релиз["lessons"][0]["hook"] = f"Зачин {ВРЕД}"
		пакет = релиз["agent"]["lessons"]["l-1"]
		пакет[ВРЕД] = "Часть пакета с таким ключом"
		пакет["extra"] = {ВРЕД: "Запись с таким ключом"}
		пакет["items"][ВРЕД] = "Пункт пакета с таким ключом"
		ответ = authoring.publish_release(release=релиз)
		self.assertTrue(ответ["ok"], ответ)
		курс = ответ["data"]["course"]
		self.assertTrue(authoring.add_note(course=курс, target="lesson.l-1", text=f"Заметка {ВРЕД}")["ok"])

		for параметры, дошло in (
			({}, None),
			({"course": курс}, "Урок x&#34; data-x=&#34;1"),
			({"course": курс, "view": "notes"}, "Заметка x&#34;"),
			({"course": курс, "lesson": "l-1"}, "onerror=alert(1)&gt;"),
		):
			with self.subTest(**параметры):
				# `<title>` — текст, а не разметка: его базовый шаблон Frappe
				# чистит `striptags`, и кавычка там ничего не закрывает.
				html = re.sub(r"<title>.*?</title>", "", страница(self.куратор, **параметры), flags=re.S)
				if дошло:
					self.assertIn(дошло, html, "строка не дошла до страницы — проверять нечего")
				self.assertNotIn("<img src=x", html)
				self.assertNotIn('<img src="x"', html)
				self.assertNotIn('x"><img', html)
				self.assertNotIn('x" data-x', html)
				for имя, значение in re.findall(r'\s(id|data-live)="([^"]*)"', html):
					self.assertRegex(значение, r"^[\w-]+$", имя)
				for адрес in re.findall(r'\shref="([^"]*)"', html):
					self.assertNotRegex(адрес, r"[<>]")

	def test_якорь_различает_места(self):
		self.assertNotEqual(author.якорь("objective.a.b"), author.якорь("objective.a-b"))
		self.assertRegex(author.якорь(f"agent.item.l-1/{ВРЕД}"), r"^n-[0-9a-f]+$")

	def test_урок_не_из_релиза_и_неизвестный_курс_дают_пометку(self):
		self.assertTrue(сведения_для(self.куратор, course=self.курс, lesson="l-9")["missing"])
		self.assertTrue(сведения_для(self.куратор, course="такого-курса-нет")["missing"])
		self.assertIn("Такого нет", страница(self.куратор, course=self.курс, lesson="l-9"))

	def test_история_релизов(self):
		второй = пример_релиза(self.ключ)
		второй["course"]["summary"] = "Вторая версия"
		authoring.publish_release(release=второй)

		с = сведения_для(self.куратор, course=self.курс, view="history")

		self.assertEqual(с["view"], "history")
		self.assertEqual([(р["version"], р["active"]) for р in с["history"]], [(2, True), (1, False)])
		self.assertEqual(с["release"]["version"], 2)
		self.assertTrue(с["history"][0]["published_by_name"])
		html = страница(self.куратор, course=self.курс, view="history")
		self.assertIn("История релизов", html)
		self.assertIn(с["history"][1]["digest"][:12], html)

	# --- заметки ---

	def test_заметка_на_каждом_месте_просмотра(self):
		"""Место заметки — у каждой части просмотра; ссылка заметки ведёт на ту
		страницу, где её место показано, и карточка там стоит."""
		экран_курса = {"course", "chapter.ch-1", "section.log"}
		страница_урока = {
			"lesson.l-1",
			"objective.l-1-D1",
			"goal.l-1/term:T1",
			"question.S1/l-1-D1",
			"agent.lesson.l-1",
			"agent.item.l-1/term:T1",
		}
		заметки = {место: self.заметка(место) for место in экран_курса | страница_урока | {"agent.frame"}}
		курс = страница(self.куратор, course=self.курс)
		урок = страница(self.куратор, course=self.курс, lesson="l-1")
		очередь = {
			з["target"]: з
			for записи in сведения_для(self.куратор, course=self.курс, view="notes")["notes_queue"].values()
			for з in записи
		}

		for место, ид in заметки.items():
			with self.subTest(место=место):
				якорь = author.якорь(место)
				if место in экран_курса:
					html, адрес = курс, f"/author?course={quote(self.курс)}#{якорь}"
				elif место in страница_урока:
					html, адрес = урок, f"/author?course={quote(self.курс)}&lesson=l-1#{якорь}"
				else:
					# Рамка пакета — на странице каждого урока, ссылка ведёт в очередь.
					html, адрес = урок, f"/author?course={quote(self.курс)}&view=notes#note-card-{ид}"
				self.assertIn(место, места_заметок(html))
				self.assertIn(f'id="{якорь}"', html)
				self.assertIn(f'id="note-card-{ид}"', html)
				self.assertEqual(очередь[место]["url"], адрес)

		# Цели, пункты и вопросы урока — и в свёрнутой строке урока на экране курса.
		self.assertLessEqual(
			страница_урока - {"agent.lesson.l-1", "agent.item.l-1/term:T1"}, места_заметок(курс)
		)
		self.assertFalse({м for м in места_заметок(курс) if м.startswith("agent.")})

	def test_заметка_к_разделу_только_у_разделов_релиза(self):
		html = страница(self.куратор, course=self.курс)

		разделы = {м for м in места_заметок(html) if м.startswith("section.")}
		self.assertEqual(разделы, {"section.log", "section.rules"})
		self.assertNotIn("map.", " ".join(места_заметок(html)))

	def test_очередь_и_заметки_без_места(self):
		ждёт_агента = self.заметка("lesson.l-1")
		сделано = self.заметка("question.S1/l-1-D1")
		authoring.set_note_status(note=сделано, status="done", text="Поправил", via="agent")
		вопрос_агента = self.заметка("course", via="agent")
		снятый = self.заметка("lesson.l-3")
		без_третьего = пример_релиза(self.ключ)
		без_третьего["chapters"] = без_третьего["chapters"][:1]
		без_третьего["lessons"] = без_третьего["lessons"][:2]
		authoring.publish_release(release=без_третьего)

		с = сведения_для(self.куратор, course=self.курс, view="notes")

		очередь = {группа: [з["id"] for з in записи] for группа, записи in с["notes_queue"].items()}
		self.assertEqual(
			очередь,
			{"check": [сделано], "question": [вопрос_агента], "agent": [ждёт_агента, снятый], "accepted": []},
		)
		self.assertEqual(с["course"]["notes_attention"], 2)
		снятая = next(з for з in с["notes_queue"]["agent"] if з["id"] == снятый)
		self.assertTrue(снятая["missing"])
		self.assertTrue(снятая["url"].endswith(f"&view=notes#note-card-{снятый}"))

		урок = сведения_для(self.куратор, course=self.курс, lesson="l-1")
		self.assertEqual([з["id"] for з in урок["lesson"]["notes_index"]], [сделано, ждёт_агента])
		self.assertEqual(урок["lesson"]["open_notes"], 2)
		html = страница(self.куратор, course=self.курс, view="notes")
		for ид in (ждёт_агента, сделано, вопрос_агента, снятый):
			self.assertIn(f'id="note-card-{ид}"', html)
		self.assertIn("Заметка к курсу", html)
		self.assertNotIn("амечани", html)

	# --- тестеры ---

	def test_тестеры(self):
		тестер = self.тестер()

		с = сведения_для(self.куратор, course=self.курс, view="testers")

		self.assertEqual(с["view"], "testers")
		self.assertEqual([т["user"] for т in с["testers"]], [тестер])
		self.assertEqual(с["course"]["testers_count"], 1)
		self.assertEqual(сведения_для(self.куратор, course=self.курс)["course"]["testers_count"], 1)
		html = страница(self.куратор, course=self.курс, view="testers")
		self.assertIn(тестер, html)
		self.assertIn("authoring.add_testers", html)

	# --- анонс ---

	def test_курс_без_релиза_карточкой_анонса(self):
		курс = authoring.create_course(title=f"Анонс {self.ключ}", summary="Скоро")["data"]["id"]
		self.assertTrue(
			authoring.announce_course(course=курс, objectives=["Уметь первое", "Уметь второе"])["ok"]
		)

		с = сведения_для(self.куратор, course=курс, view="notes", lesson="l-1")

		self.assertFalse(с["missing"])
		self.assertIsNone(с["release"])
		self.assertIsNone(с["lesson"])
		self.assertIsNone(с["notes_queue"])
		self.assertEqual((с["course"]["status"], с["course"]["upcoming"]), ("анонс", True))
		self.assertEqual(с["course"]["announce_objectives"], ["Уметь первое", "Уметь второе"])
		html = страница(self.куратор, course=курс)
		self.assertIn("Цели анонса", html)
		self.assertIn("Уметь второе", html)
		self.assertEqual(места_заметок(html), set())
		self.assertNotIn('class="tabs"', html)

	# --- опрос ---

	def test_опрос_идёт_по_course_revision(self):
		с = сведения_для(self.куратор, course=self.курс)
		отметки = authoring.course_revision(course=self.курс)["data"]
		self.assertEqual(
			(с["course"]["revision"], с["course"]["notes_revision"]), (отметки["revision"], None)
		)

		html = страница(self.куратор, course=self.курс)
		self.assertIn('"/api/method/" + "lms_frappe_app.api.authoring.course_revision" + "?course="', html)
		self.assertIn(frappe.as_json(отметки["revision"]), html)

		self.заметка("course")
		второй = пример_релиза(self.ключ)
		второй["course"]["summary"] = "Вторая версия"
		authoring.publish_release(release=второй)
		с = сведения_для(self.куратор, course=self.курс)
		self.assertNotEqual(с["course"]["revision"], отметки["revision"])
		self.assertIsNotNone(с["course"]["notes_revision"])
