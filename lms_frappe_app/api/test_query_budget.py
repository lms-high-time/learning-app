# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Ворота на число обращений к базе у горячих методов.

`Why:` N+1 не виден ни по тестам, ни по ревью: метод остаётся правильным,
просто на курсе из тридцати уроков делает триста запросов вместо пяти.
Правильность этого не ловит вообще ничем — только счётчиком (lms-platform#196).

**Число сверяется точно, а не «не больше».** Потолок разъезжается с
действительностью: код улучшили — запас молча вырос, и следующий N+1 уместился
в него незамеченным. Сверка на равенство — та же работа, что у ассерта на
состав проверок (§6.1 правил): поймать незадекларированное изменение. Меняется
число вместе с кодом, и в теле коммита пишется, что именно добавилось или
ушло.

Измеряется **второй** вызов метода, первый прогревочный: `get_meta` читает
схему DocType из базы один раз на процесс, и без прогрева число зависело бы от
того, какие тесты отработали раньше — модуль в одиночку давал одно, весь
прогон другое.
"""

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import quiz
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	курс_из_релиза,
	урок_релиза,
	зачислить,
	привязать_главу,
	привязать_урок,
	политика_по_умолчанию,
	создать_вопрос,
	создать_домашку,
	создать_занятие,
	создать_квиз,
	создать_менеджера,
	создать_организацию,
	создать_урок,
	создать_ученика,
	сдать_отчёт,
)
from lms_frappe_app.api import manager, review, student, team
from lms_frappe_app.testing import сколько_запросов

#: Сколько обращений к базе делает метод на данных этого модуля. Меняется
#: вместе с кодом и только осознанно — см. пояснение модуля.
БЮДЖЕТ = {
	# +1 за программы курса (learning-services#405): входит ли курс в
	# программу — одна выборка, пока не входит.
	"course_outline": 14,
	# Курс из релиза (learning-services#506), прохождение уже есть: урок
	# релиза по записи, действующий релиз ещё раз при сверке прохождения,
	# прохождение с блокировкой и двумя таблицами строк, есть ли вопросы,
	# рамка, срез урока, глава, тексты целей, история прошлых уроков — по
	# одной выборке; занятие, событие «пакет выдан», начало занятия, итоги
	# репортов, блоки документа, попытки, сигналы — как прежде. У курса
	# образца есть задание, поэтому домашка читает порядок уроков.
	"start_lesson": 51,
	# +2 за журнал проверки (learning-services#437): запись об ответе и о
	# выданном следом вопросе.
	"submit_answer": 17,
	"student_detail": 12,
	# Домашки ученика (learning-services#439), три сдачи в двух курсах: сдачи,
	# уроки, задания и комментарии — по одной выборке, курсы ученика — раз,
	# порядок уроков ради адресов — раз на курс, а не на сдачу.
	"my_homework": 23,
	# `start_lesson` на курсе с заданиями у текущего и прошлого урока: сверх
	# `start_lesson` — поиск и чтение сдачи прошлого урока.
	"start_lesson_homework": 56,
	# Очередь куратора (learning-services#452), три сдачи в двух курсах:
	# сдачи, счёт, значения фильтров, уроки с курсом, названия курсов,
	# организаций и заданий, имена учеников, проверенные версии — по одной
	# выборке; адресов уроков в очереди нет — порядка глав не читает.
	"review_queue": 10,
	# Назначения руководителя (learning-services#452), два курса с заданиями:
	# задания с названиями уроков — одной выборкой, порядок уроков — четырьмя
	# на все курсы, сроки назначений — одной, названия неопубликованных курсов
	# — одной, а не запросом на назначение.
	"allocations": 13,
}


class IntegrationTestQueryBudget(IntegrationTestCase):
	"""Курс из двух глав по три урока: N+1 по главам и урокам здесь виден."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		политика_по_умолчанию()
		суффикс = frappe.generate_hash(length=6)

		self.ученик = создать_ученика(f"qb-{суффикс}@example.com")
		self.организация = создать_организацию(f"Компания {суффикс}")
		добавить_в_организацию(self.ученик, self.организация)

		self.уроки = [создать_урок(f"Урок 1 {суффикс}")]
		self.курс = зачислить(self.ученик, self.уроки[0])
		глава = frappe.db.get_value("Course Lesson", self.уроки[0], "chapter")
		self.уроки += [
			self._урок(глава, f"Урок {номер} {суффикс}") for номер in (2, 3)
		]
		вторая = frappe.get_doc(
			{"doctype": "Course Chapter", "title": "Глава 2", "course": self.курс}
		).insert(ignore_permissions=True).name
		привязать_главу(self.курс, вторая)
		self.уроки += [self._урок(вторая, f"Урок {номер} {суффикс}") for номер in (4, 5, 6)]

		# Второй курс той же организации: без него запрос на организации
		# менеджера внутри перечня курсов ученика выполнялся бы один раз и
		# N+1 в `student_detail` не проявлялся.
		второй = создать_урок(f"Урок другого курса {суффикс}")
		self.второй_курс = зачислить(self.ученик, второй)
		for курс in (self.курс, self.второй_курс):
			self._назначить(курс)

		for урок in self.уроки:
			self._директива(урок)

		self.вопросы = [
			создать_вопрос(f"Вопрос {номер} {суффикс}", [("да", True), ("нет", False)])
			for номер in range(1, 5)
		]
		создать_квиз(self.уроки[0], self.вопросы)

		self.менеджер = создать_менеджера(f"qbm-{суффикс}@example.com", self.организация)
		занятие = создать_занятие(self.ученик, self.уроки[1])
		frappe.db.set_value("Agent Learning Session", занятие, "course", self.курс)

		frappe.set_user(self.ученик)
		# Документы заполняются от имени ученика: документ, заведённый
		# администратором, к ученику не относится, и бюджет прошёл бы мимо
		# чтения содержимого.
		self._артефакты(суффикс)

	# --- ворота ---

	def test_бюджет_course_outline(self):
		self._ворота("course_outline", lambda: student.course_outline(self.курс))

	def test_бюджет_start_lesson(self):
		# Прогревочное занятие бросается, чтобы измеряемый вызов завёл своё, а
		# не переиспользовал готовое: иначе вставка сессии осталась бы вне
		# ворот. Прогрев тем же уроком, а не соседним, — соседний не тронул бы
		# схемы квиза и документов курса, и бюджет поплыл бы между прогоном
		# модуля и прогоном всего приложения.
		урок = self._курс_релиза()[1]
		self._старт_с_прогревом("start_lesson", урок)

	def test_бюджет_submit_answer(self):
		попытка = self._попытка()
		student.submit_answer(попытка, self.вопросы[0], "1", "слова ученика")
		self._ворота(
			"submit_answer",
			lambda: student.submit_answer(попытка, self.вопросы[1], "1", "слова ученика"),
			прогреть=False,
		)

	def test_бюджет_my_homework(self):
		"""Три сдачи: два урока разных глав одного курса и урок второго курса."""
		self._домашки()
		self._ворота("my_homework", lambda: student.my_homework())

	def test_бюджет_start_lesson_с_домашкой(self):
		"""Курс с заданиями: задание текущего урока и сдача прошлого."""
		первый, второй = self._курс_релиза(с_домашкой=True)
		student.submit_homework(lesson=первый, answer="Сделал")
		self._старт_с_прогревом("start_lesson_homework", второй)

	def test_бюджет_review_queue(self):
		"""Те же три сдачи в очереди руководителя их организации."""
		self._домашки()
		frappe.set_user(self.менеджер)
		self.assertEqual(review.queue()["data"]["total"], 3)
		self._ворота("review_queue", lambda: review.queue(), прогреть=False)

	def test_бюджет_allocations(self):
		"""Два назначенных курса с заданиями: у первого — в двух главах."""
		self._домашки()
		frappe.set_user(self.менеджер)
		self._ворота("allocations", lambda: team.allocations(organization=self.организация))

	def test_бюджет_student_detail(self):
		frappe.set_user(self.менеджер)
		self._ворота("student_detail", lambda: manager.student_detail(self.ученик))

	# --- механика ворот ---

	def _старт_с_прогревом(self, метод: str, урок: str) -> None:
		прогрев = student.start_lesson(lesson=урок)
		self.assertTrue(прогрев["ok"], прогрев)
		frappe.db.set_value("Agent Learning Session", прогрев["data"]["session"], "status", "Abandoned")
		self._ворота(метод, lambda: student.start_lesson(lesson=урок), прогреть=False)

	def _ворота(self, метод: str, вызов, *, прогреть: bool = True) -> None:
		if прогреть:
			вызов()
		запросов, запросы = сколько_запросов(вызов)
		self.assertEqual(
			запросов,
			БЮДЖЕТ[метод],
			f"{метод} сделал {запросов} запросов, в БЮДЖЕТЕ записано "
			f"{БЮДЖЕТ[метод]}. Больше — вернулся запрос в цикл; меньше — стало "
			"лучше, и число в БЮДЖЕТЕ пора обновить. Запросы вызова:\n"
			+ "\n".join(" ".join(запрос.split()) for запрос in запросы),
		)

	# --- данные ---

	def _урок(self, глава: str, название: str) -> str:
		урок = frappe.get_doc(
			{"doctype": "Course Lesson", "title": название, "chapter": глава}
		).insert(ignore_permissions=True).name
		привязать_урок(глава, урок)
		return урок

	def _назначить(self, курс: str) -> None:
		frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.организация,
				"course": курс,
				"deadline": "2026-12-31",
				"mandatory": 1,
			}
		).insert(ignore_permissions=True)

	def _директива(self, урок: str) -> None:
		frappe.get_doc(
			{
				"doctype": "Agent Lesson Directive",
				"lesson": урок,
				"objectives": "Понимать цикл",
				"teaching_directive": "Начать с примера",
			}
		).insert(ignore_permissions=True)

	def _артефакты(self, суффикс: str) -> None:
		"""Две схемы документов курса, обе с блоками этого урока."""
		for номер in (1, 2):
			frappe.get_doc(
				{
					"doctype": "Agent Course Artifact",
					"course": self.курс,
					"slug": f"doc{номер}",
					"title": f"Документ {номер} {суффикс}",
					"version": 1,
					"is_active": 1,
					"blocks": [
						{"block_key": f"b{номер}1", "title": "Блок 1", "lesson": self.уроки[0]},
						{"block_key": f"b{номер}2", "title": "Блок 2", "lesson": self.уроки[1]},
					],
				}
			).insert(ignore_permissions=True)
			student.update_artifact(self.курс, f"doc{номер}", f"b{номер}1", "текст")

	def _домашки(self) -> None:
		"""Задания у первого и второго урока, у первого урока другой главы и у
		урока второго курса; сдачи — по всем, кроме второго, одна возвращена."""
		from lms_frappe_app.agent_learning.homework import СДАЧА

		frappe.set_user("Administrator")
		урок_второго_курса = frappe.get_all("Course Lesson", filters={"course": self.второй_курс}, pluck="name")[0]
		for урок in (self.уроки[0], self.уроки[1], self.уроки[3], урок_второго_курса):
			создать_домашку(урок)
		frappe.set_user(self.ученик)
		сдачи = [
			student.submit_homework(lesson=урок, answer="Сделал")["data"]["submission"]["id"]
			for урок in (self.уроки[0], self.уроки[3], урок_второго_курса)
		]
		возвращённая = frappe.get_doc(СДАЧА, сдачи[0])
		возвращённая.append("history", {"event": "returned", "by_user": "Administrator", "comment": "Доделай"})
		возвращённая.save(ignore_permissions=True)

	def _курс_релиза(self, *, с_домашкой: bool = False) -> tuple[str, str]:
		"""Курс из релиза той же организации — две главы, три урока, документ с
		блоками первых двух уроков, заполненный учеником; первый и второй уроки.

		`start_lesson` открывает только курс из релиза (learning-services#506).
		С `с_домашкой` задания есть у первых двух уроков.
		"""
		frappe.set_user("Administrator")
		релиз = пример_релиза(f"qb-{frappe.generate_hash(length=8)}")
		if с_домашкой:
			for урок in релиз["lessons"][:2]:
				урок["homework"] = {"title": "Задание", "description": "Сделайте пример.", "answer_mode": "text", "due_days": None}
		курс, _ = курс_из_релиза(релиз=релиз)
		self._назначить(курс)
		frappe.set_user(self.ученик)
		student.update_artifact(курс, "notebook", "log", rows=[{"topic": "Первая встреча"}])
		return урок_релиза(курс, "l-1"), урок_релиза(курс, "l-2")

	def _попытка(self) -> str:
		занятие = создать_занятие(self.ученик, self.уроки[0])
		сдать_отчёт(занятие)
		return quiz.начать_попытку(занятие)["attempt"]
