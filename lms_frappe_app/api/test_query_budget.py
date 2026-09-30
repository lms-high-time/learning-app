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
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	зачислить,
	привязать_главу,
	привязать_урок,
	политика_по_умолчанию,
	создать_вопрос,
	создать_занятие,
	создать_квиз,
	создать_менеджера,
	создать_организацию,
	создать_урок,
	создать_ученика,
	сдать_отчёт,
)
from lms_frappe_app.api import manager, student
from lms_frappe_app.testing import сколько_запросов

#: Сколько обращений к базе делает метод на данных этого модуля. Меняется
#: вместе с кодом и только осознанно — см. пояснение модуля.
БЮДЖЕТ = {
	# +1 за программы курса (learning-services#405): входит ли курс в
	# программу — одна выборка, пока не входит.
	"course_outline": 14,
	# +3 за начало занятия (#238): зачин урока, обещание курса и прочие
	# занятия ученика по курсу — по ним сервер решает, с чего начинать.
	# +1 за итоги репортов (learning-services#286): одна выборка, пока
	# нового итога нет.
	# +1 за пространство (learning-services#341): где начат документ курса —
	# одна выборка на вызов, её берут и занятие, и блоки документа.
	# +1 за порядок программы (learning-services#405): входит ли курс в
	# программу — одна выборка, пока не входит.
	# +2 за сигналы агенту (learning-services#416): история занятий по уроку —
	# одна выборка на трудные цели и брошенные попытки — и остаток уроков к
	# сроку курса, у которого срок есть.
	# +1 за домашку (learning-services#439): есть ли у курса задания — одна
	# выборка, пока их нет.
	"start_lesson": 34,
	# +2 за журнал проверки (learning-services#437): запись об ответе и о
	# выданном следом вопросе.
	"submit_answer": 17,
	"student_detail": 12,
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
		# схемы квиза и документов курса, и бюджет поплыл бы на 26 запросов
		# между прогоном модуля и прогоном всего приложения.
		прогрев = student.start_lesson(lesson=self.уроки[0])["data"]["session"]
		frappe.db.set_value("Agent Learning Session", прогрев, "status", "Abandoned")
		self._ворота(
			"start_lesson", lambda: student.start_lesson(lesson=self.уроки[0]), прогреть=False
		)

	def test_бюджет_submit_answer(self):
		попытка = self._попытка()
		student.submit_answer(попытка, self.вопросы[0], "1", "слова ученика")
		self._ворота(
			"submit_answer",
			lambda: student.submit_answer(попытка, self.вопросы[1], "1", "слова ученика"),
			прогреть=False,
		)

	def test_бюджет_student_detail(self):
		frappe.set_user(self.менеджер)
		self._ворота("student_detail", lambda: manager.student_detail(self.ученик))

	# --- механика ворот ---

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

	def _попытка(self) -> str:
		занятие = student.start_lesson(lesson=self.уроки[0])["data"]["session"]
		сдать_отчёт(занятие)
		return quiz.начать_попытку(занятие)["attempt"]
