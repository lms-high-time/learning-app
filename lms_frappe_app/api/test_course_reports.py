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
from lms_frappe_app.tests.sample_data import (
	привязать_урок,
	зачислить,
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
		self.урок = создать_урок(f"Урок репортов {суффикс}")
		глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		self.курс = frappe.db.get_value("Course Chapter", глава, "course")
		# Урок принадлежит курсу через главу, а `создать_урок` заводит уроку
		# собственный курс: без смены главы репорт по нему уходил бы в чужой
		# курс, и фильтр по уроку возвращал пустоту.
		self.второй_урок = создать_урок(f"Второй урок {суффикс}")
		frappe.db.set_value("Course Lesson", self.второй_урок, "chapter", глава)
		привязать_урок(глава, self.второй_урок)

		frappe.get_doc(
			{
				"doctype": "Agent Lesson Directive",
				"lesson": self.урок,
				"objectives": "Назвать спонсора",
				"teaching_directive": "Начать с примера",
			}
		).insert(ignore_permissions=True)

		зачислить(self.ученик, self.урок)

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
		# Курс старой модели: итоги доставляет `student_context` — тем же
		# правилом, что и `start_lesson` курса из релиза.
		данные = student.student_context(создать_занятие(self.ученик, self.урок))["data"]
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
		self.assertEqual(репорты[0]["lesson"], self.урок)

	def test_репорт_называет_редакцию_директивы_на_момент_жалобы(self):
		"""Претензия к директиве без её редакции нечитаема: курс с тех пор
		переписывали, и непонятно, на что жаловались."""
		self.пожаловаться("directive_mismatch", "Указание не подходит")
		frappe.set_user(self.куратор)

		репорт = authoring.course_reports(course=self.курс)["data"]["reports"][0]

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

		отобранные = authoring.course_reports(course=self.курс, lesson=self.второй_урок)["data"]["reports"]

		self.assertEqual([р["text"] for р in отобранные], ["Про второй урок"])

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
		чужой_урок = создать_урок(f"Чужой урок {frappe.generate_hash(length=6)}")
		зачислить(self.ученик, чужой_урок)
		чужой = self.пожаловаться("material_issue", "Про другой курс", lesson=чужой_урок)

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
		зачислить(сосед, self.урок)
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
		другой_урок = создать_урок(f"Другой курс {суффикс}")
		зачислить(self.ученик, другой_урок)
		в_другом_курсе = self.пожаловаться("stuck", "В другом курсе", lesson=другой_урок)

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
		self.assertTrue(закрытый["lesson_title"].startswith("Урок репортов"))

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
		другой_урок = создать_урок(f"Другой курс {frappe.generate_hash(length=6)}")
		зачислить(self.ученик, другой_урок)
		репорт = self.пожаловаться("stuck", "В другом курсе", lesson=другой_урок)
		frappe.set_user("Administrator")
		frappe.get_doc("Agent Course Report", репорт).update(
			{"status": "Fixed", "resolution": "Поправили"}
		).save(ignore_permissions=True)

		self.assertEqual(self.итоги_на_занятии(), [])
