# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Проекция релиза в главы и уроки Learning (learning-services#500) и уборка
снятого из релиза (learning-services#514)."""

import frappe
from frappe.tests import IntegrationTestCase
from lms.lms.utils import get_chapters, get_lessons

from lms_frappe_app.agent_learning import homework, release_quiz, structure
from lms_frappe_app.agent_learning.constants import АННУЛИРОВАНА_УРОК_СНЯТ, ПОПЫТКА_АННУЛИРОВАНА
from lms_frappe_app.agent_learning.doctype.agent_course_release.test_agent_course_release import (
	вставить_релиз,
)
from lms_frappe_app.agent_learning.errors import КУРС_ИЗ_РЕЛИЗА, Отказ
from lms_frappe_app.agent_learning.releases import projection, service
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.tests.release_sample import добавить_главу, пример_релиза
from lms_frappe_app.tests.sample_data import (
	занятие_релиза,
	зачислить_на_курс,
	как_из_релиза,
	создать_занятие,
	создать_куратора,
	создать_ученика,
	урок_релиза,
)

ПУСТО = {"chapters": {}, "lessons": {}}


def курс_куратора(куратор: str) -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "LMS Course",
				"title": f"Релизный курс {frappe.generate_hash(length=6)}",
				"short_introduction": "Курс для тестов",
				"description": "Курс для тестов",
				"published": 0,
				"instructors": [{"instructor": куратор}],
			}
		)
		.insert()
		.name
	)


def прежние(итог) -> dict:
	return {"chapters": list(итог.главы), "lessons": list(итог.уроки)}


class IntegrationTestПроекция(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.куратор = создать_куратора(f"rel-proj-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(self.куратор)
		self.курс = курс_куратора(self.куратор)

	def уроки(self) -> list[str]:
		return [урок["name"] for урок in get_lessons(self.курс)]

	def test_первая_проекция_создаёт_главы_и_уроки_в_порядке(self):
		итог = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})

		главы = get_chapters(self.курс)
		self.assertEqual([г["name"] for г in главы], [итог.главы["ch-1"], итог.главы["ch-2"]])
		self.assertEqual(self.уроки(), [итог.уроки[к] for к in ("l-1", "l-2", "l-3")])
		урок = frappe.get_doc("Course Lesson", итог.уроки["l-1"])
		self.assertEqual(урок.lesson_hook, "Зачин урока «Урок первый»")
		self.assertFalse(урок.body)
		self.assertEqual(урок.course, self.курс)
		self.assertEqual(
			frappe.db.get_value("Course Chapter", итог.главы["ch-1"], "chapter_description"),
			"Что изменится после первой главы.",
		)
		self.assertEqual(итог.создано, {"chapters": ["ch-1", "ch-2"], "lessons": ["l-1", "l-2", "l-3"]})
		self.assertEqual(projection.известные(self.курс), {"chapters": итог.главы, "lessons": итог.уроки})

	def test_ключ_пишется_при_создании_и_не_меняется_при_правке(self):
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		self.assertEqual(frappe.db.get_value("Course Lesson", первый.уроки["l-2"], "lesson_key"), "l-2")
		self.assertEqual(frappe.db.get_value("Course Chapter", первый.главы["ch-2"], "chapter_key"), "ch-2")
		релиз = пример_релиза()
		релиз["lessons"][1].update(chapter="ch-2", title="Урок второй, перенесённый")
		релиз["chapters"][0]["lessons"] = ["l-1"]
		релиз["chapters"][1].update(title="Глава вторая, исправленная", lessons=["l-2", "l-3"])

		итог = projection.спроецировать(self.курс, релиз, projection.известные(self.курс), прежние(первый))

		self.assertEqual(итог.обновлено, {"chapters": ["ch-2"], "lessons": ["l-2"]})
		self.assertEqual(
			frappe.db.get_value("Course Lesson", первый.уроки["l-2"], ["lesson_key", "title"]),
			("l-2", "Урок второй, перенесённый"),
		)
		self.assertEqual(frappe.db.get_value("Course Chapter", первый.главы["ch-2"], "chapter_key"), "ch-2")
		self.assertEqual(projection.известные(self.курс), {"chapters": первый.главы, "lessons": первый.уроки})

	def test_известные_только_записи_курса_с_ключом(self):
		"""Глава и урок курса без ключа — не из релиза: им ключ не сопоставляется."""
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		глава = frappe.get_doc(
			{"doctype": "Course Chapter", "title": "Своя глава", "course": self.курс}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{"doctype": "Course Lesson", "title": "Свой урок", "course": self.курс, "chapter": глава.name}
		).insert(ignore_permissions=True)
		другой = курс_куратора(self.куратор)
		projection.спроецировать(другой, пример_релиза(), ПУСТО, {})

		self.assertEqual(projection.известные(self.курс), {"chapters": первый.главы, "lessons": первый.уроки})

	def test_правка_названия_меняет_ту_же_запись(self):
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		релиз = пример_релиза()
		релиз["lessons"][0]["title"] = "Урок первый, исправленный"

		итог = projection.спроецировать(self.курс, релиз, projection.известные(self.курс), прежние(первый))

		self.assertEqual(итог.уроки["l-1"], первый.уроки["l-1"])
		self.assertEqual(
			frappe.db.get_value("Course Lesson", итог.уроки["l-1"], "title"), "Урок первый, исправленный"
		)
		self.assertEqual(итог.обновлено["lessons"], ["l-1"])
		self.assertEqual(итог.создано, {"chapters": [], "lessons": []})

	def test_перенос_урока_в_другую_главу(self):
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		релиз = пример_релиза()
		релиз["lessons"][1]["chapter"] = "ch-2"
		релиз["chapters"][0]["lessons"] = ["l-1"]
		релиз["chapters"][1]["lessons"] = ["l-2", "l-3"]

		итог = projection.спроецировать(self.курс, релиз, projection.известные(self.курс), прежние(первый))

		self.assertEqual(итог.уроки["l-2"], первый.уроки["l-2"])
		self.assertEqual(
			frappe.db.get_value("Course Lesson", итог.уроки["l-2"], "chapter"), итог.главы["ch-2"]
		)
		self.assertEqual(self.уроки(), [итог.уроки[к] for к in ("l-1", "l-2", "l-3")])
		self.assertEqual(
			[урок["name"] for урок in get_lessons(self.курс, frappe._dict(name=итог.главы["ch-2"]))],
			[итог.уроки["l-2"], итог.уроки["l-3"]],
		)

	def test_снятый_урок_уходит_из_порядка_и_остаётся(self):
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		релиз = пример_релиза()
		релиз["chapters"] = релиз["chapters"][:1]
		релиз["lessons"] = релиз["lessons"][:2]

		итог = projection.спроецировать(self.курс, релиз, projection.известные(self.курс), прежние(первый))

		self.assertEqual(итог.снято, {"chapters": ["ch-2"], "lessons": ["l-3"]})
		self.assertTrue(frappe.db.exists("Course Lesson", первый.уроки["l-3"]))
		self.assertNotIn(первый.уроки["l-3"], self.уроки())
		self.assertEqual([г["name"] for г in get_chapters(self.курс)], [первый.главы["ch-1"]])
		self.assertFalse(frappe.get_all("Lesson Reference", filters={"parent": первый.главы["ch-2"]}))

	def test_вернувшийся_ключ_получает_ту_же_запись(self):
		"""Ключ снятой записи остаётся на ней: вернувшийся ключ находит её без
		истории релизов — релизов в этом тесте нет вовсе."""
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		релиз = пример_релиза()
		релиз["chapters"] = релиз["chapters"][:1]
		релиз["lessons"] = релиз["lessons"][:2]
		второй = projection.спроецировать(self.курс, релиз, projection.известные(self.курс), прежние(первый))
		self.assertEqual(projection.известные(self.курс)["lessons"]["l-3"], первый.уроки["l-3"])
		self.assertEqual(projection.известные(self.курс)["chapters"]["ch-2"], первый.главы["ch-2"])
		self.assertFalse(frappe.db.exists("Agent Course Release", {"course": self.курс}))

		итог = projection.спроецировать(
			self.курс, пример_релиза(), projection.известные(self.курс), прежние(второй)
		)

		self.assertEqual(итог.уроки["l-3"], первый.уроки["l-3"])
		self.assertEqual(итог.главы["ch-2"], первый.главы["ch-2"])
		self.assertEqual(итог.создано, {"chapters": [], "lessons": []})
		self.assertEqual(итог.возвращено, {"chapters": ["ch-2"], "lessons": ["l-3"]})
		self.assertEqual(self.уроки(), [первый.уроки[к] for к in ("l-1", "l-2", "l-3")])

	def test_без_изменений_ничего_не_сохраняет(self):
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		было = {имя: frappe.db.get_value("Course Lesson", имя, "modified") for имя in первый.уроки.values()}
		курс_был = frappe.db.get_value("LMS Course", self.курс, "modified")

		итог = projection.спроецировать(
			self.курс, пример_релиза(), projection.известные(self.курс), прежние(первый)
		)

		пусто = {"chapters": [], "lessons": []}
		self.assertEqual((итог.создано, итог.обновлено, итог.снято), (пусто, пусто, пусто))
		self.assertEqual(
			{имя: frappe.db.get_value("Course Lesson", имя, "modified") for имя in первый.уроки.values()},
			было,
		)
		self.assertEqual(frappe.db.get_value("LMS Course", self.курс, "modified"), курс_был)

	def test_запись_чужого_курса_не_берётся(self):
		"""Те же ключи в другом курсе — свои записи: ключ ищется среди записей курса."""
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		другой = курс_куратора(self.куратор)

		итог = projection.спроецировать(другой, пример_релиза(), projection.известные(другой), {})

		self.assertNotEqual(итог.уроки["l-1"], первый.уроки["l-1"])
		self.assertEqual(итог.создано["lessons"], ["l-1", "l-2", "l-3"])
		self.assertEqual(frappe.db.get_value("Course Lesson", итог.уроки["l-1"], "lesson_key"), "l-1")
		self.assertEqual(projection.известные(self.курс)["lessons"], первый.уроки)

	def test_снятый_урок_не_возвращается_в_программу(self):
		"""Курс с действующим релизом: состав — только строки-ссылки, без запасного пути."""
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		frappe.db.set_value("LMS Course", self.курс, "active_release", вставить_релиз(self.курс))
		релиз = пример_релиза()
		релиз["chapters"] = релиз["chapters"][:1]
		релиз["lessons"] = релиз["lessons"][:2]

		projection.спроецировать(self.курс, релиз, projection.известные(self.курс), прежние(первый))

		self.assertEqual(structure.уроки_курса(self.курс), [первый.уроки["l-1"], первый.уроки["l-2"]])
		self.assertEqual(structure.уроков_в_курсах([self.курс]), {self.курс: 2})
		self.assertEqual([г["name"] for г in structure.главы_курса(self.курс)], [первый.главы["ch-1"]])
		self.assertEqual(structure.уроки_главы(первый.главы["ch-2"]), [])

	def test_курс_без_релиза_держит_запасной_путь(self):
		"""Урок без строки-ссылки у курса без релиза по-прежнему в программе — в конце главы."""
		первый = projection.спроецировать(self.курс, пример_релиза(), ПУСТО, {})
		frappe.db.delete("Lesson Reference", {"parent": первый.главы["ch-1"], "lesson": первый.уроки["l-1"]})

		self.assertEqual(
			structure.уроки_главы(первый.главы["ch-1"]), [первый.уроки["l-2"], первый.уроки["l-1"]]
		)
		self.assertEqual(structure.уроков_в_курсах([self.курс]), {self.курс: 3})


ДОМАШКА = {"title": "Задание", "description": "Сделайте пример.", "answer_mode": "text", "due_days": None}
#: Уроки главы `ch-3`: у каждого — свой след ученика или автора, у последнего — никакого.
СЛЕДЫ = ("l-session", "l-run", "l-attempt", "l-progress", "l-current", "l-block", "l-homework", "l-free")


class IntegrationTestУборкаСнятого(IntegrationTestCase):
	"""Публикация удаляет снятые ею главы и уроки без ссылок, а со ссылками оставляет."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"rel-prune-{суффикс}@example.com")
		self.ученик = создать_ученика(f"rel-prune-pupil-{суффикс}@example.com")
		self.ключ = f"prune-{суффикс}"

	def опубликовать(self, релиз: dict) -> dict:
		return service.опубликовать(релиз, None, self.куратор)

	def без(self, релиз: dict, *ключи: str) -> dict:
		"""Релиз без уроков `ключи`; глава, оставшаяся без уроков, уходит тоже."""
		релиз["lessons"] = [у for у in релиз["lessons"] if у["key"] not in ключи]
		for глава in релиз["chapters"]:
			глава["lessons"] = [у for у in глава["lessons"] if у not in ключи]
		релиз["chapters"] = [г for г in релиз["chapters"] if г["lessons"]]
		for ключ in ключи:
			del релиз["agent"]["lessons"][ключ]
		return релиз

	def есть(self, doctype: str, имя: str) -> bool:
		return bool(frappe.db.exists(doctype, имя))

	def test_снятые_без_ссылок_удаляются_глава_после_уроков(self):
		первый = self.опубликовать(пример_релиза(self.ключ))
		урок, глава = (
			урок_релиза(первый["course"], "l-3"),
			frappe.db.get_value("Course Lesson", урок_релиза(первый["course"], "l-3"), "chapter"),
		)

		ответ = self.опубликовать(self.без(пример_релиза(self.ключ), "l-3"))

		self.assertEqual((ответ["lessons"]["removed"], ответ["chapters"]["removed"]), (["l-3"], ["ch-2"]))
		self.assertFalse(self.есть("Course Lesson", урок))
		self.assertFalse(self.есть("Course Chapter", глава))
		self.assertFalse(frappe.db.exists("Lesson Reference", {"parent": глава}))
		self.assertFalse(frappe.db.exists("Chapter Reference", {"chapter": глава}))
		self.assertFalse(
			frappe.db.exists("Deleted Document", {"deleted_doctype": "Course Lesson", "deleted_name": урок})
		)
		self.assertEqual(frappe.db.count("Course Lesson", {"course": первый["course"]}), 2)

	def test_снятые_со_ссылками_остаются_и_ссылки_целы(self):
		"""У каждого урока главы `ch-3` — свой след; попытка квиза и сдача домашки
		идут через занятие, остальные следы — единственные у своего урока."""
		релиз = добавить_главу(пример_релиза(self.ключ), "ch-3", list(СЛЕДЫ))
		релиз["lessons"][-2]["homework"] = ДОМАШКА
		курс = self.опубликовать(релиз)["course"]
		уроки = {ключ: урок_релиза(курс, ключ) for ключ in СЛЕДЫ}
		глава = frappe.db.get_value("Course Lesson", уроки["l-free"], "chapter")
		зачислить_на_курс(self.ученик, курс)
		запись = frappe.db.get_value("LMS Enrollment", {"member": self.ученик, "course": курс})

		занятие = создать_занятие(self.ученик, уроки["l-session"])
		прохождение = прохождения.прохождение(self.ученик, курс, "l-run").name
		попытка = release_quiz.начать(
			прохождения.прохождение(self.ученик, курс, "l-attempt"),
			занятие_релиза(self.ученик, курс, "l-attempt"),
		)["attempt"]
		прогресс = (
			frappe.get_doc(
				{
					"doctype": "LMS Course Progress",
					"member": self.ученик,
					"lesson": уроки["l-progress"],
					"status": "Complete",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.set_value("LMS Enrollment", запись, "current_lesson", уроки["l-current"])
		схема = (
			как_из_релиза(
				frappe.get_doc(
					{
						"doctype": "Agent Course Artifact",
						"course": курс,
						"slug": "notebook",
						"title": "Тетрадь другой версии",
						"is_active": 0,
						"blocks": [{"block_key": "log", "title": "Журнал", "lesson": уроки["l-block"]}],
					}
				)
			)
			.insert(ignore_permissions=True)
			.name
		)
		homework.выдать(
			frappe.get_doc("Agent Learning Session", создать_занятие(self.ученик, уроки["l-homework"]))
		)
		сдача = frappe.db.get_value(homework.СДАЧА, {"lesson": уроки["l-homework"]})
		self.assertTrue(сдача)

		ответ = self.опубликовать(self.без(релиз, *СЛЕДЫ))

		self.assertEqual(ответ["lessons"]["removed"], list(СЛЕДЫ))
		self.assertFalse(self.есть("Course Lesson", уроки["l-free"]))
		оставлены = [ключ for ключ in СЛЕДЫ if self.есть("Course Lesson", уроки[ключ])]
		self.assertEqual(оставлены, list(СЛЕДЫ[:-1]))
		self.assertTrue(self.есть("Course Chapter", глава))
		self.assertEqual(
			projection.вне_релиза(курс), {"chapters": [глава], "lessons": [уроки[к] for к in оставлены]}
		)
		self.assertNotIn(глава, [г["name"] for г in get_chapters(курс)])
		self.assertEqual(
			{урок["name"] for урок in get_lessons(курс)},
			{урок_релиза(курс, к) for к in ("l-1", "l-2", "l-3")},
		)
		self.assertEqual(frappe.db.get_value("Agent Learning Session", занятие, "lesson"), уроки["l-session"])
		self.assertEqual(frappe.db.get_value("Agent Lesson Run", прохождение, "lesson"), уроки["l-run"])
		self.assertEqual(
			frappe.db.get_value("Agent Quiz Attempt", попытка, ["status", "cancel_reason", "lesson"]),
			(ПОПЫТКА_АННУЛИРОВАНА, АННУЛИРОВАНА_УРОК_СНЯТ, уроки["l-attempt"]),
		)
		self.assertEqual(frappe.db.get_value("LMS Course Progress", прогресс, "lesson"), уроки["l-progress"])
		self.assertEqual(frappe.db.get_value("LMS Enrollment", запись, "current_lesson"), уроки["l-current"])
		self.assertEqual(
			frappe.db.get_value("Agent Artifact Block", {"parent": схема}, "lesson"), уроки["l-block"]
		)
		self.assertEqual(frappe.db.get_value(homework.СДАЧА, сдача, "lesson"), уроки["l-homework"])
		self.assertEqual(frappe.db.get_value(homework.ЗАДАНИЕ, {"lesson": уроки["l-homework"]}, "retired"), 1)

	def test_вернувшийся_ключ_удалённой_записи_получает_новую(self):
		первый = self.опубликовать(пример_релиза(self.ключ))
		прежний = урок_релиза(первый["course"], "l-3")
		self.опубликовать(self.без(пример_релиза(self.ключ), "l-3"))

		ответ = self.опубликовать(пример_релиза(self.ключ))

		новый = урок_релиза(первый["course"], "l-3")
		self.assertNotEqual(новый, прежний)
		self.assertFalse(self.есть("Course Lesson", прежний))
		self.assertEqual(frappe.db.get_value("Course Lesson", новый, "lesson_key"), "l-3")
		self.assertEqual((ответ["lessons"]["created"], ответ["lessons"]["restored"]), (["l-3"], []))
		self.assertEqual((ответ["chapters"]["created"], ответ["chapters"]["restored"]), (["ch-2"], []))

	def test_desk_не_удаляет_урок_и_главу_курса_из_релиза(self):
		"""Уборка удаляет мимо хука `course_guard`; Desk — и оставленную запись
		вне оглавления — по-прежнему через него."""
		курс = self.опубликовать(пример_релиза(self.ключ))["course"]
		урок = урок_релиза(курс, "l-3")
		глава = frappe.db.get_value("Course Lesson", урок, "chapter")
		создать_занятие(self.ученик, урок)
		self.опубликовать(self.без(пример_релиза(self.ключ), "l-3"))

		for doctype, имя in (
			("Course Lesson", урок),
			("Course Chapter", глава),
			("Course Lesson", урок_релиза(курс, "l-1")),
		):
			with self.subTest(doctype=doctype, имя=имя):
				with self.assertRaises(Отказ) as пойман:
					frappe.delete_doc(doctype, имя)
				self.assertEqual(пойман.exception.код, КУРС_ИЗ_РЕЛИЗА)
				self.assertTrue(self.есть(doctype, имя))

	def test_запись_в_оглавлении_не_удаляется(self):
		"""Строки оглавления — ссылки дочерних таблиц: урок в главе и глава в курсе держатся ими."""
		курс = self.опубликовать(пример_релиза(self.ключ))["course"]
		урок = урок_релиза(курс, "l-1")
		глава = frappe.db.get_value("Course Lesson", урок, "chapter")

		уборка = projection.убрать({"chapters": [глава], "lessons": [урок]})

		self.assertEqual(уборка.оставлено, {"chapters": [глава], "lessons": [урок]})
		self.assertEqual(уборка.удалено, {"chapters": [], "lessons": []})
		# Строка дочерней таблицы держит запись от имени родителя.
		self.assertLessEqual(
			{"Course Chapter": 1, "Agent Course Release": 1}.items(), уборка.держат[урок].items()
		)
		self.assertLessEqual({"LMS Course": 1, "Course Lesson": 2}.items(), уборка.держат[глава].items())

	def test_глава_удаляется_со_строками_уроков_мимо_их_хука(self):
		"""Строки `Lesson Reference` удаляемой главы уходят запросом: хук `on_trash`
		строк (`course_guard.проверить_ссылку`) отказал бы — курс из релиза.
		Строку оставленного урока в снятой главе здесь заводит тест: проекция
		таких не оставляет."""
		курс = self.опубликовать(пример_релиза(self.ключ))["course"]
		урок = урок_релиза(курс, "l-3")
		глава = frappe.db.get_value("Course Lesson", урок, "chapter")
		создать_занятие(self.ученик, урок)
		self.опубликовать(self.без(пример_релиза(self.ключ), "l-3"))
		frappe.db.set_value("Course Lesson", урок, "chapter", None)
		frappe.get_doc(
			{
				"doctype": "Lesson Reference",
				"parent": глава,
				"parenttype": "Course Chapter",
				"parentfield": "lessons",
				"lesson": урок,
			}
		).db_insert()

		уборка = projection.убрать({"chapters": [глава], "lessons": [урок]})

		self.assertEqual(уборка.удалено, {"chapters": [глава], "lessons": []})
		self.assertEqual(уборка.оставлено, {"chapters": [], "lessons": [урок]})
		self.assertFalse(self.есть("Course Chapter", глава))
		self.assertFalse(frappe.db.exists("Lesson Reference", {"parent": глава}))
		self.assertTrue(self.есть("Course Lesson", урок))
