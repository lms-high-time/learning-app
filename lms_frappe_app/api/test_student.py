# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import quiz
from lms_frappe_app.agent_learning.artifacts import codes
from lms_frappe_app.agent_learning.profile import КЛЮЧИ_ПРОФИЛЯ
from lms_frappe_app.tests.sample_data import (
	курс_из_релиза,
	урок_релиза,
	зачислить_на_курс,
	настроить_квиз,
	привязать_урок,
	создать_курс,
	создать_занятие,
	зачислить,
	добавить_в_организацию,
	занятие_релиза,
	создать_организацию,
	создать_ученика,
	создать_урок,
)
from lms_frappe_app.agent_learning.access import (
	НЕ_ЗАЧИСЛЕН,
	КУРС_НЕ_ОПУБЛИКОВАН,
	КУРС_НЕ_ОТКРЫТ,
	УЖЕ_ЗАПИСАН,
)
from lms_frappe_app.api import student


def закрыть_урок(занятие: str) -> None:
	"""Урок занятия пройден, занятие завершено — без отметок пунктов и квиза."""
	документ = frappe.get_doc("Agent Learning Session", занятие)
	quiz.отметить_урок_пройденным(документ)
	документ.status = "Completed"
	документ.save(ignore_permissions=True)


class IntegrationTestStudentAPI(IntegrationTestCase):
	"""Методы учебного потока — в том виде, в каком их увидит агент."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)

		self.ученик = создать_ученика(f"api-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = frappe.db.get_value(
			"Course Chapter", frappe.db.get_value("Course Lesson", self.урок, "chapter"), "course"
		)
		self.организация = создать_организацию(f"Компания {суффикс}")
		добавить_в_организацию(self.ученик, self.организация)
		frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.организация,
				"course": self.курс,
				"deadline": "2026-12-31",
				"mandatory": 1,
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.ученик)

	# --- форма ответа ---

	def test_отказ_приходит_успешным_ответом_с_кодом(self):
		# Ожидаемый отказ не может ехать HTTP-ошибкой: тело ошибки формирует
		# Frappe, и машинного кода в нём не остаётся.
		ответ = student.start_lesson(lesson="такого-урока-нет")
		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.УРОК_НЕ_НАЙДЕН)

	# --- список курсов ---

	def test_курс_приходит_с_дедлайном_и_прогрессом(self):
		курсы = student.list_my_courses()["data"]["courses"]
		мой = next(к for к in курсы if к["id"] == self.курс)

		self.assertEqual(str(мой["deadline"]), "2026-12-31")
		self.assertTrue(мой["mandatory"])
		self.assertEqual(мой["progress"]["lessons_total"], 1)
		self.assertEqual(мой["progress"]["lessons_completed"], 0)
		self.assertEqual(мой["next_lesson"]["id"], self.урок)

	def test_во_внутренностях_frappe_наружу_не_течёт(self):
		# Контракт обязан оставаться интерфейсом общего назначения.
		выдано = json.dumps(student.list_my_courses(), ensure_ascii=False, default=str)
		for поле in ("doctype", "docstatus", "modified_by", "owner"):
			self.assertNotIn(поле, выдано)

	# --- что продолжать ---

	def _второй_урок(self) -> str:
		"""Второй урок того же курса, после первого."""
		frappe.set_user("Administrator")
		глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		урок = frappe.get_doc(
			{"doctype": "Course Lesson", "title": "Второй", "chapter": глава}
		).insert(ignore_permissions=True).name
		привязать_урок(глава, урок)
		frappe.set_user(self.ученик)
		return урок

	def test_развилка_ставит_урок_последнего_занятия_первым(self):
		второй = self._второй_урок()
		срочный = self._срочный_курс()
		создать_занятие(self.ученик, второй)

		ответ = student.study_options()["data"]

		рекомендация = ответ["recommended"]
		self.assertEqual(рекомендация["lesson"]["id"], второй)
		self.assertEqual((рекомендация["lesson"]["number"], рекомендация["lesson"]["total"]), (2, 2))
		self.assertEqual(рекомендация["reason"], "last_lesson")
		self.assertEqual(рекомендация["course"]["id"], self.курс)
		self.assertEqual([д["course"]["id"] for д in ответ["others"]], [срочный])
		self.assertEqual(ответ["others"][0]["reason"], "deadline")
		self.assertEqual(ответ["others"][0]["deadline"], "2026-06-30")

	def test_развилка_говорит_куда_пустит_браузер(self):
		# Проба кончилась — в браузере можно только уроки, уже начатые там.
		self.addCleanup(настроить_квиз, web_demo_lessons=2)
		frappe.set_user("Administrator")
		настроить_квиз(web_demo_lessons=1)
		frappe.set_user(self.ученик)
		self._срочный_курс()
		занятие = создать_занятие(self.ученик, self.урок)
		frappe.db.set_value("Agent Learning Session", занятие, "web_chat", 1)

		ответ = student.study_options()["data"]

		self.assertEqual(ответ["web_demo"], {"used": 1, "limit": 1, "left": 0})
		self.assertTrue(ответ["recommended"]["web"])
		self.assertFalse(ответ["others"][0]["web"])

	def test_развилка_без_курсов_пуста(self):
		frappe.set_user("Administrator")
		одинокий = создать_ученика(f"api-solo-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(одинокий)

		ответ = student.study_options()["data"]

		self.assertIsNone(ответ["recommended"])
		self.assertEqual(ответ["others"], [])

	def _срочный_курс(self) -> str:
		"""Второй курс с дедлайном раньше — его взял бы вызов без аргументов."""
		frappe.set_user("Administrator")
		урок = создать_урок(f"Срочный {frappe.generate_hash(length=6)}")
		курс = frappe.db.get_value(
			"Course Chapter", frappe.db.get_value("Course Lesson", урок, "chapter"), "course"
		)
		frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.организация,
				"course": курс,
				"deadline": "2026-06-30",
				"mandatory": 1,
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)
		return курс

	# --- кто вошёл ---

	def test_whoami_называет_учётную_запись_и_организацию(self):
		"""Без этого «вошёл не тем аккаунтом» неотличимо от «нет курсов»."""
		данные = student.whoami()["data"]

		self.assertEqual(данные["login"], self.ученик)
		self.assertEqual(
			[(о["id"], о["role"]) for о in данные["organizations"]],
			[(self.организация, "Member")],
		)
		self.assertFalse(данные["organizations"][0]["suspended"])

	def test_whoami_не_несёт_ролей_frappe(self):
		# Роли — внутреннее устройство платформы, агенту они ни к чему.
		выдано = json.dumps(student.whoami(), ensure_ascii=False, default=str)

		for поле in ("roles", "LMS Student", "System Manager", "doctype"):
			self.assertNotIn(поле, выдано)

	# --- заметки об ученике ---

	def test_заметка_замещается_по_ключу(self):
		student.remember(kind="fact", key="role", text="Директор агентства")
		student.remember(kind="fact", key="Role", text="Совладелец агентства")

		факты = student.my_notes()["data"]["facts"]

		self.assertEqual(
			[(ф["key"], ф["text"]) for ф in факты],
			[("role", "Совладелец агентства")],
			"ключ нормализуется, а запись по нему замещается, а не удваивается",
		)

	def test_наблюдение_живёт_при_курсе_и_помнит_занятие(self):
		занятие = создать_занятие(self.ученик, self.урок)

		student.remember(
			kind="observation", key="pace", text="Торопится", session=занятие
		)

		запись = frappe.get_doc(
			"Agent Student Note", {"student": self.ученик, "note_key": "pace"}
		)
		self.assertEqual(запись.course, self.курс)
		self.assertEqual(запись.source_session, занятие)
		self.assertEqual(запись.kind, "Observation")

	def test_проект_живёт_при_курсе_и_в_другом_курсе_приходит_вопросом(self):
		"""#408: учебный сценарий одного курса не подставляется в другой молча."""
		занятие = создать_занятие(self.ученик, self.урок)
		ответ = student.remember(
			kind="project", key="scenario", text="Учебный сценарий «Северный склад»", session=занятие
		)
		self.assertTrue(ответ["ok"], ответ)
		запись = frappe.get_doc("Agent Student Note", {"student": self.ученик, "note_key": "scenario"})
		self.assertEqual((запись.kind, запись.course), ("Project", self.курс))

		свои = student.my_notes(course=self.курс)["data"]
		self.assertEqual([з["key"] for з in свои["project"]], ["scenario"])
		self.assertEqual(свои["projects_elsewhere"], [])
		self.assertEqual(свои["facts"], [])

		frappe.set_user("Administrator")
		другой_урок = создать_урок(f"Другой курс {frappe.generate_hash(length=6)}")
		другой_курс = зачислить(self.ученик, другой_урок)
		frappe.set_user(self.ученик)
		чужие = student.my_notes(course=другой_курс)["data"]
		self.assertEqual(чужие["project"], [])
		self.assertEqual(
			[(з["key"], з["course"]) for з in чужие["projects_elsewhere"]], [("scenario", self.курс)]
		)
		self.assertTrue(чужие["projects_elsewhere"][0]["course_title"])

		своё = создать_занятие(self.ученик, другой_урок)
		student.remember(kind="project", key="project", text="Свой проект", session=своё)
		теперь = student.my_notes(course=другой_курс)["data"]
		self.assertEqual([з["key"] for з in теперь["project"]], ["project"])
		self.assertEqual(теперь["projects_elsewhere"], [], "свой проект есть — чужие не предлагаются")

	def test_проект_без_занятия_отклоняется(self):
		ответ = student.remember(kind="project", key="scenario", text="Склад")
		self.assertEqual(ответ["error"]["code"], student.ЧУЖОЕ_ЗАНЯТИЕ)

	def test_наблюдение_без_занятия_отклоняется(self):
		ответ = student.remember(kind="observation", key="pace", text="Торопится")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ЧУЖОЕ_ЗАНЯТИЕ)

	def test_неизвестный_вид_заметки_отклоняется(self):
		ответ = student.remember(kind="мнение", key="pace", text="Торопится")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.НЕИЗВЕСТНЫЙ_ВИД)

	def test_пустой_ключ_заметки_отклоняется_своим_кодом(self):
		"""Why: раньше пустой ключ отвечал кодом «неизвестный вид», хотя вид к
		этому месту уже распознан. Агент ветвится по коду, а не по тексту, и
		чинил бы не то — подставлял другой вид вместо того, чтобы дать ключ."""
		ответ = student.remember(kind="fact", key="   ", text="Ведёт склад")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ПУСТОЙ_КЛЮЧ)

	def test_лимит_заметок_упирается_в_предел(self):
		for номер in range(student.ЛИМИТ_ЗАМЕТОК):
			student.remember(kind="fact", key=f"k{номер}", text="да")

		ответ = student.remember(kind="fact", key="ещё один", text="да")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ПЕРЕПОЛНЕНО)

	def _предел_заметок(self, предел: int) -> None:
		from lms_frappe_app.tests.sample_data import политика_по_умолчанию

		self.addCleanup(политика_по_умолчанию)
		frappe.db.set_single_value("Agent Learning Settings", "student_notes_limit", предел)
		frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")

	def test_предел_заметок_читается_из_настроек(self):
		self._предел_заметок(1)
		student.remember(kind="fact", key="language", text="Python")

		ответ = student.remember(kind="fact", key="ещё один", text="да")

		self.assertEqual(ответ["error"]["code"], student.ПЕРЕПОЛНЕНО)
		self.assertEqual(ответ["error"]["limit"], 1)

	def test_ключ_профиля_пишется_и_при_заполненном_пределе(self):
		"""learning-services#463: ученик с полным набором прочих фактов иначе
		не смог бы заполнить профиль."""
		self._предел_заметок(2)
		student.remember(kind="fact", key="language", text="Python")
		student.remember(kind="fact", key="city", text="Казань")

		self.assertTrue(student.remember(kind="fact", key="role", text="Директор")["ok"])
		ответ = student.remember(kind="fact", key="ещё один", text="да")
		self.assertEqual(ответ["error"]["code"], student.ПЕРЕПОЛНЕНО)

	def test_ключи_профиля_не_занимают_предел(self):
		self._предел_заметок(2)
		for ключ in КЛЮЧИ_ПРОФИЛЯ:
			student.remember(kind="fact", key=ключ, text="да")
		student.remember(kind="fact", key="language", text="Python")

		ответ = student.remember(kind="fact", key="city", text="Казань")

		self.assertTrue(ответ["ok"], ответ)

	def test_замена_по_ключу_проходит_и_на_пределе(self):
		"""Иначе упор в лимит становится тупиком: заменить тоже нельзя."""
		for номер in range(student.ЛИМИТ_ЗАМЕТОК):
			student.remember(kind="fact", key=f"k{номер}", text="да")

		self.assertTrue(student.remember(kind="fact", key="k0", text="нет")["ok"])

	def test_забытая_заметка_исчезает(self):
		student.remember(kind="fact", key="role", text="Директор")

		self.assertTrue(student.forget(key="role")["ok"])
		self.assertEqual(student.my_notes()["data"]["facts"], [])

	def test_забыть_несуществующее_отклоняется(self):
		ответ = student.forget(key="ничего-такого")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ЗАМЕТКА_НЕ_НАЙДЕНА)

	def test_заметки_приходят_с_датами(self):
		student.remember(kind="fact", key="role", text="Директор")

		факт = student.my_notes()["data"]["facts"][0]

		self.assertIsNotNone(факт["since"])
		self.assertIsNotNone(факт["updated"])

	# --- сводка ---

	def test_сводка_считает_курсы_и_последние_занятия(self):
		создать_занятие(self.ученик, self.урок)
		сводка = student.get_my_progress()["data"]

		# Ровно один: «не меньше» замаскировало бы утечку чужих зачислений.
		self.assertEqual(сводка["courses_total"], 1)
		self.assertEqual(сводка["courses_overdue"], 0)
		self.assertEqual(сводка["recent_sessions"][0]["lesson"], self.урок)


class IntegrationTestReportIssue(IntegrationTestCase):
	"""Репорт агента о курсе — по занятию урока курса из релиза."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.ученик = создать_ученика(f"rep-{frappe.generate_hash(length=6)}@example.com")
		self.курс, self.релиз = курс_из_релиза()
		зачислить_на_курс(self.ученик, self.курс)
		self.урок = урок_релиза(self.курс, "l-1")
		self.занятие = занятие_релиза(self.ученик, self.курс, "l-1")
		frappe.set_user(self.ученик)

	def test_репорт_привязан_сервером_к_курсу_уроку_и_релизу(self):
		"""Why: привязку от агента можно указать на чужой урок, и вторая копия
		разъедется с занятием. Сервер берёт её из занятия."""
		ответ = student.report_issue(
			session=self.занятие, kind="material_issue", text="В примере перепутаны роли"
		)

		self.assertTrue(ответ["ok"], ответ)
		репорт = frappe.get_doc("Agent Course Report", ответ["data"]["report"])
		self.assertEqual(
			(репорт.kind, репорт.course, репорт.lesson, репорт.release, репорт.status),
			("Material Issue", self.курс, self.урок, self.релиз, "New"),
		)

	def test_курс_без_релиза_отказ_без_записи(self):
		frappe.set_user("Administrator")
		урок = создать_урок(f"Без релиза {frappe.generate_hash(length=6)}")
		зачислить(self.ученик, урок)
		занятие = создать_занятие(self.ученик, урок)
		frappe.set_user(self.ученик)

		ответ = student.report_issue(session=занятие, kind="stuck", text="Встал на примере")

		self.assertEqual(ответ["error"]["code"], "course_not_released")
		self.assertFalse(frappe.db.exists("Agent Course Report", {"session": занятие}))

	def test_цель_репорта_сохраняется_и_не_ломает_запись_длиной(self):
		"""Why: `objective` в схеме — `Data`, то есть varchar(140), а цель
		присылает агент, и длина её ничем не ограничена. Длинная цель уезжала
		бы агенту ошибкой базы мимо контракта, да ещё с присланным текстом в
		сообщении."""
		длинная = "Понимать цикл и всё, что с ним связано, " * 10

		своя = student.report_issue(
			session=self.занятие, kind="stuck", text="Встал на этой цели", objective="Понимать цикл"
		)
		длинноватая = student.report_issue(
			session=self.занятие, kind="stuck", text="Встал на этой цели", objective=длинная
		)

		self.assertEqual(
			frappe.db.get_value("Agent Course Report", своя["data"]["report"], "objective"),
			"Понимать цикл",
		)
		self.assertTrue(длинноватая["ok"])
		сохранено = frappe.db.get_value(
			"Agent Course Report", длинноватая["data"]["report"], "objective"
		)
		self.assertEqual(сохранено, длинная.strip()[: student.ДЛИНА_ЦЕЛИ])
		# Пин на тип поля: `Data` длиннее 140 символов не принимает.
		self.assertLessEqual(len(сохранено), 140)

	def test_длинное_описание_обрезается_а_репорт_доходит(self):
		"""Why: описание пишет агент, и предела у него нет — зациклившийся
		высыпет в репорт весь разговор, а читает список человек. Обрезка, а не
		отказ: сигнал нужнее хвоста текста. Предел молчаливый, и без пина он
		уедет незамеченным вместе с концом описания."""
		длинное = "Материал противоречит сам себе. " * 200

		ответ = student.report_issue(session=self.занятие, kind="material_issue", text=длинное)

		self.assertTrue(ответ["ok"])
		сохранено = frappe.db.get_value(
			"Agent Course Report", ответ["data"]["report"], "text"
		)
		self.assertEqual(сохранено, длинное.strip()[: student.ДЛИНА_ОПИСАНИЯ])
		self.assertLess(len(сохранено), len(длинное.strip()))

	def test_виды_репорта_совпадают_со_схемой(self):
		"""Why: словарь метода и options поля живут врозь, а сверяет их только
		база — уже на вставке. Переименуют значение в схеме, и метод сложит
		репорт с несуществующим видом: `ValidationError` мимо контракта,
		агенту 500 вместо машинного кода. В обе стороны: вид, заведённый в
		схеме и не выставленный наружу, недостижим и потому тоже расхождение."""
		опции = frappe.get_meta("Agent Course Report").get_field("kind").options.split("\n")

		self.assertEqual(set(student.ВИДЫ_РЕПОРТОВ.values()), set(опции))

	def test_неизвестный_вид_репорта_отклоняется(self):
		ответ = student.report_issue(session=self.занятие, kind="нытьё", text="всё плохо")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.НЕИЗВЕСТНЫЙ_ВИД_РЕПОРТА)

	def test_репорт_без_описания_отклоняется(self):
		"""Why: без машинного кода пустой текст упирается в обязательное поле
		схемы и уезжает агенту ошибкой сервера, а не отказом, который он умеет
		разобрать."""
		ответ = student.report_issue(session=self.занятие, kind="stuck", text="   ")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ПУСТОЙ_РЕПОРТ)


class IntegrationTestSelfEnroll(IntegrationTestCase):
	"""Самозапись: частный ученик и сотрудник компании."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"self-{суффикс}@example.com")
		self.урок = создать_урок(f"Открытый {суффикс}")
		self.курс = frappe.db.get_value(
			"Course Chapter", frappe.db.get_value("Course Lesson", self.урок, "chapter"), "course"
		)
		frappe.db.set_value("LMS Course", self.курс, "published", 1)

	def каталог(self):
		return {к["id"] for к in student.list_catalog()["data"]["courses"]}

	def test_частный_ученик_видит_каталог_и_записывается(self):
		frappe.set_user(self.ученик)
		self.assertIn(self.курс, self.каталог())

		ответ = student.enroll(self.курс)["data"]

		self.assertEqual(ответ["course"], self.курс)
		self.assertEqual(ответ["first_lesson"]["id"], self.урок)
		self.assertTrue(
			frappe.db.exists("LMS Enrollment", {"member": self.ученик, "course": self.курс})
		)

	def test_записанный_курс_из_каталога_исчезает(self):
		# Он и так виден в list_my_courses — дублировать незачем.
		frappe.set_user(self.ученик)
		student.enroll(self.курс)
		self.assertNotIn(self.курс, self.каталог())

	def test_повторная_запись_отклоняется(self):
		frappe.set_user(self.ученик)
		student.enroll(self.курс)

		ответ = student.enroll(self.курс)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], УЖЕ_ЗАПИСАН)

	def test_неопубликованный_курс_недоступен(self):
		frappe.db.set_value("LMS Course", self.курс, "published", 0)
		frappe.set_user(self.ученик)

		self.assertNotIn(self.курс, self.каталог())
		self.assertEqual(student.enroll(self.курс)["error"]["code"], КУРС_НЕ_ОПУБЛИКОВАН)

	def test_сотрудник_видит_только_курсы_своей_компании(self):
		"""Обучение идёт за счёт компании: запись на произвольный курс
		каталога тратила бы чужой бюджет."""
		frappe.set_user("Administrator")
		свой = создать_курс(f"Свой {frappe.generate_hash(length=6)}")
		frappe.db.set_value("LMS Course", свой, "published", 1)
		организация = создать_организацию(
			f"Компания {frappe.generate_hash(length=6)}",
			allowed_courses=[{"course": свой}],
		)
		добавить_в_организацию(self.ученик, организация)

		frappe.set_user(self.ученик)
		каталог = self.каталог()

		self.assertIn(свой, каталог)
		self.assertNotIn(self.курс, каталог)
		self.assertEqual(student.enroll(self.курс)["error"]["code"], КУРС_НЕ_ОТКРЫТ)

	def test_компания_без_ограничений_открывает_весь_каталог(self):
		frappe.set_user("Administrator")
		организация = создать_организацию(f"Компания {frappe.generate_hash(length=6)}")
		добавить_в_организацию(self.ученик, организация)

		frappe.set_user(self.ученик)

		self.assertIn(self.курс, self.каталог())


class IntegrationTestCourseOutlineRelease(IntegrationTestCase):
	"""Дерево курса из релиза: агент должен уметь вернуться к пройденному."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"out-rel-{суффикс}@example.com")
		self.курс, _ = курс_из_релиза()
		зачислить_на_курс(self.ученик, self.курс)
		self.первый = урок_релиза(self.курс, "l-1")
		self.второй = урок_релиза(self.курс, "l-2")
		frappe.set_user(self.ученик)

	def уроки(self):
		структура = student.course_outline(self.курс)["data"]
		return [урок for глава in структура["chapters"] for урок in глава["lessons"]]

	def test_структура_показывает_все_уроки_и_текущий(self):
		уроки = self.уроки()

		self.assertEqual([у["id"] for у in уроки], [self.первый, self.второй, урок_релиза(self.курс, "l-3")])
		self.assertTrue(уроки[0]["current"])
		self.assertFalse(уроки[0]["completed"])

	def test_после_прохождения_урок_помечен_пройденным(self):
		закрыть_урок(создать_занятие(self.ученик, self.первый))

		уроки = self.уроки()

		self.assertTrue(уроки[0]["completed"])
		self.assertTrue(уроки[1]["current"], "текущим должен стать следующий урок")

	def test_повтор_пройденного_не_двигает_прогресс(self):
		# Ровно то, ради чего метод и нужен: идентификатор пройденного урока
		# больше неоткуда взять — list_my_courses отдаёт только следующий.
		закрыть_урок(создать_занятие(self.ученик, self.первый))
		до = student.list_my_courses()["data"]["courses"][0]["progress"]

		пройденный = next(у["id"] for у in self.уроки() if у["completed"])
		self.assertEqual(пройденный, self.первый)
		закрыть_урок(создать_занятие(self.ученик, пройденный))

		self.assertEqual(student.list_my_courses()["data"]["courses"][0]["progress"], до)

	def test_структура_чужого_курса_недоступна(self):
		frappe.set_user("Administrator")
		чужой = создать_курс(f"Чужой {frappe.generate_hash(length=6)}")
		frappe.set_user(self.ученик)

		ответ = student.course_outline(чужой)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], НЕ_ЗАЧИСЛЕН)


class IntegrationTestArtifacts(IntegrationTestCase):
	"""Документы курса: ученик собирает их по ходу обучения, агент помогает."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"art-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = зачислить(self.ученик, self.урок)
		self.схема(
			blocks=[
				{"block_key": "goal", "title": "Цель", "hint": "Одной фразой, без клише"},
				{"block_key": "sponsor", "title": "Спонсор"},
			]
		)
		frappe.set_user(self.ученик)

	def схема(self, slug: str = "summary", **поля):
		frappe.set_user("Administrator")
		документ = frappe.get_doc(
			{
				"doctype": "Agent Course Artifact",
				"course": self.курс,
				"slug": slug,
				"title": "Резюме проекта",
				**поля,
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)
		return документ

	# --- чтение ---

	def test_без_ключа_перечисляются_документы_с_заполненностью(self):
		перечень = student.artifact(self.курс)["data"]["artifacts"]

		self.assertEqual(
			[(а["artifact"], а["blocks_total"], а["blocks_filled"]) for а in перечень],
			[("summary", 2, 0)],
		)
		self.assertEqual(перечень[0]["layout"], "sections")

	def test_с_ключом_приходят_блоки_с_подсказками(self):
		student.update_artifact(self.курс, "summary", "goal", "Открыть седьмую кофейню")

		документ = student.artifact(self.курс, "summary")["data"]

		self.assertEqual(
			[(б["key"], б["content"]) for б in документ["blocks"]],
			[("goal", "Открыть седьмую кофейню"), ("sponsor", "")],
			"порядок — из схемы; пустой блок приходит без содержимого",
		)
		self.assertEqual(документ["blocks"][0]["hint"], "Одной фразой, без клише")

	def test_неизвестный_документ_отклоняется(self):
		ответ = student.artifact(self.курс, "lean_canvas")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], codes.АРТЕФАКТ_НЕ_НАЙДЕН)

	def test_документы_чужого_курса_недоступны(self):
		frappe.set_user("Administrator")
		чужой = создать_курс(f"Чужой {frappe.generate_hash(length=6)}")
		frappe.set_user(self.ученик)

		ответ = student.artifact(чужой)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], НЕ_ЗАЧИСЛЕН)

	def test_документы_идут_в_порядке_объявления(self):
		"""Правка схемы не должна переставлять документы местами."""
		self.схема("map", title="Карта результатов", blocks=[{"block_key": "d1", "title": "Р1"}])
		self.схема("summary", blocks=[{"block_key": "goal", "title": "Цель"}])

		перечень = student.artifact(self.курс)["data"]["artifacts"]

		self.assertEqual([а["artifact"] for а in перечень], ["summary", "map"])

	# --- запись ---

	def test_запись_создаёт_экземпляр_и_считает_заполненность(self):
		ответ = student.update_artifact(self.курс, "summary", "goal", "Открыть кофейню")["data"]

		self.assertEqual((ответ["blocks_filled"], ответ["blocks_total"]), (1, 2))
		self.assertTrue(
			frappe.db.exists(
				"Agent Student Artifact",
				{"student": self.ученик, "course": self.курс, "artifact": "summary"},
			)
		)

	def test_повторная_запись_замещает_блок(self):
		student.update_artifact(self.курс, "summary", "goal", "Черновик")
		student.update_artifact(self.курс, "summary", "Goal", "Открыть седьмую кофейню")

		документ = frappe.get_doc(
			"Agent Student Artifact",
			{"student": self.ученик, "course": self.курс, "artifact": "summary"},
		)
		self.assertEqual(
			[(б.block_key, б.content) for б in документ.blocks],
			[("goal", "Открыть седьмую кофейню")],
			"ключ нормализуется, строка замещается, а не удваивается",
		)

	def test_неизвестный_блок_отклоняется(self):
		ответ = student.update_artifact(self.курс, "summary", "budget", "Миллион")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], codes.БЛОК_НЕ_НАЙДЕН)

	def test_пустой_блок_отклоняется_и_не_стирает_записанное(self):
		student.update_artifact(self.курс, "summary", "goal", "Открыть кофейню")

		ответ = student.update_artifact(self.курс, "summary", "goal", "   ")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], codes.ПУСТОЙ_БЛОК)
		блоки = student.artifact(self.курс, "summary")["data"]["blocks"]
		self.assertEqual(блоки[0]["content"], "Открыть кофейню")

	def test_очистка_удаляет_блок(self):
		student.update_artifact(self.курс, "summary", "goal", "Старый проект")
		student.update_artifact(self.курс, "summary", "sponsor", "Марина")

		ответ = student.update_artifact(self.курс, "summary", "Goal", clear=True)["data"]

		self.assertEqual(
			ответ,
			{
				"artifact": "summary",
				"key": "goal",
				"blocks_total": 2,
				"blocks_filled": 1,
				"empty_cells": [],
				"created": [],
			},
		)
		документ = frappe.get_doc(
			"Agent Student Artifact",
			{"student": self.ученик, "course": self.курс, "artifact": "summary"},
		)
		self.assertEqual([б.block_key for б in документ.blocks], ["sponsor"])

	def test_очистка_пустого_блока_не_отказ(self):
		# Документ ещё не заводился: очищать нечего, и ответ тот же, что после
		# удаления, — а экземпляр ради этого не создаётся.
		ответ = student.update_artifact(self.курс, "summary", "goal", "", clear="true")

		self.assertTrue(ответ["ok"])
		self.assertEqual(ответ["data"]["blocks_filled"], 0)
		self.assertFalse(
			frappe.db.exists(
				"Agent Student Artifact",
				{"student": self.ученик, "course": self.курс, "artifact": "summary"},
			)
		)

	def test_очистка_с_текстом_отклоняется(self):
		student.update_artifact(self.курс, "summary", "goal", "Старый проект")

		ответ = student.update_artifact(self.курс, "summary", "goal", "Новый проект", clear=True)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], codes.ОЧИСТКА_С_ТЕКСТОМ)
		блоки = student.artifact(self.курс, "summary")["data"]["blocks"]
		self.assertEqual(блоки[0]["content"], "Старый проект")

	def test_блок_исчезнувший_из_схемы_не_теряет_содержимого(self):
		"""Автор правит схему — труд ученика остаётся."""
		student.update_artifact(self.курс, "summary", "sponsor", "Марина")
		self.схема(blocks=[{"block_key": "goal", "title": "Цель"}])

		без_спонсора = student.artifact(self.курс, "summary")["data"]["blocks"]
		self.assertEqual([б["key"] for б in без_спонсора], ["goal"])

		self.схема(
			blocks=[{"block_key": "goal", "title": "Цель"}, {"block_key": "sponsor", "title": "Спонсор"}]
		)
		вернулся = student.artifact(self.курс, "summary")["data"]["blocks"]
		self.assertEqual(вернулся[1]["content"], "Марина")

	def test_запись_помнит_версию_схемы(self):
		student.update_artifact(self.курс, "summary", "goal", "Цель")
		вторая = self.схема(blocks=[{"block_key": "goal", "title": "Цель"}])
		student.update_artifact(self.курс, "summary", "goal", "Уточнённая цель")

		документ = frappe.get_doc(
			"Agent Student Artifact",
			{"student": self.ученик, "course": self.курс, "artifact": "summary"},
		)
		self.assertEqual(документ.schema_version, вторая.name)

	def test_прогресс_показывает_заполненность_документа(self):
		student.update_artifact(self.курс, "summary", "goal", "Открыть кофейню")

		курс = next(
			к for к in student.get_my_progress()["data"]["courses"] if к["id"] == self.курс
		)

		self.assertEqual(
			[(д["artifact"], д["blocks_total"], д["blocks_filled"]) for д in курс["documents"]],
			[("summary", 2, 1)],
		)
