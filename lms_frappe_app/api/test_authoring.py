# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Курс, собранный агентом куратора, против трактовки Frappe Learning.

`Why:` авторинг пишет ровно те структуры, чтение которых уже трижды ломалось —
строки порядка глав и уроков. Курс, собранный нашими методами, обязан читаться
самой платформой так же, как собранный руками в интерфейсе; иначе ученик пойдёт
по одной последовательности, а куратор будет видеть другую.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.sample_data import создать_куратора, создать_ученика
from lms_frappe_app.agent_learning import quiz
from lms_frappe_app.agent_learning import structure
from lms_frappe_app.agent_learning.structure import уроки_главы
from lms_frappe_app.agent_learning import snapshots
from lms_frappe_app.api import authoring


class IntegrationTestAuthoring(IntegrationTestCase):
	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"curator-{суффикс}@example.com")
		frappe.set_user(self.куратор)

		self.курс = authoring.create_course(title=f"Курс {суффикс}", summary="Собран агентом")["data"]["id"]
		self.глава = authoring.add_chapter(course=self.курс, title="Глава")["data"]["id"]
		self.уроки = [
			authoring.add_lesson(chapter=self.глава, title=f"Урок {б}", body=f"# {б}")["data"]["id"]
			for б in "ABC"
		]

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_собранный_курс_читается_frappe_learning(self):
		from lms.lms.utils import get_chapters, get_lessons

		self.assertEqual([у["name"] for у in get_lessons(self.курс)], self.уроки)
		self.assertEqual([г["name"] for г in get_chapters(self.курс)], [self.глава])

	def test_перестановка_видна_frappe_learning(self):
		from lms.lms.utils import get_lessons

		новый = [self.уроки[2], self.уроки[0], self.уроки[1]]
		authoring.reorder_lessons(chapter=self.глава, lessons=новый)

		self.assertEqual(уроки_главы(self.глава), новый)
		self.assertEqual([у["name"] for у in get_lessons(self.курс)], новый)

	def test_неполный_список_отклоняется(self):
		"""Агент, забывший урок, иначе молча выкинул бы его из программы."""
		ответ = authoring.reorder_lessons(chapter=self.глава, lessons=self.уроки[:2])

		self.assertEqual(ответ["error"]["code"], "order_mismatch")
		self.assertEqual(уроки_главы(self.глава), self.уроки)

	def test_курс_без_релиза_не_открывается(self):
		"""Открывается только курс с действующим релизом."""
		ответ = authoring.publish_course(course=self.курс)

		self.assertEqual(ответ["error"]["code"], "course_not_released")
		self.assertFalse(frappe.db.get_value("LMS Course", self.курс, "published"))

	def test_снятие_несуществующего_курса_даёт_код(self):
		ответ = authoring.unpublish_course(course="нет-такого-курса")

		self.assertEqual(ответ["error"]["code"], "course_not_found")

	def test_кривой_вопрос_не_оставляет_мусора(self):
		"""Отказ отменяет только вопросы этого квиза, но отменяет их все."""
		было = frappe.db.count("LMS Question")

		ответ = authoring.add_quiz(
			lesson=self.уроки[0],
			questions=[
				{"text": "Верный есть", "options": [{"text": "a", "correct": True}, {"text": "b"}]},
				{"text": "Верного нет", "options": [{"text": "a"}, {"text": "b"}]},
			],
		)

		self.assertEqual(ответ["error"]["code"], "invalid_question")
		self.assertEqual(ответ["error"]["question_index"], 2)
		self.assertEqual(frappe.db.count("LMS Question"), было, "остались вопросы от сбойного квиза")
		self.assertIsNone(frappe.db.get_value("Course Lesson", self.уроки[0], "quiz_id"))

	def test_ученик_не_собирает_курсы_и_не_видит_эталонов(self):
		"""Отдельный эндпоинт ничего не защищает — защищает эта проверка."""
		ученик = создать_ученика(f"pupil-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(ученик)

		for вызов in (
			lambda: authoring.course_draft(course=self.курс),
			lambda: authoring.get_lesson(lesson=self.уроки[0]),
			lambda: authoring.add_lesson(chapter=self.глава, title="Свой урок", body="x"),
			lambda: authoring.publish_course(course=self.курс),
			lambda: authoring.set_directive(lesson=self.уроки[0], teaching_directive="x"),
			lambda: authoring.set_course_directive(course=self.курс, teaching_directive="x"),
			lambda: authoring.set_course_artifact(
				course=self.курс, artifact="summary", title="Резюме", blocks=[]
			),
		):
			with self.assertRaises(frappe.PermissionError):
				вызов()


class IntegrationTestAuthoringEdits(IntegrationTestCase):
	"""Правка собранного: без неё сборка через агента разваливается."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"editor-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Правка {суффикс}", summary="было")["data"]["id"]
		self.глава = authoring.add_chapter(course=self.курс, title="Было")["data"]["id"]
		self.урок = authoring.add_lesson(chapter=self.глава, title="Урок", body="# Урок")["data"]["id"]
		authoring.add_quiz(
			lesson=self.урок,
			questions=[{"text": "Первый?", "options": [{"text": "a", "correct": True}, {"text": "b"}]}],
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_курс_находится_по_списку(self):
		"""Без списка идентификатор курса взять негде — все методы требуют его."""
		курсы = authoring.list_courses()["data"]["courses"]

		наш = [курс for курс in курсы if курс["id"] == self.курс]
		self.assertTrue(наш, "созданный курс не виден в списке")
		self.assertFalse(наш[0]["published"])
		self.assertEqual(наш[0]["lessons_total"], 1)

	def test_название_главы_правится(self):
		authoring.update_chapter(chapter=self.глава, title="Стало")

		self.assertEqual(frappe.db.get_value("Course Chapter", self.глава, "title"), "Стало")

	def test_правится_только_анонс_без_уроков(self):
		ответ = authoring.update_course(course=self.курс, title="Стало")

		self.assertEqual(ответ["error"]["code"], "course_has_content")
		self.assertEqual(ответ["error"]["lessons"], 1)
		self.assertNotEqual(frappe.db.get_value("LMS Course", self.курс, "title"), "Стало")

		анонс = authoring.create_course(title="Анонс", summary="было")["data"]["id"]
		ответ = authoring.update_course(course=анонс, title="Стало", summary="и описание")
		self.assertEqual(ответ["data"]["title"], "Стало")
		self.assertEqual(frappe.db.get_value("LMS Course", анонс, "short_introduction"), "и описание")

	def test_второй_квиз_на_уроке_отклоняется(self):
		"""`Why:` урок отдаёт агенту ровно один квиз, второй становится
		невидимым мусором — а куратор считает, что заменил вопросы."""
		ответ = authoring.add_quiz(
			lesson=self.урок,
			questions=[{"text": "Другой?", "options": [{"text": "c", "correct": True}, {"text": "d"}]}],
		)

		self.assertEqual(ответ["error"]["code"], "quiz_exists")
		self.assertEqual(frappe.db.count("LMS Quiz", {"lesson": self.урок}), 1)

	def test_вопросы_квиза_добавляются_правятся_и_убираются(self):
		квиз = quiz._квиз_урока(self.урок)
		первый = frappe.get_all("LMS Quiz Question", filters={"parent": квиз}, pluck="question")[0]

		добавлен = authoring.add_question(
			lesson=self.урок,
			question={"text": "Второй?", "options": [{"text": "c", "correct": True}, {"text": "d"}]},
		)["data"]
		self.assertEqual(добавлен["questions_total"], 2)

		authoring.update_question(question=первый, text="Исправленный?")
		self.assertEqual(frappe.db.get_value("LMS Question", первый, "question"), "Исправленный?")

		убран = authoring.remove_question(lesson=self.урок, question=первый)["data"]
		self.assertEqual(убран["questions_total"], 1)
		# Сам вопрос остаётся: на него ссылаются ответы прошлых попыток.
		self.assertTrue(frappe.db.exists("LMS Question", первый))

	def test_варианты_заменяются_целиком(self):
		"""Правка «трёх вариантов на два» не должна оставлять третий."""
		вопрос = frappe.get_all(
			"LMS Quiz Question", filters={"parent": quiz._квиз_урока(self.урок)}, pluck="question"
		)[0]
		authoring.update_question(
			question=вопрос,
			options=[{"text": "1", "correct": True}, {"text": "2"}, {"text": "3"}],
		)

		authoring.update_question(
			question=вопрос, options=[{"text": "новый", "correct": True}, {"text": "другой"}]
		)

		документ = frappe.get_doc("LMS Question", вопрос)
		self.assertEqual(документ.option_1, "новый")
		self.assertIsNone(документ.option_3, "третий вариант остался от прошлой редакции")

	def test_неверная_правка_не_оставляет_вопрос_наполовину(self):
		вопрос = frappe.get_all(
			"LMS Quiz Question", filters={"parent": quiz._квиз_урока(self.урок)}, pluck="question"
		)[0]

		ответ = authoring.update_question(
			question=вопрос, text="Без верного", options=[{"text": "x"}, {"text": "y"}]
		)

		self.assertEqual(ответ["error"]["code"], "invalid_question")
		self.assertEqual(frappe.db.get_value("LMS Question", вопрос, "question"), "Первый?")


class IntegrationTestAuthoringStructure(IntegrationTestCase):
	"""Перенос и удаление: чинят ошибку структуры, но не историю ученика."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		# Удаление уроков Frappe Learning разрешает только `Moderator`.
		self.куратор = создать_куратора(f"mover-{суффикс}@example.com", роль="Moderator")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Структура {суффикс}", summary="к")["data"]["id"]
		self.первая = authoring.add_chapter(course=self.курс, title="Первая")["data"]["id"]
		self.вторая = authoring.add_chapter(course=self.курс, title="Вторая")["data"]["id"]
		self.уроки = [
			authoring.add_lesson(chapter=self.первая, title=б, body=f"# {б}")["data"]["id"]
			for б in "ABC"
		]

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_урок_переставляется_внутри_главы(self):
		authoring.move_lesson(lesson=self.уроки[2], position=1)

		self.assertEqual(уроки_главы(self.первая), [self.уроки[2], self.уроки[0], self.уроки[1]])

	def test_урок_переносится_в_другую_главу(self):
		authoring.move_lesson(lesson=self.уроки[0], chapter=self.вторая)

		self.assertEqual(уроки_главы(self.вторая), [self.уроки[0]])
		self.assertNotIn(self.уроки[0], уроки_главы(self.первая))
		# Поле курса переезжает вместе с уроком: иначе он останется числиться
		# в прежнем курсе, а показываться в новом.
		self.assertEqual(frappe.db.get_value("Course Lesson", self.уроки[0], "chapter"), self.вторая)

	def test_главы_переставляются_и_это_видно_frappe_learning(self):
		"""Порядок глав ученик и куратор обязаны видеть одинаковым."""
		from lms.lms.utils import get_chapters

		ответ = authoring.reorder_chapters(course=self.курс, chapters=[self.вторая, self.первая])

		self.assertEqual(ответ["data"]["chapters"], [self.вторая, self.первая])
		self.assertEqual(
			[глава["name"] for глава in structure.главы_курса(self.курс)],
			[self.вторая, self.первая],
		)
		self.assertEqual(
			[глава["name"] for глава in get_chapters(self.курс)], [self.вторая, self.первая]
		)

	def test_неполный_список_глав_отклоняется(self):
		"""Агент, забывший главу, иначе молча выкинул бы её из программы."""
		ответ = authoring.reorder_chapters(course=self.курс, chapters=[self.вторая])

		self.assertEqual(ответ["error"]["code"], "order_mismatch")
		self.assertEqual(
			[глава["name"] for глава in structure.главы_курса(self.курс)],
			[self.первая, self.вторая],
		)

	def test_порядок_глав_приходит_строкой_json(self):
		"""Frappe отдаёт тело запроса как форму: список приезжает строкой."""
		import json

		authoring.reorder_chapters(
			course=self.курс, chapters=json.dumps([self.вторая, self.первая])
		)

		self.assertEqual(
			[глава["name"] for глава in structure.главы_курса(self.курс)],
			[self.вторая, self.первая],
		)

	def test_лишний_урок_удаляется_целиком(self):
		authoring.add_quiz(
			lesson=self.уроки[1],
			questions=[{"text": "Вопрос?", "options": [{"text": "a", "correct": True}, {"text": "b"}]}],
		)
		квиз = quiz._квиз_урока(self.уроки[1])

		authoring.remove_lesson(lesson=self.уроки[1])

		self.assertNotIn(self.уроки[1], уроки_главы(self.первая))
		self.assertFalse(frappe.db.exists("Course Lesson", self.уроки[1]))
		self.assertFalse(frappe.db.exists("LMS Quiz", квиз), "квиз остался без урока")

	def test_урок_с_прогрессом_не_удаляется(self):
		"""`Why:` стирание урока, по которому занимались, испортило бы историю
		зачётов; курс от лишнего урока не рушится, а история — да."""
		ученик = создать_ученика(f"pupil-{frappe.generate_hash(length=6)}@example.com")
		frappe.get_doc(
			{
				"doctype": "LMS Course Progress",
				"lesson": self.уроки[0],
				"member": ученик,
				"course": self.курс,
				"status": "Complete",
			}
		).insert(ignore_permissions=True)

		ответ = authoring.remove_lesson(lesson=self.уроки[0])

		self.assertEqual(ответ["error"]["code"], "lesson_in_use")
		self.assertEqual(ответ["error"]["progress"], 1)
		self.assertTrue(frappe.db.exists("Course Lesson", self.уроки[0]))

	def test_непустая_глава_не_удаляется(self):
		ответ = authoring.remove_chapter(chapter=self.первая)

		self.assertEqual(ответ["error"]["code"], "chapter_not_empty")
		self.assertTrue(frappe.db.exists("Course Chapter", self.первая))

	def test_пустая_глава_удаляется(self):
		authoring.remove_chapter(chapter=self.вторая)

		self.assertFalse(frappe.db.exists("Course Chapter", self.вторая))
		self.assertNotIn(
			self.вторая, [глава["name"] for глава in structure.главы_курса(self.курс)]
		)


class IntegrationTestAuthoringReadBack(IntegrationTestCase):
	"""Обратное чтение: сверить, что на платформе лежит утверждённый текст."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"reader-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Чтение {суффикс}", summary="к")["data"]["id"]
		глава = authoring.add_chapter(course=self.курс, title="Глава")["data"]["id"]
		self.материал = "## Заголовок\n\nАбзац с примером.\n\n- пункт\n- ещё пункт\n"
		self.урок = authoring.add_lesson(
			chapter=глава, title="Урок", body=self.материал
		)["data"]["id"]

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_материал_возвращается_дословно(self):
		"""`Why:` иначе сверять собранное с исходником нечем — `course_draft`
		показывает только признак `has_body`."""
		урок = authoring.get_lesson(lesson=self.урок)["data"]

		self.assertEqual(урок["body"], self.материал)
		self.assertEqual(урок["title"], "Урок")

	def test_директива_приходит_действующей_версией(self):
		authoring.set_directive(lesson=self.урок, teaching_directive="Первая редакция")
		authoring.set_directive(
			lesson=self.урок, teaching_directive="Вторая редакция", objectives="Цель"
		)

		директива = authoring.get_lesson(lesson=self.урок)["data"]["directive"]

		self.assertEqual(директива["teaching_directive"], "Вторая редакция")
		self.assertEqual(директива["version"], 2)

	def test_у_действующей_директивы_есть_дата_версии(self):
		"""Кабинет автора показывает, когда версия поставлена (#261)."""
		from datetime import datetime

		authoring.set_directive(lesson=self.урок, teaching_directive="Веди")
		курс = frappe.db.get_value("Course Lesson", self.урок, "course")
		authoring.set_course_directive(course=курс, teaching_directive="Сквозная")

		урок = authoring.get_lesson(lesson=self.урок)["data"]

		for директива in (урок["directive"], урок["course_directive"]):
			self.assertIsInstance(datetime.fromisoformat(директива["created_at"]), datetime)

	def test_урок_без_директивы_не_ломает_чтение(self):
		урок = authoring.get_lesson(lesson=self.урок)["data"]

		self.assertIsNone(урок["directive"])
		self.assertIsNone(урок["quiz"])


class IntegrationTestCourseDirective(IntegrationTestCase):
	"""Сквозная директива курса: одна на курс, версионируется, видна автору."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"course-dir-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Сквозной {суффикс}", summary="есть")["data"]["id"]
		self.глава = authoring.add_chapter(course=self.курс, title="Глава")["data"]["id"]
		self.урок = authoring.add_lesson(chapter=self.глава, title="Урок", body="# Урок")["data"]["id"]

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_новая_редакция_вытесняет_прежнюю(self):
		"""Прежняя редакция остаётся в истории. `Why:` занятие, идущее сейчас,
		уже получило свою директиву."""
		authoring.set_course_directive(course=self.курс, teaching_directive="Первая редакция")
		вторая = authoring.set_course_directive(
			course=self.курс,
			teaching_directive="Вторая редакция",
			student_profile="Руководители малого бизнеса",
		)["data"]

		директива = authoring.course_draft(course=self.курс)["data"]["directive"]
		self.assertEqual(вторая["version"], 2)
		self.assertEqual(директива["teaching_directive"], "Вторая редакция")
		self.assertEqual(директива["student_profile"], "Руководители малого бизнеса")
		self.assertEqual(
			frappe.db.count("Agent Course Directive", {"course": self.курс, "is_active": 1}),
			1,
			"действующих директив курса должно оставаться ровно одна",
		)
		self.assertEqual(frappe.db.count("Agent Course Directive", {"course": self.курс}), 2)

	def test_директива_курса_хранит_что_запоминать(self):
		"""Автор задаёт, чему место в заметках агента об ученике."""
		authoring.set_course_directive(
			course=self.курс,
			teaching_directive="Веди спокойно",
			remember_about_student="роль и отрасль\nтекущий проект",
		)

		директива = authoring.course_draft(course=self.курс)["data"]["directive"]

		self.assertEqual(
			директива["remember_about_student"], "роль и отрасль\nтекущий проект"
		)

	def test_урок_показывает_директиву_курса(self):
		"""Автор правит урок, видя сказанное на уровне курса, и не дублирует."""
		authoring.set_course_directive(course=self.курс, teaching_directive="Сквозное правило")

		урок = authoring.get_lesson(lesson=self.урок)["data"]

		self.assertIn("Сквозное правило", урок["course_directive"]["teaching_directive"])

	def test_курс_без_директивы_предупреждает_но_не_блокирует(self):
		готовность = authoring.course_draft(course=self.курс)["data"]["readiness"]

		self.assertIn("course_without_directive", [п["code"] for п in готовность["warnings"]])
		self.assertNotIn(
			"course_without_directive", [п["code"] for п in готовность["blocking"]]
		)

	def test_директива_курса_не_путается_с_чужим_курсом(self):
		другой = authoring.create_course(title="Соседний", summary="есть")["data"]["id"]
		authoring.set_course_directive(course=self.курс, teaching_directive="Наше правило")

		self.assertIsNone(authoring.course_draft(course=другой)["data"]["directive"])


class IntegrationTestCourseArtifact(IntegrationTestCase):
	"""Схема документа курса: версионируется, видна автору, публикацию не держит."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"artifact-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Документы {суффикс}", summary="есть")["data"]["id"]
		глава = authoring.add_chapter(course=self.курс, title="Глава")["data"]["id"]
		self.урок = authoring.add_lesson(chapter=глава, title="Урок", body="# Урок")["data"]["id"]
		self.блоки = [
			{"key": "goal", "title": "Цель", "hint": "Одной фразой", "lesson": self.урок},
			{"key": "sponsor", "title": "Спонсор", "span": 2},
		]

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_схема_заводится_первой_версией(self):
		ответ = authoring.set_course_artifact(
			course=self.курс, artifact="Summary", title="Резюме проекта", blocks=self.блоки
		)["data"]

		self.assertEqual(ответ["version"], 1)
		self.assertEqual(ответ["artifact"], "summary", "ключ нормализуется и зовётся как у ученика")
		схема = frappe.get_doc("Agent Course Artifact", ответ["id"])
		self.assertEqual(схема.slug, "summary")
		self.assertEqual([(б.block_key, б.span) for б in схема.blocks], [("goal", 1), ("sponsor", 2)])
		self.assertEqual(схема.blocks[0].lesson, self.урок)

		# Повторный вызов снимает прежнюю с действия.
		вторая = authoring.set_course_artifact(
			course=self.курс, artifact="summary", title="Резюме проекта", blocks=self.блоки[:1]
		)["data"]

		self.assertEqual(вторая["version"], 2)
		self.assertEqual(
			frappe.db.count("Agent Course Artifact", {"course": self.курс, "slug": "summary", "is_active": 1}),
			1,
		)
		self.assertEqual(frappe.db.count("Agent Course Artifact", {"course": self.курс, "slug": "summary"}), 2)

	def test_зачем_документ_переживает_новые_версии(self):
		"""Не названный `purpose` остаётся у прежней версии, пустой — убирается (#462)."""
		зачем = "Проект на одной странице — чтобы все договорились об одном и том же."
		authoring.set_course_artifact(
			course=self.курс, artifact="summary", title="Резюме", blocks=self.блоки, purpose=f"  {зачем} "
		)
		authoring.set_course_artifact(course=self.курс, artifact="summary", title="Резюме", blocks=self.блоки)

		def действующий():
			return frappe.db.get_value(
				"Agent Course Artifact", {"course": self.курс, "slug": "summary", "is_active": 1}, "purpose"
			)

		self.assertEqual(действующий(), зачем, "вторая версия без purpose сохранила прежний")
		артефакты = authoring.course_draft(course=self.курс)["data"]["artifacts"]
		self.assertEqual(артефакты[0]["purpose"], зачем, "автор видит его в черновике")

		authoring.set_course_artifact(
			course=self.курс, artifact="summary", title="Резюме", blocks=self.блоки, purpose=""
		)
		self.assertIsNone(действующий())

	def test_черновик_показывает_документы_с_блоками(self):
		authoring.set_course_artifact(
			course=self.курс, artifact="summary", title="Резюме", blocks=self.блоки, layout="canvas"
		)

		артефакты = authoring.course_draft(course=self.курс)["data"]["artifacts"]

		self.assertEqual(len(артефакты), 1)
		self.assertEqual(артефакты[0]["artifact"], "summary")
		self.assertEqual(артефакты[0]["layout"], "canvas")
		self.assertEqual([б["key"] for б in артефакты[0]["blocks"]], ["goal", "sponsor"])
		self.assertEqual(артефакты[0]["blocks"][0]["hint"], "Одной фразой")

	def test_схема_без_блоков_предупреждает_но_не_блокирует(self):
		authoring.set_course_artifact(course=self.курс, artifact="summary", title="Резюме", blocks=[])

		готовность = authoring.course_draft(course=self.курс)["data"]["readiness"]

		коды = [п["code"] for п in готовность["warnings"]]
		self.assertIn("artifact_without_blocks", коды)
		self.assertNotIn("artifact_without_blocks", [п["code"] for п in готовность["blocking"]])

	def test_блок_с_несуществующим_уроком_отклоняется(self):
		ответ = authoring.set_course_artifact(
			course=self.курс,
			artifact="summary",
			title="Резюме",
			blocks=[{"key": "goal", "title": "Цель", "lesson": "нет-такого-урока"}],
		)

		self.assertEqual(ответ["error"]["code"], "lesson_not_found")
		self.assertFalse(frappe.db.exists("Agent Course Artifact", {"course": self.курс}))

	def test_блоки_приходят_строкой_json(self):
		"""Frappe отдаёт тело запроса как форму: список приезжает строкой."""
		import json

		ответ = authoring.set_course_artifact(
			course=self.курс, artifact="summary", title="Резюме", blocks=json.dumps(self.блоки)
		)["data"]

		self.assertEqual(len(frappe.get_doc("Agent Course Artifact", ответ["id"]).blocks), 2)


class IntegrationTestAuthorMirrorFields(IntegrationTestCase):
	"""Черновик показывает наполненность урока фактами, а не признаками.

	`Why:` зеркало автора (#261) и сам агент сверяют собранное по черновику;
	признак `has_body` не говорит ни сколько материала, ни на сколько частей
	его режет платформа, ни какая версия директивы действует.
	"""

	def setUp(self):
		from lms_frappe_app.tests.sample_data import политика_по_умолчанию

		self.addCleanup(политика_по_умолчанию)
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"mirror-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Зеркало {суффикс}", summary="к")["data"]["id"]
		глава = authoring.add_chapter(course=self.курс, title="Глава")["data"]["id"]
		self.материал = "## Заголовок\n\n" + "\n\n".join(["текст. " * 30] * 6)
		self.урок = authoring.add_lesson(chapter=глава, title="Урок", body=self.материал)["data"]["id"]

	def tearDown(self):
		frappe.set_user("Administrator")

	def урок_черновика(self) -> dict:
		черновик = authoring.course_draft(course=self.курс)["data"]
		return черновик["chapters"][0]["lessons"][0]

	def test_длина_и_сегменты_материала(self):
		from lms_frappe_app.agent_learning.normalizer import нормализовать_урок

		урок = self.урок_черновика()
		self.assertEqual(урок["body_chars"], len(self.материал.strip()))
		self.assertEqual(урок["body_segments"], 1)

		frappe.db.set_single_value("Agent Learning Settings", "lesson_segment_limit", 300)
		frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")

		урок = self.урок_черновика()
		self.assertGreater(урок["body_segments"], 1)
		self.assertEqual(урок["body_segments"], нормализовать_урок(self.урок).total_segments)

	def test_версия_директивы_и_число_целей(self):
		урок = self.урок_черновика()
		self.assertIsNone(урок["directive_version"])
		self.assertEqual(урок["objectives"], 0)

		authoring.set_directive(lesson=self.урок, teaching_directive="Первая")
		authoring.set_directive(lesson=self.урок, teaching_directive="Вторая", objectives="Цель один\nЦель два\n")

		урок = self.урок_черновика()
		self.assertEqual(урок["directive_version"], 2)
		self.assertEqual(урок["objectives"], 2)

	def test_порог_квиза_и_пояснения_вариантов(self):
		authoring.add_quiz(
			lesson=self.урок,
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

		квиз = self.урок_черновика()["quiz"]

		self.assertEqual(квиз["passing_percentage"], 60)
		self.assertEqual(
			[(в["text"], в["explanation"]) for в in квиз["questions"][0]["options"]],
			[("Верно", "Потому что."), ("Неверно", "")],
		)


class IntegrationTestCourseRevision(IntegrationTestCase):
	"""Отметка изменения курса: по ней зеркало автора узнаёт, что агент что-то
	поменял, не перечитывая курс целиком (#261)."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		# Удаление урока Frappe Learning разрешает только модератору.
		self.куратор = создать_куратора(f"revision-{суффикс}@example.com", роль="Moderator")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Ревизия {суффикс}", summary="к")["data"]["id"]
		self.глава = authoring.add_chapter(course=self.курс, title="Глава")["data"]["id"]
		self.урок = authoring.add_lesson(chapter=self.глава, title="Урок", body="# Текст")["data"]["id"]
		self.лишний = authoring.add_lesson(chapter=self.глава, title="Лишний", body="# Лишний")["data"]["id"]
		authoring.add_quiz(
			lesson=self.урок,
			questions=[{"text": "Первый?", "options": [{"text": "a", "correct": True}, {"text": "b"}]}],
		)
		self.вопрос = authoring.get_lesson(lesson=self.урок)["data"]["quiz"]["questions"][0]["id"]

	def tearDown(self):
		frappe.set_user("Administrator")

	def ревизия(self) -> str:
		return authoring.course_revision(course=self.курс)["data"]["revision"]

	def test_ревизия_растёт_от_каждой_правки_курса(self):
		правки = {
			"update_lesson": lambda: authoring.update_lesson(lesson=self.урок, body="# Новый текст"),
			"set_directive": lambda: authoring.set_directive(lesson=self.урок, teaching_directive="Веди"),
			"set_course_directive": lambda: authoring.set_course_directive(
				course=self.курс, teaching_directive="Сквозная"
			),
			"update_question": lambda: authoring.update_question(question=self.вопрос, text="Второй?"),
			"set_course_artifact": lambda: authoring.set_course_artifact(
				course=self.курс, artifact="summary", title="Резюме", blocks=[{"key": "goal", "title": "Цель"}]
			),
			"add_chapter": lambda: authoring.add_chapter(course=self.курс, title="Ещё глава"),
			"remove_lesson": lambda: authoring.remove_lesson(lesson=self.лишний),
		}
		for имя, правка in правки.items():
			with self.subTest(правка=имя):
				до = self.ревизия()
				правка()
				self.assertGreater(self.ревизия(), до)

	def test_чтение_ревизию_не_меняет(self):
		до = self.ревизия()
		authoring.course_draft(course=self.курс)
		authoring.get_lesson(lesson=self.урок)

		self.assertEqual(self.ревизия(), до)

	def test_черновик_отдаёт_ту_же_ревизию_и_ссылку_на_зеркало(self):
		черновик = authoring.course_draft(course=self.курс)["data"]

		self.assertEqual(черновик["revision"], self.ревизия())
		self.assertTrue(черновик["author_url"].endswith(f"/author?course={self.курс}"))

	def test_ученику_ревизия_недоступна(self):
		ученик = создать_ученика(f"revision-s-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(ученик)

		with self.assertRaises(frappe.PermissionError):
			authoring.course_revision(course=self.курс)

	def test_неизвестный_курс_даёт_код(self):
		ответ = authoring.course_revision(course="такого-курса-нет")

		self.assertEqual(ответ["error"]["code"], "course_not_found")


class IntegrationTestCourseMap(IntegrationTestCase):
	"""Карта декомпозиции на платформе: запись с версиями и сверка с курсом.

	`Why:` карта — замысел курса, и первоисточник её теперь платформа: вести
	её может любой автор, а сверку с собранным видят все авторы, а не только
	агент со скриптом по файлам (lms-high-time/learning-services#264).
	"""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"map-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Карта {суффикс}", summary="к")["data"]["id"]
		глава = authoring.add_chapter(course=self.курс, title="Рамка")["data"]["id"]
		self.урок = authoring.add_lesson(chapter=глава, title="Риск как событие", body="# Риск")["data"]["id"]
		authoring.set_directive(
			lesson=self.урок, teaching_directive="Веди", objectives="Риск это событие\nЛишняя цель"
		)
		authoring.set_course_artifact(
			course=self.курс,
			artifact="register",
			title="Реестр",
			blocks=[{"key": "risks", "title": "Риски", "hint": "Пять записей, у каждой причина", "lesson": self.урок}],
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	def данные(self, **правки) -> dict:
		return {
			"levels": [
				{"key": "result", "title": "Результат"},
				{"key": "thesis", "title": "Тезисы", "needs_lesson": True},
			],
			"nodes": [
				{"id": "R", "level": "result", "text": "Живой реестр"},
				{"id": "T1", "level": "thesis", "text": "Риск это событие", "parents": ["R"], "lesson": "u1", "objective": True},
			],
			"lessons": [{"key": "u1", "title": "Риск как событие", "chapter": "Рамка", "lesson": self.урок}],
			"blocks": [{"artifact": "register", "key": "risks", "lesson": "u1", "criteria": ["пять записей"]}],
			**правки,
		}

	def записать(self, **правки) -> dict:
		return authoring.set_course_map(course=self.курс, **self.данные(**правки))

	def test_карта_пишется_новой_версией(self):
		первая = self.записать()["data"]
		вторая = self.записать()["data"]

		self.assertEqual((первая["version"], вторая["version"]), (1, 2))
		self.assertEqual(frappe.db.count("Agent Course Map", {"course": self.курс, "is_active": 1}), 1)
		self.assertEqual(вторая["counts"]["total"], 1)

	def test_части_карты_приходят_строками_json(self):
		"""Frappe отдаёт тело формы строками — как у reorder_lessons."""
		данные = {ключ: json.dumps(значение, ensure_ascii=False) for ключ, значение in self.данные().items()}

		ответ = authoring.set_course_map(course=self.курс, **данные)

		self.assertTrue(ответ["ok"], ответ)

	def test_неверная_карта_отклоняется_и_не_пишется(self):
		ответ = self.записать(nodes=[{"id": "R", "level": "nope", "text": "Реестр"}])

		self.assertEqual((ответ["error"]["code"], ответ["error"]["where"]), ("invalid_map", "nodes[R].level"))
		self.assertFalse(frappe.db.exists("Agent Course Map", {"course": self.курс}))

	def test_привязка_к_уроку_другого_курса_отклоняется(self):
		другой = authoring.create_course(title=f"Другой {frappe.generate_hash(length=6)}", summary="к")["data"]["id"]
		глава = authoring.add_chapter(course=другой, title="Глава")["data"]["id"]
		чужой = authoring.add_lesson(chapter=глава, title="Чужой", body="# Чужой")["data"]["id"]

		ответ = self.записать(lessons=[{"key": "u1", "title": "Риск как событие", "lesson": чужой}])

		self.assertEqual((ответ["error"]["code"], ответ["error"]["where"]), ("invalid_map", "lessons[u1].lesson"))

	def test_без_карты_сверки_нет(self):
		сверка = authoring.course_map_check(course=self.курс)["data"]

		self.assertIsNone(сверка["map"])
		self.assertIsNone(сверка["counts"])
		self.assertEqual(сверка["discrepancies"], [])
		self.assertIsNone(authoring.course_draft(course=self.курс)["data"]["map_discrepancies"])

	def test_сверка_видит_курс_на_платформе(self):
		self.записать()

		сверка = authoring.course_map_check(course=self.курс)["data"]

		self.assertEqual(сверка["map"]["version"], 1)
		self.assertEqual(сверка["matches"], {"u1": self.урок})
		self.assertEqual(
			сверка["platform"]["lessons"],
			[
				{
					"id": self.урок,
					"number": 1,
					"title": "Риск как событие",
					"chapter": "Рамка",
					"objectives": ["Риск это событие", "Лишняя цель"],
				}
			],
		)
		self.assertEqual([(р["group"], р["code"], р.get("extra")) for р in сверка["discrepancies"]], [("objectives", "mismatch", ["Лишняя цель"])])
		self.assertEqual(authoring.course_draft(course=self.курс)["data"]["map_discrepancies"], 1)

	def test_правка_курса_видна_в_сверке_сразу(self):
		self.записать()
		authoring.set_directive(lesson=self.урок, teaching_directive="Веди", objectives="Риск это событие")

		self.assertEqual(authoring.course_map_check(course=self.курс)["data"]["discrepancies"], [])
		self.assertEqual(authoring.course_draft(course=self.курс)["data"]["map_discrepancies"], 0)

	def test_правка_карты_двигает_ревизию(self):
		до = authoring.course_revision(course=self.курс)["data"]["revision"]

		self.записать()

		self.assertGreater(authoring.course_revision(course=self.курс)["data"]["revision"], до)

	def test_ученику_карта_недоступна(self):
		self.записать()
		frappe.set_user(создать_ученика(f"map-s-{frappe.generate_hash(length=6)}@example.com"))

		with self.assertRaises(frappe.PermissionError):
			authoring.course_map_check(course=self.курс)
		with self.assertRaises(frappe.PermissionError):
			authoring.set_course_map(course=self.курс, **self.данные())


class IntegrationTestAuthorNotes(IntegrationTestCase):
	"""Замечания автора: место, цикл «сделано → принято», чей ход
	(lms-high-time/learning-services#266)."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"notes-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Замечания {суффикс}", summary="к")["data"]["id"]
		глава = authoring.add_chapter(course=self.курс, title="Рамка")["data"]["id"]
		self.урок = authoring.add_lesson(chapter=глава, title="Ответ и мера", body="# Мера")["data"]["id"]
		authoring.set_directive(lesson=self.урок, teaching_directive="Веди")
		authoring.add_quiz(
			lesson=self.урок,
			questions=[{"text": "Что здесь не так?", "options": [{"text": "a", "correct": True}, {"text": "b"}]}],
		)
		self.вопрос = authoring.get_lesson(lesson=self.урок)["data"]["quiz"]["questions"][0]["id"]

	def tearDown(self):
		frappe.set_user("Administrator")

	def замечание(self, target="directive.teaching_directive", **правки) -> dict:
		параметры = {"course": self.курс, "target": target, "text": "Слишком допрос", "lesson": self.урок, **правки}
		return authoring.add_note(**параметры)

	def очередь(self, **фильтры) -> list[dict]:
		return authoring.list_notes(course=self.курс, **фильтры)["data"]["notes"]

	def снимок_замечания(self, ид: str) -> dict | None:
		return snapshots.из_json(frappe.db.get_value("Agent Author Note", ид, "baseline"))

	def test_замечание_запоминает_место_каким_его_видел_человек(self):
		"""Снимок места — «как было» для разницы, когда агент отметит
		«сделано» (lms-high-time/learning-services#271)."""
		на_материал = self.замечание(target="material")["data"]["id"]
		к_курсу = self.замечание(target="course", lesson=None)["data"]["id"]

		self.assertEqual(self.снимок_замечания(на_материал), {"text": "# Мера", "mode": "text"})
		self.assertIsNone(self.снимок_замечания(к_курсу))

	def test_вернуть_запоминает_место_заново(self):
		"""Вернули — следующее «сделано» сравнивается с тем, что человек
		видел, когда возвращал, а не с исходным текстом."""
		ид = self.замечание(target="material")["data"]["id"]
		authoring.update_lesson(lesson=self.урок, body="# Мера\n\nПример меры.")
		authoring.set_note_status(note=ид, status="done", text="Добавил пример", via="agent")

		authoring.set_note_status(note=ид, status="open", text="Пример не про склад")

		self.assertEqual(self.снимок_замечания(ид)["text"], "# Мера\n\nПример меры.")

	def test_урок_с_замечаниями_удаляется_а_замечание_остаётся(self):
		"""Замечание — не содержание курса и удалению урока не мешает: место
		пропало, замечание осталось с пометкой «места больше нет»."""
		frappe.set_user(создать_куратора(f"notes-mod-{frappe.generate_hash(length=6)}@example.com", роль="Moderator"))
		ид = self.замечание(target="material")["data"]["id"]

		удаление = authoring.remove_lesson(lesson=self.урок)

		self.assertTrue(удаление["ok"], удаление)
		(з,) = [з for з in self.очередь() if з["id"] == ид]
		self.assertTrue(з["missing"])

	def test_снимок_не_уходит_наружу(self):
		"""Снимок — рабочий материал кабинета, не контракт методов."""
		self.замечание(target="material")

		(з,) = self.очередь()
		self.assertNotIn("baseline", з)

	def test_замечание_приходит_с_адресом_и_ходом_агента(self):
		ид = self.замечание(quote="По каждой мере три вопроса")["data"]["id"]

		(з,) = self.очередь()
		self.assertEqual(з["id"], ид)
		self.assertEqual((з["status"], з["via"], з["waiting_on"]), ("open", "author", "agent"))
		self.assertEqual(з["label"], "Урок 1 «Ответ и мера» · директива · teaching_directive")
		self.assertEqual(з["quote"], "По каждой мере три вопроса")
		self.assertFalse(з["missing"])
		self.assertEqual(з["replies"], [])

	def test_адреса_курса_вопроса_блока_и_карты(self):
		self.замечание(target="course", lesson=None)
		self.замечание(target=f"question.{self.вопрос}")
		self.замечание(target="block.Register/RISKS", lesson=None)
		self.замечание(target="map.T9", lesson=None)

		подписи = {з["target"]: (з["label"], з["missing"]) for з in self.очередь()}
		self.assertEqual(подписи["course"], ("Курс", False))
		self.assertEqual(подписи[f"question.{self.вопрос}"], ("Урок 1 «Ответ и мера» · вопрос «Что здесь не так?»", False))
		self.assertEqual(подписи["block.register/risks"], ("Документ register · блок «risks»", True))
		self.assertEqual(подписи["map.T9"], ("Карта · узел T9", True))

	def test_неверное_место_отклоняется(self):
		другой = authoring.create_course(title=f"Другой {frappe.generate_hash(length=6)}", summary="к")["data"]["id"]
		глава = authoring.add_chapter(course=другой, title="Глава")["data"]["id"]
		чужой = authoring.add_lesson(chapter=глава, title="Чужой", body="# Чужой")["data"]["id"]

		for правки, место in (
			({"lesson": чужой}, "lesson"),
			({"target": "question.QTS-нет-такого"}, "target"),
			({"target": "directive.nope"}, "target"),
			({"target": "material", "lesson": None}, "lesson"),
		):
			with self.subTest(правки=правки):
				ответ = self.замечание(**правки)
				self.assertEqual((ответ["error"]["code"], ответ["error"]["where"]), ("invalid_target", место))
		self.assertEqual(self.очередь(), [])

	def test_пустое_замечание_отклоняется(self):
		ответ = self.замечание(text="  ")

		self.assertEqual((ответ["error"]["code"], ответ["error"]["where"]), ("invalid_note", "text"))

	def test_цикл_сделано_принято_и_возврат(self):
		ид = self.замечание()["data"]["id"]

		сделано = authoring.set_note_status(note=ид, status="done", text="Добавил пример меры", via="agent")["data"]
		self.assertEqual((сделано["status"], сделано["waiting_on"]), ("done", "author"))

		возврат = authoring.set_note_status(note=ид, status="open", text="Пример всё ещё про кафе")["data"]
		self.assertEqual((возврат["status"], возврат["waiting_on"]), ("open", "agent"))

		authoring.set_note_status(note=ид, status="done", text="Заменил на склад", via="agent")
		принято = authoring.set_note_status(note=ид, status="accepted")["data"]
		self.assertEqual((принято["status"], принято["waiting_on"]), ("accepted", None))

		(з,) = self.очередь()
		self.assertEqual(
			[(о["via"], о["text"]) for о in з["replies"]],
			[("agent", "Добавил пример меры"), ("author", "Пример всё ещё про кафе"), ("agent", "Заменил на склад")],
		)

	def test_агент_не_принимает_автор_не_отмечает_сделанным(self):
		ид = self.замечание()["data"]["id"]

		for параметры in (
			{"status": "accepted", "via": "agent"},
			{"status": "done", "text": "Сам поправил"},
			{"status": "done", "via": "agent"},
		):
			with self.subTest(**параметры):
				ответ = authoring.set_note_status(note=ид, **параметры)
				self.assertEqual(ответ["error"]["code"], "invalid_transition")

	def test_вопрос_агента_ждёт_автора_а_ответ_возвращает_ход(self):
		ид = self.замечание(via="agent", text="Какой пример меры взять для кафе?")["data"]["id"]
		self.assertEqual(self.очередь()[0]["waiting_on"], "author")

		ответ = authoring.reply_note(note=ид, text="Возьми склад")["data"]

		self.assertEqual((ответ["status"], ответ["waiting_on"]), ("open", "agent"))

	def test_черновик_считает_замечания_которые_ждут_агента(self):
		первое = self.замечание()["data"]["id"]
		self.замечание(target="material")
		self.замечание(via="agent", text="Вопрос автору")
		authoring.set_note_status(note=первое, status="done", text="Сделал", via="agent")

		self.assertEqual(authoring.course_draft(course=self.курс)["data"]["open_notes"], 1)

	def test_замечания_двигают_свою_метку_а_не_метку_курса(self):
		до = authoring.course_revision(course=self.курс)["data"]
		self.assertIsNone(до["notes_revision"])

		ид = self.замечание()["data"]["id"]
		после_записи = authoring.course_revision(course=self.курс)["data"]
		authoring.reply_note(note=ид, text="Уточню: шаг 3")
		после_ответа = authoring.course_revision(course=self.курс)["data"]

		self.assertEqual(после_записи["revision"], до["revision"])
		self.assertIsNotNone(после_записи["notes_revision"])
		self.assertGreater(после_ответа["notes_revision"], после_записи["notes_revision"])

	def test_фильтр_по_статусу_и_уроку(self):
		ид = self.замечание()["data"]["id"]
		self.замечание(target="course", lesson=None)
		authoring.set_note_status(note=ид, status="accepted")

		self.assertEqual([з["target"] for з in self.очередь(status="open")], ["course"])
		self.assertEqual([з["id"] for з in self.очередь(lesson=self.урок)], [ид])

	def test_ученику_замечания_недоступны(self):
		ид = self.замечание()["data"]["id"]
		frappe.set_user(создать_ученика(f"notes-s-{frappe.generate_hash(length=6)}@example.com"))

		with self.assertRaises(frappe.PermissionError):
			authoring.list_notes(course=self.курс)
		with self.assertRaises(frappe.PermissionError):
			authoring.reply_note(note=ид, text="Я ученик")


class IntegrationTestLessonHookAndPromise(IntegrationTestCase):
	"""Зачин урока и обещание курса (#238).

	Адресат обоих — ученик, а директива адресована агенту; поэтому это поля
	`Course Lesson` и `LMS Course`, а не поля директив (решение владельца 1Б).
	"""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"curator-{суффикс}@example.com")
		frappe.set_user(self.куратор)
		self.курс = authoring.create_course(title=f"Курс {суффикс}", summary="Собран агентом")["data"]["id"]
		глава = authoring.add_chapter(course=self.курс, title="Глава")["data"]["id"]
		self.урок = authoring.add_lesson(chapter=глава, title="Урок", body="# Урок")["data"]["id"]

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_у_урока_и_курса_есть_поля_зачина_и_обещания(self):
		self.assertTrue(frappe.get_meta("Course Lesson").has_field("lesson_hook"))
		self.assertTrue(frappe.get_meta("LMS Course").has_field("course_promise"))

	def test_зачин_урока_задаётся_и_очищается(self):
		ответ = authoring.update_lesson(lesson=self.урок, lesson_hook="Зачем тема сейчас")
		self.assertEqual(ответ["data"]["lesson_hook"], "Зачем тема сейчас")
		self.assertEqual(frappe.db.get_value("Course Lesson", self.урок, "lesson_hook"), "Зачем тема сейчас")

		authoring.update_lesson(lesson=self.урок, title="Другое название")
		self.assertEqual(
			frappe.db.get_value("Course Lesson", self.урок, "lesson_hook"),
			"Зачем тема сейчас",
			"параметр не передан — поле не трогается",
		)

		authoring.update_lesson(lesson=self.урок, lesson_hook="")
		self.assertFalse(frappe.db.get_value("Course Lesson", self.урок, "lesson_hook"))

	def test_обещание_курса_задаётся_и_очищается(self):
		анонс = authoring.create_course(title="Анонс", summary="Без уроков")["data"]["id"]
		ответ = authoring.update_course(course=анонс, promise="Уйдёте с готовым канвасом")
		self.assertEqual(ответ["data"]["promise"], "Уйдёте с готовым канвасом")
		self.assertEqual(
			frappe.db.get_value("LMS Course", анонс, "course_promise"), "Уйдёте с готовым канвасом"
		)

		authoring.update_course(course=анонс, promise="")
		self.assertFalse(frappe.db.get_value("LMS Course", анонс, "course_promise"))


class IntegrationTestReadinessHookAndPromise(IntegrationTestCase):
	"""Пустые зачин и обещание — предупреждение, а не отказ: иначе три
	опубликованных курса разом перестали бы публиковаться (#238, пункт 8)."""

	# Тот же курс, что и выше, без наследования: наследник прогнал бы тесты
	# родителя второй раз.
	setUp = IntegrationTestLessonHookAndPromise.setUp
	tearDown = IntegrationTestLessonHookAndPromise.tearDown

	def готовность(self) -> dict:
		from lms_frappe_app.agent_learning import course_builder

		return course_builder.проверить_готовность(self.курс)

	def test_без_обещания_и_зачина_курс_публикуется_с_предупреждением(self):
		готовность = self.готовность()
		коды = [(п["code"], п.get("lesson")) for п in готовность["warnings"]]

		self.assertIn(("course_without_promise", None), коды)
		self.assertIn(("lesson_without_hook", self.урок), коды)
		self.assertNotIn("course_without_promise", [п["code"] for п in готовность["blocking"]])
		self.assertNotIn("lesson_without_hook", [п["code"] for п in готовность["blocking"]])

	def test_с_обещанием_и_зачином_предупреждений_нет(self):
		frappe.db.set_value("LMS Course", self.курс, "course_promise", "Уйдёте с канвасом")
		authoring.update_lesson(lesson=self.урок, lesson_hook="Зачем это сейчас")

		коды = [п["code"] for п in self.готовность()["warnings"]]

		self.assertNotIn("course_without_promise", коды)
		self.assertNotIn("lesson_without_hook", коды)
