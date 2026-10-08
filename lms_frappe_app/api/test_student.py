# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.agent_learning import quiz
from lms_frappe_app.agent_learning.artifacts import codes
from lms_frappe_app.agent_learning.profile import КЛЮЧИ_ПРОФИЛЯ
from lms_frappe_app.tests.sample_data import (
	настроить_квиз,
	привязать_урок,
	создать_курс,
	создать_занятие,
	зачислить,
	добавить_в_организацию,
	создать_вопрос,
	создать_квиз,
	создать_организацию,
	создать_ученика,
	создать_урок,
	сдать_отчёт,
)
from lms_frappe_app.agent_learning.access import (
	НЕ_ЗАЧИСЛЕН,
	КУРС_НЕ_ОПУБЛИКОВАН,
	КУРС_НЕ_ОТКРЫТ,
	ОРГАНИЗАЦИЯ_ПРИОСТАНОВЛЕНА,
	УЖЕ_ЗАПИСАН,
)
from lms_frappe_app.api import student

ЭТАЛОННЫЕ_ПОЛЯ = ("is_correct", "possibility", "explanation_")


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
		frappe.db.set_value(
			"Course Lesson",
			self.урок,
			"body",
			"## Циклы\n\nЦикл повторяет действие. {{ YouTubeVideo(abc) }}",
		)
		self.директива = frappe.get_doc(
			{
				"doctype": "Agent Lesson Directive",
				"lesson": self.урок,
				"objectives": "Понимать цикл\nУметь читать код",
				"teaching_directive": "Начать с примера, не с определения",
				"probing_questions": "Что произойдёт при нуле итераций?",
			}
		).insert(ignore_permissions=True)

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

	# --- квиз через методы ---

	def test_полный_проход_квиза_через_методы(self):
		frappe.set_user("Administrator")
		вопрос = создать_вопрос("Два плюс два?", варианты=[("4", True), ("5", False)])
		создать_квиз(self.урок, [вопрос])
		frappe.set_user(self.ученик)

		занятие = создать_занятие(self.ученик, self.урок)
		сдать_отчёт(занятие)
		начало = student.request_quiz(занятие)["data"]
		итог = student.submit_answer(начало["attempt"], вопрос, "1", "слова ученика")["data"]

		self.assertTrue(итог["verdict"]["correct"])
		self.assertTrue(итог["result"]["passed"])
		self.assertEqual(итог["result"]["session_status"], "Completed")

	def test_ответ_не_принимается_после_отзыва_доступа(self):
		"""Доступ, отозванный посреди квиза, обязан останавливать и ответы.

		Иначе попытка, начатая при живом доступе, доходит до зачёта по курсу,
		которого у ученика уже нет: `request_quiz` доступ перепроверяет, а
		`submit_answer` — нет (lms-platform#195).
		"""
		frappe.set_user("Administrator")
		вопрос = создать_вопрос("Два плюс два?", варианты=[("4", True), ("5", False)])
		создать_квиз(self.урок, [вопрос])
		frappe.set_user(self.ученик)
		занятие = создать_занятие(self.ученик, self.урок)
		сдать_отчёт(занятие)
		начало = student.request_quiz(занятие)["data"]
		frappe.db.set_value("Learning Organization", self.организация, "status", "Suspended")

		ответ = student.submit_answer(начало["attempt"], вопрос, "1", "слова ученика")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], ОРГАНИЗАЦИЯ_ПРИОСТАНОВЛЕНА)
		self.assertFalse(
			frappe.db.exists("Agent Quiz Answer", {"attempt": начало["attempt"]}),
			"ответ по отозванному курсу не должен попадать в попытку",
		)

	def test_в_вопросе_квиза_нет_полей_эталона(self):
		frappe.set_user("Administrator")
		вопрос = создать_вопрос(
			"Столица?", варианты=[("Москва", True), ("Тула", False)], пояснение="Так исторически"
		)
		создать_квиз(self.урок, [вопрос])
		frappe.set_user(self.ученик)

		занятие = создать_занятие(self.ученик, self.урок)
		сдать_отчёт(занятие)
		выдано = json.dumps(student.request_quiz(занятие), ensure_ascii=False, default=str)

		for поле in ЭТАЛОННЫЕ_ПОЛЯ:
			self.assertNotIn(поле, выдано)
		self.assertNotIn("Так исторически", выдано)

	# --- отметки целей по ходу (learning-services#409) ---

	def test_отметка_по_номеру_с_прогрессом_и_следом_на_странице_курса(self):
		from lms_frappe_app.api import public

		занятие = создать_занятие(self.ученик, self.урок)
		ответ = student.mark_objective(занятие, 2, "covered", "Прочитал цикл в своём скрипте")
		self.assertTrue(ответ["ok"], ответ)
		данные = ответ["data"]
		self.assertEqual((данные["objective"], данные["text"]), (2, "Уметь читать код"))
		self.assertEqual(данные["progress"]["marked"], 1)
		self.assertEqual(данные["progress"]["open"], [{"number": 1, "text": "Понимать цикл"}])

		цели = public.course_map(course=self.курс)["data"]["chapters"][0]["lessons"][0]["objectives"]
		self.assertEqual(
			{ц["text"]: ц.get("status") for ц in цели},
			{"Понимать цикл": None, "Уметь читать код": "covered"},
			"отметка видна на странице курса сразу, без итога урока",
		)

	def test_разобранная_цель_без_заметки_и_skipped_по_ходу_отклоняются(self):
		занятие = создать_занятие(self.ученик, self.урок)
		без_заметки = student.mark_objective(занятие, 1, "covered")
		self.assertEqual(без_заметки["error"]["code"], student.ЦЕЛИ_НЕ_СОВПАЛИ)
		пропуск = student.mark_objective(занятие, 1, "skipped", "не дошли")
		self.assertEqual(пропуск["error"]["code"], student.ЦЕЛИ_НЕ_СОВПАЛИ)
		мимо = student.mark_objective(занятие, 3, "touched", "нет такой")
		self.assertEqual(мимо["error"]["code"], student.ЦЕЛИ_НЕ_СОВПАЛИ)

	def test_квиз_ждёт_отметки_всех_целей(self):
		frappe.set_user("Administrator")
		создать_квиз(self.урок, [создать_вопрос("Два плюс два?", варианты=[("4", True), ("5", False)])])
		frappe.set_user(self.ученик)
		занятие = создать_занятие(self.ученик, self.урок)
		student.mark_objective(занятие, 1, "covered", "Объяснил цикл своими словами")
		рано = student.request_quiz(занятие)
		self.assertEqual(рано["error"]["code"], student.НЕТ_ОТЧЁТА)
		self.assertEqual(рано["error"]["missing"], ["Уметь читать код"])

		student.mark_objective(занятие, 2, "covered", "Прочитал чужой цикл вслух")
		self.assertTrue(student.request_quiz(занятие)["ok"])

	def test_итог_дополняет_отметки_и_не_стирает_сделанное(self):
		занятие = создать_занятие(self.ученик, self.урок)
		student.mark_objective(занятие, 1, "covered", "Объяснил цикл своими словами")
		ответ = student.report_outcomes(занятие, [{"objective": 2, "status": "touched", "resume_from": "с чтения"}])
		self.assertTrue(ответ["ok"], ответ)
		self.assertEqual(ответ["data"]["progress"]["marked"], 2)

		строки = {
			с.objective: (с.status, с.evidence, с.resume_from)
			for с in frappe.get_doc("Agent Learning Session", занятие).outcomes
		}
		self.assertEqual(строки["Понимать цикл"], ("covered", "Объяснил цикл своими словами", None))
		self.assertEqual(строки["Уметь читать код"], ("touched", None, "с чтения"))

		неполный = student.report_outcomes(занятие, [{"objective": 1, "status": "covered"}])
		self.assertTrue(неполный["ok"], "то же занятие: вторая цель уже отмечена")

	def test_отметки_брошенного_занятия_доходят_до_следующего_урока(self):
		занятие = создать_занятие(self.ученик, self.урок)
		student.mark_objective(занятие, 1, "covered", "Объяснил цикл своими словами")
		student.mark_objective(занятие, 2, "touched", "начать с чтения чужого кода")
		frappe.set_user("Administrator")
		frappe.db.set_value("Agent Learning Session", занятие, {"status": "Abandoned", "finished_at": frappe.utils.now_datetime()})
		глава = frappe.db.get_value("Course Lesson", self.урок, "chapter")
		второй = frappe.get_doc({"doctype": "Course Lesson", "title": "Второй", "chapter": глава}).insert(ignore_permissions=True).name
		привязать_урок(глава, второй)
		frappe.set_user(self.ученик)

		контекст = student.student_context(создать_занятие(self.ученик, второй))["data"]
		self.assertEqual(
			[(п["objective"], п["resume_from"]) for п in контекст["carried_over"]],
			[("Уметь читать код", "начать с чтения чужого кода")],
		)
		self.assertEqual(
			[(р["objective"], р["evidence"]) for р in контекст["recent_work"]],
			[("Понимать цикл", "Объяснил цикл своими словами")],
		)

	def test_напоминание_об_отметках_в_ответах_посреди_занятия(self):
		занятие = создать_занятие(self.ученик, self.урок)
		ответ = student.remember(kind="observation", key="pace", text="Торопится", session=занятие)
		self.assertEqual(ответ["data"]["objectives_progress"]["marked"], 0)
		факт = student.remember(kind="fact", key="role", text="Руководитель")
		self.assertNotIn("objectives_progress", факт["data"], "вне занятия ключа нет")

	def test_отметка_цели_пишется_в_журнал(self):
		занятие = создать_занятие(self.ученик, self.урок)
		ответ = student.mark_objective(занятие, 1, "touched", "начать с примера")
		self.assertTrue(ответ["ok"], ответ)
		self.assertTrue(
			frappe.db.exists(
				"Agent Session Event", {"session": занятие, "kind": "Checkpoint Reported"}
			)
		)

	# --- репорты о курсе ---

	def test_репорт_привязан_сервером_к_курсу_уроку_и_редакции_указаний(self):
		"""Why: привязку от агента можно указать на чужой урок, и вторая копия
		разъедется с занятием. Сервер берёт её из занятия.

		Редакция указаний берётся действующая, а не любая: репорт отвечает на
		вопрос «это ещё актуально или указание с тех пор переписали», и ссылка
		на снятую с действия версию отвечала бы на него неверно."""
		frappe.set_user("Administrator")
		переписанная = frappe.get_doc(
			{
				"doctype": "Agent Lesson Directive",
				"lesson": self.урок,
				"objectives": "Понимать цикл\nУметь читать код",
				"teaching_directive": "Переписанные указания",
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)
		занятие = создать_занятие(self.ученик, self.урок)

		ответ = student.report_issue(
			session=занятие, kind="material_issue", text="В примере перепутаны роли"
		)

		self.assertTrue(ответ["ok"])
		репорт = frappe.get_doc("Agent Course Report", ответ["data"]["report"])
		self.assertEqual(репорт.kind, "Material Issue")
		self.assertEqual(репорт.course, self.курс)
		self.assertEqual(репорт.lesson, self.урок)
		self.assertEqual(репорт.status, "New")
		self.assertEqual(репорт.lesson_directive, переписанная.name)
		self.assertNotEqual(переписанная.name, self.директива.name)

	def test_цель_репорта_сохраняется_и_не_ломает_запись_длиной(self):
		"""Why: `objective` в схеме — `Data`, то есть varchar(140), а цели
		приходят из `objectives` директивы, где длина ничем не ограничена.
		Длинная цель уезжала бы агенту ошибкой базы мимо контракта, да ещё с
		присланным текстом в сообщении."""
		занятие = создать_занятие(self.ученик, self.урок)
		длинная = "Понимать цикл и всё, что с ним связано, " * 10

		своя = student.report_issue(
			session=занятие, kind="stuck", text="Встал на этой цели", objective="Понимать цикл"
		)
		длинноватая = student.report_issue(
			session=занятие, kind="stuck", text="Встал на этой цели", objective=длинная
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
		занятие = создать_занятие(self.ученик, self.урок)
		длинное = "Материал противоречит сам себе. " * 200

		ответ = student.report_issue(session=занятие, kind="material_issue", text=длинное)

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
		занятие = создать_занятие(self.ученик, self.урок)

		ответ = student.report_issue(session=занятие, kind="нытьё", text="всё плохо")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.НЕИЗВЕСТНЫЙ_ВИД_РЕПОРТА)

	def test_репорт_без_описания_отклоняется(self):
		"""Why: без машинного кода пустой текст упирается в обязательное поле
		схемы и уезжает агенту ошибкой сервера, а не отказом, который он умеет
		разобрать."""
		занятие = создать_занятие(self.ученик, self.урок)

		ответ = student.report_issue(session=занятие, kind="stuck", text="   ")

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ПУСТОЙ_РЕПОРТ)

	def test_в_репорт_идёт_любой_вопрос_своего_урока_и_не_идёт_чужой(self):
		"""Why: иначе репорт о своём уроке указывает на вопрос чужого курса, и
		автор правит не то место.

		«Свой» — принадлежность квизу урока, а не пригодность к проверке:
		вопрос, у которого не отмечен ни один верный вариант, сервер проверять
		не берётся, и это ровно тот случай, ради которого репорт и заведён."""
		frappe.set_user("Administrator")
		свой_вопрос = создать_вопрос("Свой вопрос", варианты=[("2", True), ("3", False)])
		непроверяемый_вопрос = создать_вопрос(
			"Вопрос без эталона", варианты=[("2", True), ("3", False)]
		)
		создать_квиз(self.урок, [свой_вопрос, непроверяемый_вопрос])
		# Через документ такой вопрос не завести: LMS Question требует хотя бы
		# один верный вариант. В базу он попадает импортом или прямой правкой —
		# на это и рассчитана отбраковка непроверяемых вопросов в quiz.py.
		frappe.db.set_value("LMS Question", непроверяемый_вопрос, "is_correct_1", 0)
		чужой_урок = создать_урок(f"Чужой {frappe.generate_hash(length=6)}")
		чужой_вопрос = создать_вопрос("Чужой вопрос", варианты=[("1", True), ("2", False)])
		создать_квиз(чужой_урок, [чужой_вопрос])
		frappe.set_user(self.ученик)
		занятие = создать_занятие(self.ученик, self.урок)

		свой = student.report_issue(
			session=занятие,
			kind="quiz_question_issue",
			text="Вопрос двусмысленный",
			question=свой_вопрос,
		)
		непроверяемый = student.report_issue(
			session=занятие,
			kind="quiz_question_issue",
			text="Вопрос сформулирован двусмысленно",
			question=непроверяемый_вопрос,
		)
		чужой = student.report_issue(
			session=занятие,
			kind="quiz_question_issue",
			text="Вопрос двусмысленный",
			question=чужой_вопрос,
		)

		self.assertTrue(свой["ok"])
		self.assertEqual(
			frappe.db.get_value("Agent Course Report", свой["data"]["report"], "question"),
			свой_вопрос,
		)
		self.assertTrue(непроверяемый["ok"])
		self.assertEqual(
			frappe.db.get_value("Agent Course Report", непроверяемый["data"]["report"], "question"),
			непроверяемый_вопрос,
		)
		self.assertFalse(чужой["ok"])
		self.assertEqual(чужой["error"]["code"], quiz.ЧУЖОЙ_ВОПРОС)

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

	# --- отчёт по целям ---

	def test_отчёт_по_целям_сохраняется(self):
		занятие = создать_занятие(self.ученик, self.урок)

		ответ = student.report_outcomes(
			занятие,
			outcomes=[
				{"objective": "Понимать цикл", "status": "covered"},
				{"objective": "Уметь читать код", "status": "skipped"},
			],
		)

		self.assertTrue(ответ["ok"])
		документ = frappe.get_doc("Agent Learning Session", занятие)
		self.assertEqual(
			[(с.objective, с.status) for с in документ.outcomes],
			[("Понимать цикл", "covered"), ("Уметь читать код", "skipped")],
			"порядок берётся из директивы, а не из отчёта",
		)

	def test_отчёт_с_пропущенной_целью_отклоняется(self):
		занятие = создать_занятие(self.ученик, self.урок)

		ответ = student.report_outcomes(
			занятие, outcomes=[{"objective": "Понимать цикл", "status": "covered"}]
		)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ЦЕЛИ_НЕ_СОВПАЛИ)
		self.assertEqual(ответ["error"]["missing"], ["Уметь читать код"])

	def test_отчёт_с_чужой_целью_отклоняется(self):
		занятие = создать_занятие(self.ученик, self.урок)

		ответ = student.report_outcomes(
			занятие,
			outcomes=[
				{"objective": "Понимать цикл", "status": "covered"},
				{"objective": "Уметь читать код", "status": "covered"},
				{"objective": "Выдуманная цель", "status": "covered"},
			],
		)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["unexpected"], ["Выдуманная цель"])

	def test_неизвестный_статус_отклоняется(self):
		занятие = создать_занятие(self.ученик, self.урок)

		ответ = student.report_outcomes(
			занятие, outcomes=[{"objective": "Понимать цикл", "status": "почти"}]
		)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ЦЕЛИ_НЕ_СОВПАЛИ)

	def test_повторный_отчёт_замещает_прежний(self):
		"""Занятие продолжили — отчёт должен обновиться, а не удвоиться."""
		занятие = создать_занятие(self.ученик, self.урок)
		сдать_отчёт(занятие)

		student.report_outcomes(
			занятие,
			outcomes=[
				{"objective": "Понимать цикл", "status": "covered"},
				{"objective": "Уметь читать код", "status": "touched"},
			],
		)

		документ = frappe.get_doc("Agent Learning Session", занятие)
		self.assertEqual(len(документ.outcomes), 2)
		self.assertEqual(документ.outcomes[1].status, "touched")

	def test_урок_не_закрывается_без_отчёта(self):
		занятие = создать_занятие(self.ученик, self.урок)

		ответ = student.complete_lesson(занятие)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.НЕТ_ОТЧЁТА)

	def test_квиз_не_начинается_без_отчёта(self):
		frappe.set_user("Administrator")
		создать_квиз(self.урок, [создать_вопрос("Два?", варианты=[("2", True), ("3", False)])])
		frappe.set_user(self.ученик)
		занятие = создать_занятие(self.ученик, self.урок)

		ответ = student.request_quiz(занятие)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.НЕТ_ОТЧЁТА)

	def _урок_с_квизом(self):
		frappe.set_user("Administrator")
		создать_квиз(self.урок, [создать_вопрос("Два?", варианты=[("2", True), ("3", False)])])
		frappe.set_user(self.ученик)
		return создать_занятие(self.ученик, self.урок)

	def test_квиз_не_начинается_с_пропущенной_целью(self):
		"""Иначе ученик получает вопрос по теме, которой на занятии не было."""
		занятие = self._урок_с_квизом()
		student.report_outcomes(
			занятие,
			outcomes=[
				{"objective": "Понимать цикл", "status": "covered"},
				{"objective": "Уметь читать код", "status": "skipped"},
			],
		)

		ответ = student.request_quiz(занятие)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ЦЕЛИ_ПРОПУЩЕНЫ)
		self.assertEqual(ответ["error"]["skipped"], ["Уметь читать код"])

	def test_разобранная_заново_цель_открывает_квиз(self):
		"""Отказ не тупик: отчёт замещается, и путь вперёд есть."""
		занятие = self._урок_с_квизом()
		student.report_outcomes(
			занятие,
			outcomes=[
				{"objective": "Понимать цикл", "status": "covered"},
				{"objective": "Уметь читать код", "status": "skipped"},
			],
		)

		сдать_отчёт(занятие)

		ответ = student.request_quiz(занятие)
		self.assertTrue(ответ["ok"])
		self.assertEqual(ответ["data"]["touched_objectives"], [])

	def test_задетая_вскользь_цель_квиз_не_блокирует_и_называется(self):
		"""`touched` квиз не закрывает, но агент узнаёт о таких целях из ответа."""
		занятие = self._урок_с_квизом()
		student.report_outcomes(
			занятие,
			outcomes=[
				{"objective": "Понимать цикл", "status": "covered"},
				{"objective": "Уметь читать код", "status": "touched"},
			],
		)

		ответ = student.request_quiz(занятие)

		self.assertTrue(ответ["ok"])
		self.assertTrue(ответ["data"]["question"])
		self.assertEqual(ответ["data"]["touched_objectives"], ["Уметь читать код"])

	def test_после_отчёта_урок_закрывается(self):
		занятие = создать_занятие(self.ученик, self.урок)
		сдать_отчёт(занятие)

		self.assertTrue(student.complete_lesson(занятие)["ok"])

	# --- сводка ---

	def test_сводка_считает_курсы_и_последние_занятия(self):
		создать_занятие(self.ученик, self.урок)
		сводка = student.get_my_progress()["data"]

		# Ровно один: «не меньше» замаскировало бы утечку чужих зачислений.
		self.assertEqual(сводка["courses_total"], 1)
		self.assertEqual(сводка["courses_overdue"], 0)
		self.assertEqual(сводка["recent_sessions"][0]["lesson"], self.урок)


class IntegrationTestCompleteLesson(IntegrationTestCase):
	"""Урок без квиза должен закрываться, урок с квизом — только квизом."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"cl-{суффикс}@example.com")
		self.теория = создать_урок(f"Теория {суффикс}")
		self.курс = зачислить(self.ученик, self.теория)
		# Второй урок того же курса, с квизом.
		глава = frappe.db.get_value("Course Lesson", self.теория, "chapter")
		self.практика = frappe.get_doc(
			{"doctype": "Course Lesson", "title": "Практика", "chapter": глава}
		).insert(ignore_permissions=True).name
		привязать_урок(глава, self.практика)
		вопрос = создать_вопрос("Два плюс два?", варианты=[("4", True), ("5", False)])
		создать_квиз(self.практика, [вопрос])
		frappe.set_user(self.ученик)

	def test_урок_без_квиза_закрывается_и_двигает_прогресс(self):
		"""Иначе ученик застревает на первом же теоретическом уроке.

		Директивы у этого урока нет, значит нет и целей: отчёт не требуется —
		требовать было бы нечего, а отказ загнал бы агента в тупик.
		"""
		занятие = создать_занятие(self.ученик, self.теория)

		ответ = student.complete_lesson(занятие)["data"]

		self.assertEqual(ответ["session_status"], "Completed")
		self.assertTrue(
			frappe.db.exists(
				"LMS Course Progress",
				{"member": self.ученик, "lesson": self.теория, "status": "Complete"},
			)
		)

	def test_после_закрытия_приходит_следующий_урок(self):
		занятие = создать_занятие(self.ученик, self.теория)
		student.complete_lesson(занятие)

		следующий = student.study_options()["data"]["recommended"]

		self.assertEqual(следующий["lesson"]["id"], self.практика)

	def test_урок_с_обязательным_квизом_так_не_закрыть(self):
		"""Несущее ограничение: иначе метод стал бы обходом проверки."""
		занятие = создать_занятие(self.ученик, self.практика)

		ответ = student.complete_lesson(занятие)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.НУЖЕН_КВИЗ)
		self.assertFalse(
			frappe.db.exists(
				"LMS Course Progress",
				{"member": self.ученик, "lesson": self.практика, "status": "Complete"},
			)
		)

	def test_курс_проходится_целиком(self):
		# Критерий готовности: оба урока закрыты, курс пройден.
		занятие = создать_занятие(self.ученик, self.теория)
		student.complete_lesson(занятие)

		квиз = student.request_quiz(создать_занятие(self.ученик, self.практика))["data"]
		student.submit_answer(квиз["attempt"], квиз["question"]["id"], "1", "слова ученика")

		курс = next(
			к for к in student.list_my_courses()["data"]["courses"] if к["id"] == self.курс
		)
		self.assertEqual(курс["progress"]["lessons_completed"], 2)
		self.assertEqual(курс["progress"]["lessons_total"], 2)
		self.assertIsNone(курс["next_lesson"])

	def test_брошенное_занятие_урок_не_закрывает(self):
		"""Иначе прогресс и журнал разъезжаются.

		До правки урок отмечался пройденным, событие писалось, а занятие
		оставалось брошенным — и отчёт руководителя показывал пройденный урок
		при брошенном занятии (lms-platform#195).

		Статус ставится прямо: в жизни его ставит фоновая задача по
		бездействию, ждать её в тесте нечем.
		"""
		занятие = создать_занятие(self.ученик, self.теория)
		frappe.db.set_value("Agent Learning Session", занятие, "status", "Abandoned")

		ответ = student.complete_lesson(занятие)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ЗАНЯТИЕ_ЗАКРЫТО)
		self.assertFalse(
			frappe.db.exists(
				"LMS Course Progress", {"member": self.ученик, "lesson": self.теория}
			),
			"прогресс по брошенному занятию не пишется",
		)
		self.assertFalse(
			frappe.db.exists(
				"Agent Session Event", {"session": занятие, "kind": "Verdict Returned"}
			),
			"вердикта по брошенному занятию в журнале быть не должно",
		)

	def test_чужое_занятие_закрыть_нельзя(self):
		frappe.set_user("Administrator")
		чужой = создать_ученика(f"cl-other-{frappe.generate_hash(length=6)}@example.com")
		зачислить(чужой, self.теория)
		чужое = создать_занятие(чужой, self.теория)
		frappe.set_user(self.ученик)

		ответ = student.complete_lesson(чужое)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["code"], student.ЧУЖОЕ_ЗАНЯТИЕ)


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


class IntegrationTestCourseOutline(IntegrationTestCase):
	"""Структура курса: агент должен уметь вернуться к пройденному."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"out-{суффикс}@example.com")
		self.первый = создать_урок(f"Первый {суффикс}")
		self.курс = зачислить(self.ученик, self.первый)
		глава = frappe.db.get_value("Course Lesson", self.первый, "chapter")
		self.второй = frappe.get_doc(
			{"doctype": "Course Lesson", "title": "Второй", "chapter": глава}
		).insert(ignore_permissions=True).name
		привязать_урок(глава, self.второй)
		frappe.set_user(self.ученик)

	def test_незакрытая_цель_переносится_на_следующий_урок(self):
		"""Агент следующего занятия должен знать, что осталось подобрать."""
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Agent Lesson Directive",
				"lesson": self.первый,
				"objectives": "Разобрать основу\nПосчитать сроки",
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)

		первое = создать_занятие(self.ученик, self.первый)
		student.report_outcomes(
			первое,
			outcomes=[
				{"objective": "Разобрать основу", "status": "covered"},
				{"objective": "Посчитать сроки", "status": "skipped"},
			],
		)
		student.complete_lesson(первое)

		перенос = student.student_context(создать_занятие(self.ученик, self.второй))["data"]["carried_over"]

		self.assertEqual(
			[(п["objective"], п["status"]) for п in перенос],
			[("Посчитать сроки", "skipped")],
			"разобранная цель переноситься не должна",
		)
		self.assertEqual(перенос[0]["lesson"], self.первый)

	def test_с_чего_продолжать_возвращается_в_перенесённых_целях(self):
		"""Мостик строится на «с чего продолжать», а не на «что пройдено»: верный
		конспект может разрушить педагогическую линию — вернуть объяснение,
		которое запутало (#238, решение 2А)."""
		перенос = self._перенос_после(
			[{"objective": "Посчитать сроки", "status": "touched", "resume_from": "  вернуться к буферу, а не к оценке  "}]
		)
		self.assertEqual(перенос[0]["resume_from"], "вернуться к буферу, а не к оценке")

	def test_resume_from_необязателен(self):
		перенос = self._перенос_после([{"objective": "Посчитать сроки", "status": "skipped"}])
		self.assertIsNone(перенос[0]["resume_from"])

	def test_resume_from_это_фраза_а_не_конспект(self):
		frappe.set_user("Administrator")
		frappe.get_doc(
			{"doctype": "Agent Lesson Directive", "lesson": self.первый, "objectives": "Посчитать сроки"}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)
		занятие = создать_занятие(self.ученик, self.первый)

		ответ = student.report_outcomes(
			занятие,
			outcomes=[{"objective": "Посчитать сроки", "status": "touched", "resume_from": "я" * 501}],
		)

		self.assertFalse(ответ["ok"])
		self.assertEqual(ответ["error"]["field"], "resume_from")

	def _перенос_после(self, строки: list[dict]) -> list[dict]:
		frappe.set_user("Administrator")
		frappe.get_doc(
			{"doctype": "Agent Lesson Directive", "lesson": self.первый, "objectives": "Посчитать сроки"}
		).insert(ignore_permissions=True)
		frappe.set_user(self.ученик)
		первое = создать_занятие(self.ученик, self.первый)
		student.report_outcomes(первое, outcomes=строки)
		student.complete_lesson(первое)
		return student.student_context(создать_занятие(self.ученик, self.второй))["data"]["carried_over"]

	def test_глубина_переноса_читается_из_настроек(self):
		"""С глубиной в один урок цель позапрошлого занятия уже не переносится."""
		from lms_frappe_app.tests.sample_data import политика_по_умолчанию

		self.addCleanup(политика_по_умолчанию)
		frappe.set_user("Administrator")
		глава = frappe.db.get_value("Course Lesson", self.первый, "chapter")
		третий = frappe.get_doc(
			{"doctype": "Course Lesson", "title": "Третий", "chapter": глава}
		).insert(ignore_permissions=True).name
		привязать_урок(глава, третий)
		занятия = ((self.первый, "Цель первого"), (self.второй, "Цель второго"))
		for урок, цель in занятия:
			frappe.get_doc(
				{"doctype": "Agent Lesson Directive", "lesson": урок, "objectives": цель}
			).insert(ignore_permissions=True)
		frappe.db.set_single_value("Agent Learning Settings", "carry_over_depth", 1)
		frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")
		frappe.set_user(self.ученик)
		for урок, цель in занятия:
			занятие = создать_занятие(self.ученик, урок)
			student.report_outcomes(занятие, outcomes=[{"objective": цель, "status": "skipped"}])
			student.complete_lesson(занятие)

		перенос = student.student_context(создать_занятие(self.ученик, третий))["data"]["carried_over"]

		self.assertEqual([п["objective"] for п in перенос], ["Цель второго"])

	def уроки(self):
		структура = student.course_outline(self.курс)["data"]
		return [урок for глава in структура["chapters"] for урок in глава["lessons"]]

	def test_структура_показывает_все_уроки_и_текущий(self):
		уроки = self.уроки()

		self.assertEqual([у["id"] for у in уроки], [self.первый, self.второй])
		self.assertTrue(уроки[0]["current"])
		self.assertFalse(уроки[0]["completed"])

	def test_после_прохождения_урок_помечен_пройденным(self):
		student.complete_lesson(создать_занятие(self.ученик, self.первый))

		уроки = self.уроки()

		self.assertTrue(уроки[0]["completed"])
		self.assertTrue(уроки[1]["current"], "текущим должен стать следующий урок")

	def test_повтор_пройденного_не_двигает_прогресс(self):
		# Ровно то, ради чего метод и нужен: идентификатор пройденного урока
		# больше неоткуда взять — list_my_courses отдаёт только следующий.
		student.complete_lesson(создать_занятие(self.ученик, self.первый))
		до = student.list_my_courses()["data"]["courses"][0]["progress"]

		пройденный = next(у["id"] for у in self.уроки() if у["completed"])
		self.assertEqual(пройденный, self.первый)
		student.complete_lesson(создать_занятие(self.ученик, пройденный))

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

	# --- готовность документа (learning-services#296) ---

	def test_отчёт_и_закрытие_предупреждают_о_пустом_блоке_урока(self):
		"""Предупреждение, а не отказ: урок закрывается и с пустым блоком."""
		self.схема(
			blocks=[
				{"block_key": "goal", "title": "Цель", "lesson": self.урок},
				{"block_key": "sponsor", "title": "Спонсор"},
			]
		)
		занятие = создать_занятие(self.ученик, self.урок)
		пустые = [{"artifact": "summary", "key": "goal", "title": "Цель"}]

		отчёт = student.report_outcomes(занятие, outcomes=[])["data"]
		закрытие = student.complete_lesson(занятие)["data"]

		self.assertEqual(отчёт["empty_blocks"], пустые, "блок без урока не в счёт")
		self.assertEqual(закрытие["empty_blocks"], пустые)
		self.assertEqual(закрытие["session_status"], "Completed")

	def test_заполненный_блок_урока_не_предупреждает(self):
		self.схема(blocks=[{"block_key": "goal", "title": "Цель", "lesson": self.урок}])
		student.update_artifact(self.курс, "summary", "goal", "Открыть кофейню")
		занятие = создать_занятие(self.ученик, self.урок)

		отчёт = student.report_outcomes(занятие, outcomes=[])["data"]

		self.assertEqual(отчёт["empty_blocks"], [])

	def test_прогресс_показывает_заполненность_документа(self):
		student.update_artifact(self.курс, "summary", "goal", "Открыть кофейню")

		курс = next(
			к for к in student.get_my_progress()["data"]["courses"] if к["id"] == self.курс
		)

		self.assertEqual(
			[(д["artifact"], д["blocks_total"], д["blocks_filled"]) for д in курс["documents"]],
			[("summary", 2, 1)],
		)
