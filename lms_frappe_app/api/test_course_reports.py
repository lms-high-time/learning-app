# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.api import authoring, student
from lms_frappe_app.api.authoring import (
	КУРС_НЕ_НАЙДЕН,
	НЕВЕРНЫЙ_ОРИГИНАЛ,
	НЕДОПУСТИМЫЙ_ПЕРЕХОД_РЕПОРТА,
	НЕИЗВЕСТНЫЙ_СТАТУС_РЕПОРТА,
	НУЖЕН_ОРИГИНАЛ,
	НУЖЕН_ОТВЕТ_УЧЕНИКУ,
)
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	зачислить_на_курс,
	курс_из_релиза,
	урок_релиза,
	создать_занятие,
	создать_куратора,
	создать_ученика,
	создать_урок,
)


class IntegrationTestCourseReports(IntegrationTestCase):
	"""Репорты курса: куратор разбирает, ученик видит итог.

	Без чтения механизм разомкнут: агент ученика шлёт `report_issue`, а
	посмотреть накопленное некому — обратная связь про курс, который не
	работает, лежит мёртвым грузом (lms-platform#231). Без ответа разомкнут
	с другой стороны: ученик не видит смысла писать репорты, а курс правят
	именно по ним (learning-services#286).
	"""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)

		self.куратор = создать_куратора(f"rep-author-{суффикс}@example.com")
		self.ученик = создать_ученика(f"rep-pupil-{суффикс}@example.com")
		self.курс, _ = курс_из_релиза()
		self.урок = урок_релиза(self.курс, "l-1")
		self.второй_урок = урок_релиза(self.курс, "l-2")
		зачислить_на_курс(self.ученик, self.курс)

	def урок_другого_курса(self) -> str:
		"""Первый урок другого курса из релиза, на который записан ученик."""
		курс, _ = курс_из_релиза()
		зачислить_на_курс(self.ученик, курс)
		return урок_релиза(курс, "l-1")

	def пожаловаться(self, kind: str, text: str, lesson: str | None = None, ученик: str | None = None) -> str:
		"""Репорт от имени ученика — тем же путём, каким его шлёт агент."""
		урок = lesson or self.урок
		ученик = ученик or self.ученик
		занятие = создать_занятие(ученик, урок)
		frappe.set_user(ученик)
		ответ = student.report_issue(session=занятие, kind=kind, text=text)
		frappe.set_user("Administrator")
		return ответ["data"]["report"]

	def разобрать(self, репорт: str, status: str, **поля) -> dict:
		"""Смена статуса от имени куратора — тем же путём, каким её шлёт агент."""
		frappe.set_user(self.куратор)
		ответ = authoring.resolve_report(report=репорт, status=status, **поля)
		frappe.set_user("Administrator")
		return ответ

	def итоги_на_занятии(self) -> list[dict]:
		frappe.set_user(self.ученик)
		данные = student.start_lesson(lesson=self.урок)["data"]
		frappe.set_user("Administrator")
		return данные["closed_reports"]

	def мои_репорты(self, **параметры) -> list[dict]:
		frappe.set_user(self.ученик)
		репорты = student.my_reports(**параметры)["data"]["reports"]
		frappe.set_user("Administrator")
		return репорты

	def test_куратор_видит_репорты_своего_курса(self):
		self.пожаловаться("directive_mismatch", "Слишком напористо, вопросы подряд")
		frappe.set_user(self.куратор)

		репорты = authoring.course_reports(course=self.курс)["data"]["reports"]

		self.assertEqual(len(репорты), 1)
		self.assertEqual(репорты[0]["kind"], "directive_mismatch")
		self.assertEqual(репорты[0]["text"], "Слишком напористо, вопросы подряд")
		self.assertEqual((репорты[0]["lesson"], репорты[0]["lesson_key"]), (self.урок, "l-1"))

	def test_репорт_называет_редакцию_директивы_на_момент_жалобы(self):
		"""Претензия к директиве без её редакции нечитаема: курс с тех пор
		переписывали, и непонятно, на что жаловались. Редакция есть у репорта
		урока с директивой; репорт по курсу из релиза ссылается на релиз."""
		урок = создать_урок(f"Урок с директивой {frappe.generate_hash(length=6)}")
		курс = frappe.db.get_value("Course Chapter", frappe.db.get_value("Course Lesson", урок, "chapter"), "course")
		директива = frappe.get_doc(
			{"doctype": "Agent Lesson Directive", "lesson": урок, "objectives": "Назвать спонсора"}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Agent Course Report",
				"session": создать_занятие(self.ученик, урок),
				"course": курс,
				"lesson": урок,
				"lesson_directive": директива.name,
				"kind": "Directive Mismatch",
				"text": "Указание не подходит",
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.куратор)

		репорт = authoring.course_reports(course=курс)["data"]["reports"][0]

		self.assertTrue(репорт["directive_version"])

	def test_кто_пожаловался_не_отдаётся(self):
		"""`Why:` куратору нужно, что не так с курсом, а не кто сказал. Имя в
		выдаче превращает обратную связь в донос и мешает жаловаться."""
		self.пожаловаться("material_issue", "Материал противоречит сам себе")
		frappe.set_user(self.куратор)

		целиком = frappe.as_json(authoring.course_reports(course=self.курс)["data"])

		self.assertNotIn(self.ученик, целиком)
		self.assertNotIn("student", целиком)

	def test_фильтр_по_виду(self):
		self.пожаловаться("material_issue", "Материал плох")
		self.пожаловаться("directive_mismatch", "Указание не подходит")
		frappe.set_user(self.куратор)

		отобранные = authoring.course_reports(course=self.курс, kind="material_issue")["data"]["reports"]

		self.assertEqual([р["kind"] for р in отобранные], ["material_issue"])

	def test_фильтр_по_уроку(self):
		self.пожаловаться("material_issue", "Про первый урок")
		self.пожаловаться("material_issue", "Про второй урок", lesson=self.второй_урок)
		frappe.set_user(self.куратор)

		отобранные = authoring.course_reports(course=self.курс, lesson="l-2")["data"]["reports"]

		self.assertEqual(
			[(р["text"], р["lesson"], р["lesson_key"]) for р in отобранные],
			[("Про второй урок", self.второй_урок, "l-2")],
		)
		self.assertEqual(authoring.course_reports(course=self.курс, lesson="l-9")["data"]["reports"], [])

	def test_ученику_метод_закрыт(self):
		"""Роль, а не эндпоинт: агент ученика не должен читать чужие жалобы."""
		self.пожаловаться("stuck", "Застрял")
		frappe.set_user(self.ученик)

		with self.assertRaises(frappe.PermissionError):
			authoring.course_reports(course=self.курс)

	def test_несуществующий_курс_отказывает_доменным_кодом(self):
		frappe.set_user(self.куратор)

		ответ = authoring.course_reports(course="нет-такого-курса")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], КУРС_НЕ_НАЙДЕН)

	# --- разбор куратором ---

	def test_куратор_видит_статус_и_фильтрует_по_нему(self):
		закрытый = self.пожаловаться("material_issue", "Материал плох")
		self.пожаловаться("stuck", "Застрял")
		self.разобрать(закрытый, "fixed", resolution="Переписали пример")
		frappe.set_user(self.куратор)

		все = authoring.course_reports(course=self.курс)["data"]["reports"]
		открытые = authoring.course_reports(course=self.курс, status="open")["data"]["reports"]
		исправленные = authoring.course_reports(course=self.курс, status="fixed")["data"]["reports"]
		неизвестный = authoring.course_reports(course=self.курс, status="closed")

		по_номеру = {р["id"]: р for р in все}
		self.assertEqual(по_номеру[закрытый]["status"], "fixed")
		self.assertEqual(по_номеру[закрытый]["resolution"], "Переписали пример")
		self.assertTrue(по_номеру[закрытый]["resolved_at"])
		self.assertEqual([р["text"] for р in открытые], ["Застрял"])
		self.assertIsNone(открытые[0]["resolution"])
		self.assertEqual([р["id"] for р in исправленные], [закрытый])
		self.assertEqual(неизвестный["error"]["code"], НЕИЗВЕСТНЫЙ_СТАТУС_РЕПОРТА)

	def test_переходы_статуса_идут_по_таблице(self):
		"""Закрытый репорт меняет итог только через переоткрытие: итог уже
		ушёл ученику, и молча переписать его значило бы соврать ему."""
		репорт = self.пожаловаться("material_issue", "Материал плох")

		self.assertEqual(self.разобрать(репорт, "in_progress")["data"]["status"], "in_progress")
		закрыт = self.разобрать(репорт, "rejected", resolution="Пример верный, так задумано")
		self.assertEqual(закрыт["data"]["status"], "rejected")
		self.assertTrue(закрыт["data"]["resolved_at"])

		в_обход = self.разобрать(репорт, "fixed", resolution="Всё-таки поправили")
		self.assertEqual(в_обход["error"]["code"], НЕДОПУСТИМЫЙ_ПЕРЕХОД_РЕПОРТА)
		self.assertEqual(в_обход["error"]["allowed"], ["in_progress"])

		переоткрыт = self.разобрать(репорт, "in_progress")["data"]
		self.assertIsNone(переоткрыт["resolved_at"])
		self.assertIsNone(переоткрыт["resolution"], "прежний итог не выдаётся за действующий")
		self.assertEqual(self.разобрать(репорт, "resolved")["error"]["code"], НЕИЗВЕСТНЫЙ_СТАТУС_РЕПОРТА)

	def test_исправить_и_отклонить_можно_только_с_ответом_ученику(self):
		репорт = self.пожаловаться("material_issue", "Материал плох")

		for статус in ("fixed", "rejected"):
			ответ = self.разобрать(репорт, статус, resolution="   ")
			self.assertEqual(ответ["error"]["code"], НУЖЕН_ОТВЕТ_УЧЕНИКУ, статус)
		self.assertEqual(frappe.db.get_value("Agent Course Report", репорт, "status"), "New")

	def test_дубль_ссылается_на_репорт_того_же_курса(self):
		"""Ответ оригинала уходит ученику: ссылка на чужой курс показала бы
		ему ответ о курсе, которого он не проходил."""
		репорт = self.пожаловаться("material_issue", "Материал плох")
		чужой = self.пожаловаться("material_issue", "Про другой курс", lesson=self.урок_другого_курса())

		self.assertEqual(self.разобрать(репорт, "duplicate")["error"]["code"], НУЖЕН_ОРИГИНАЛ)
		for оригинал in (чужой, репорт, "нет-такого"):
			ответ = self.разобрать(репорт, "duplicate", duplicate_of=оригинал)
			self.assertEqual(ответ["error"]["code"], НЕВЕРНЫЙ_ОРИГИНАЛ, оригинал)

	def test_ученику_разбор_закрыт(self):
		репорт = self.пожаловаться("stuck", "Застрял")
		frappe.set_user(self.ученик)

		with self.assertRaises(frappe.PermissionError):
			authoring.resolve_report(report=репорт, status="rejected", resolution="Сам разберусь")

	# --- итог глазами ученика ---

	def test_ученик_видит_только_репорты_своих_занятий(self):
		"""Отбор — по занятиям, а не по `owner`: репорт, перенесённый
		сотрудником платформы, записан от его имени, но остаётся репортом
		ученика (learning-services#237)."""
		суффикс = frappe.generate_hash(length=6)
		сосед = создать_ученика(f"rep-other-{суффикс}@example.com")
		зачислить_на_курс(сосед, self.курс)
		self.пожаловаться("stuck", "Сосед застрял", ученик=сосед)
		свой = self.пожаловаться("stuck", "Я застрял")
		перенесённый = frappe.get_doc(
			{
				"doctype": "Agent Course Report",
				"session": создать_занятие(self.ученик, self.урок),
				"course": self.курс,
				"lesson": self.урок,
				"kind": "Material Issue",
				"text": "Перенесён из переписки",
			}
		).insert(ignore_permissions=True)
		в_другом_курсе = self.пожаловаться("stuck", "В другом курсе", lesson=self.урок_другого_курса())

		все = {р["id"] for р in self.мои_репорты()}
		этого_курса = {р["id"] for р in self.мои_репорты(course=self.курс)}

		self.assertEqual(все, {свой, перенесённый.name, в_другом_курсе})
		self.assertEqual(этого_курса, {свой, перенесённый.name})

	def test_ученик_видит_статус_и_ответ(self):
		репорт = self.пожаловаться("material_issue", "Материал плох")
		открытый = self.мои_репорты()[0]
		self.assertEqual(открытый["status"], "new")
		self.assertIsNone(открытый["resolution"])
		self.assertIsNone(открытый["resolved_at"])

		self.разобрать(репорт, "fixed", resolution="Переписали пример")

		закрытый = self.мои_репорты()[0]
		self.assertEqual(закрытый["id"], репорт)
		self.assertEqual(закрытый["kind"], "material_issue")
		self.assertEqual(закрытый["status"], "fixed")
		self.assertEqual(закрытый["resolution"], "Переписали пример")
		self.assertTrue(закрытый["resolved_at"])
		self.assertEqual(закрытый["lesson_title"], "Урок первый")

	def test_дубль_отвечает_ответом_оригинала(self):
		оригинал = self.пожаловаться("material_issue", "Пример неверный")
		дубль = self.пожаловаться("material_issue", "В примере ошибка")
		self.разобрать(оригинал, "fixed", resolution="Исправили пример")
		self.разобрать(дубль, "duplicate", duplicate_of=оригинал)

		по_номеру = {р["id"]: р for р in self.мои_репорты()}

		self.assertEqual(по_номеру[дубль]["status"], "duplicate")
		self.assertEqual(по_номеру[дубль]["resolution"], "Исправили пример")

	def test_итог_приходит_на_занятии_один_раз(self):
		"""Повтор на каждом старте превратил бы новость в шум, а открытый
		репорт итога ещё не несёт."""
		репорт = self.пожаловаться("material_issue", "Материал плох")
		self.пожаловаться("stuck", "Застрял")
		self.разобрать(репорт, "fixed", resolution="Переписали пример")

		первый = self.итоги_на_занятии()
		второй = self.итоги_на_занятии()

		self.assertEqual([р["id"] for р in первый], [репорт])
		self.assertEqual(первый[0]["resolution"], "Переписали пример")
		self.assertEqual(второй, [])
		# Итог ушёл, но из списка ученика репорт никуда не делся.
		self.assertIn(репорт, {р["id"] for р in self.мои_репорты()})

	def test_новый_итог_после_переоткрытия_приходит_снова(self):
		репорт = self.пожаловаться("material_issue", "Материал плох")
		self.разобрать(репорт, "rejected", resolution="Так задумано")
		self.итоги_на_занятии()

		self.разобрать(репорт, "in_progress")
		self.assertEqual(self.итоги_на_занятии(), [], "переоткрытый — не итог")
		self.разобрать(репорт, "fixed", resolution="Всё-таки переписали")

		снова = self.итоги_на_занятии()
		self.assertEqual([(р["id"], р["status"]) for р in снова], [(репорт, "fixed")])

	def test_итоги_другого_курса_на_занятии_не_приходят(self):
		репорт = self.пожаловаться("stuck", "В другом курсе", lesson=self.урок_другого_курса())
		frappe.set_user("Administrator")
		frappe.get_doc("Agent Course Report", репорт).update(
			{"status": "Fixed", "resolution": "Поправили"}
		).save(ignore_permissions=True)

		self.assertEqual(self.итоги_на_занятии(), [])


class IntegrationTestCourseReportsRelease(IntegrationTestCase):
	"""Репорт по уроку курса из релиза: вопрос — ключ вопроса урока в релизе,
	привязка — действующий релиз, а не редакция указаний (learning-services#506)."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"rep-rel-author-{суффикс}@example.com")
		self.ученик = создать_ученика(f"rep-rel-pupil-{суффикс}@example.com")
		self.курс, self.релиз = курс_из_релиза()
		зачислить_на_курс(self.ученик, self.курс)
		self.занятие = создать_занятие(self.ученик, урок_релиза(self.курс, "l-1"))
		frappe.set_user(self.ученик)

	def test_вопрос_по_ключу_релиза_и_привязка_к_релизу(self):
		ответ = student.report_issue(
			session=self.занятие, kind="quiz_question_issue", text="Два верных варианта", question="S1/l-1-D1"
		)
		self.assertTrue(ответ["ok"], ответ)

		запись = frappe.db.get_value(
			"Agent Course Report",
			ответ["data"]["report"],
			["question", "question_key", "release", "lesson_directive"],
			as_dict=True,
		)
		self.assertEqual(
			запись,
			{"question": None, "question_key": "S1/l-1-D1", "release": self.релиз, "lesson_directive": None},
		)
		frappe.set_user(self.куратор)
		[репорт] = authoring.course_reports(course=self.курс)["data"]["reports"]
		self.assertEqual(
			(репорт["question"], репорт["question_key"], репорт["release"], репорт["directive_version"]),
			(None, "S1/l-1-D1", self.релиз, None),
		)

	def test_вопрос_чужого_урока_отказ_без_записи(self):
		ответ = student.report_issue(
			session=self.занятие, kind="quiz_question_issue", text="Не тот вопрос", question="S1/l-2-D1"
		)

		self.assertEqual(ответ["error"]["code"], "question_mismatch")
		self.assertFalse(frappe.db.exists("Agent Course Report", {"session": self.занятие}))

	def test_урок_снят_из_релиза_вопрос_не_из_квиза(self):
		"""Урока занятия нет в действующем релизе: вопросов у него нет, репорт без вопроса — с релизом."""
		frappe.set_user("Administrator")
		ключ = f"rep-gone-{frappe.generate_hash(length=6)}"
		курс, _ = курс_из_релиза(релиз=пример_релиза(ключ))
		зачислить_на_курс(self.ученик, курс)
		занятие = создать_занятие(self.ученик, урок_релиза(курс, "l-2"))
		без_урока = пример_релиза(ключ)
		без_урока["chapters"][0]["lessons"] = ["l-1"]
		без_урока["lessons"] = [у for у in без_урока["lessons"] if у["key"] != "l-2"]
		_, релиз = курс_из_релиза(релиз=без_урока)
		frappe.set_user(self.ученик)

		ответ = student.report_issue(
			session=занятие, kind="quiz_question_issue", text="Не тот вопрос", question="S1/l-2-D1"
		)
		self.assertEqual(ответ["error"]["code"], "question_mismatch")
		self.assertFalse(frappe.db.exists("Agent Course Report", {"session": занятие}))

		ответ = student.report_issue(session=занятие, kind="stuck", text="Встал на примере")
		запись = frappe.db.get_value(
			"Agent Course Report", ответ["data"]["report"], ["question_key", "release"], as_dict=True
		)
		self.assertEqual(запись, {"question_key": None, "release": релиз})

	def test_ключ_урока_по_релизу_репорта(self):
		"""Ключ урока выводится по релизу, на котором жаловались: и у урока,
		снятого из действующего релиза; фильтр по ключу находит и его."""
		frappe.set_user("Administrator")
		ключ = f"rep-keys-{frappe.generate_hash(length=6)}"
		курс, первый = курс_из_релиза(релиз=пример_релиза(ключ))
		зачислить_на_курс(self.ученик, курс)
		третий = создать_занятие(self.ученик, урок_релиза(курс, "l-3"))
		frappe.set_user(self.ученик)
		student.report_issue(session=третий, kind="stuck", text="Про третий")
		frappe.set_user("Administrator")
		без_третьего = пример_релиза(ключ)
		без_третьего["chapters"] = без_третьего["chapters"][:1]
		без_третьего["lessons"] = без_третьего["lessons"][:2]
		_, второй = курс_из_релиза(релиз=без_третьего)
		занятие = создать_занятие(self.ученик, урок_релиза(курс, "l-2"))
		frappe.set_user(self.ученик)
		student.report_issue(session=занятие, kind="material_issue", text="Про второй")
		frappe.set_user(self.куратор)

		репорты = authoring.course_reports(course=курс)["data"]["reports"]

		self.assertEqual(
			[(р["text"], р["release"], р["lesson_key"]) for р in репорты],
			[("Про второй", второй, "l-2"), ("Про третий", первый, "l-3")],
		)
		self.assertEqual(
			[р["text"] for р in authoring.course_reports(course=курс, lesson="l-3")["data"]["reports"]],
			["Про третий"],
		)

	def test_репорт_без_вопроса(self):
		ответ = student.report_issue(
			session=self.занятие, kind="stuck", text="Встал на примере", objective="Цель"
		)

		запись = frappe.db.get_value(
			"Agent Course Report",
			ответ["data"]["report"],
			["question_key", "release", "objective"],
			as_dict=True,
		)
		self.assertEqual(запись, {"question_key": None, "release": self.релиз, "objective": "Цель"})
