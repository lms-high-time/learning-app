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

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from lms_frappe_app.tests.release_sample import добавить_главу, пример_релиза, релиз_двух_целей
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	курс_из_релиза,
	занятие_релиза,
	отметить_все_пункты,
	урок_релиза,
	зачислить,
	зачислить_на_курс,
	привязать_главу,
	привязать_урок,
	политика_по_умолчанию,
	создать_домашку,
	создать_занятие,
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_урок,
	создать_ученика,
)
from lms_frappe_app.agent_learning import release_quiz
from lms_frappe_app.agent_learning.runs import service as прохождения
from lms_frappe_app.api import authoring, manager, review, student, team
from lms_frappe_app.testing import сколько_запросов

#: Сколько обращений к базе делает метод на данных этого модуля. Меняется
#: вместе с кодом и только осознанно — см. пояснение модуля.
БЮДЖЕТ = {
	# Курс из релиза (learning-services#506): доступ — четыре выборки,
	# действующий релиз, главы, уроки, цели и разделы релиза, статусы уроков и
	# целей прохождений ученика — по одной на курс, программы курса — одна.
	"course_outline": 12,
	# Курс из релиза (learning-services#506), прохождение уже есть: урок
	# релиза по записи, действующий релиз ещё раз при сверке прохождения,
	# прохождение с блокировкой и двумя таблицами строк, есть ли вопросы,
	# рамка, срез урока, глава, история прошлых уроков — по одной выборке;
	# тексты целей — из строк прохождения (learning-services#514); занятие,
	# событие «пакет выдан», начало занятия, итоги репортов, блоки документа,
	# попытки, сигналы — по одной выборке. У курса образца есть задание, поэтому
	# домашка читает порядок уроков.
	"start_lesson": 50,
	# Квиз урока из релиза (learning-services#506), ответ посреди попытки:
	# владелец, релиз и курс попытки; попытка с блокировкой; доступ к курсу —
	# четыре выборки; действующий релиз курса — попытка не из гонки с
	# публикацией (learning-services#514); отвечен ли вопрос, эталон из
	# релиза; вставка ответа в точке сохранения (три); журнал проверки — об
	# ответе и о выданном следом вопросе; отвеченные с блокировкой.
	"submit_answer": 15,
	# Обычная отметка пункта (learning-services#506): занятие, релиз и ключ
	# урока; доступ к курсу — четыре выборки; есть ли вопросы и
	# политика квиза — четыре; прохождение с блокировкой и двумя таблицами
	# строк, релиз при сверке, занятие пункта; прежняя версия прохождения для
	# `track_changes` (три), ссылка на занятие и сохранение (семь);
	# активность занятия. Сигнал без разобранной цели ничего не читает.
	"mark_goal": 28,
	# Покрытие целей — из прохождений (learning-services#506): прохождения
	# ученика и цели нужных прохождений с их текстами — две выборки на всю
	# выдачу вместо одной выборки отметок занятий.
	"student_detail": 13,
	# Домашки ученика (learning-services#439), три сдачи в двух курсах: сдачи,
	# уроки, задания и комментарии — по одной выборке, курсы ученика — раз,
	# порядок уроков ради адресов — раз на курс, а не на сдачу.
	"my_homework": 23,
	# `start_lesson` на курсе с заданиями у текущего и прошлого урока: сверх
	# `start_lesson` — поиск и чтение сдачи прошлого урока.
	"start_lesson_homework": 55,
	# Очередь куратора (learning-services#452), три сдачи в двух курсах:
	# сдачи, счёт, значения фильтров, уроки с курсом, названия курсов,
	# организаций и заданий, имена учеников, проверенные версии — по одной
	# выборке; адресов уроков в очереди нет — порядка глав не читает.
	"review_queue": 10,
	# Назначения руководителя (learning-services#452), два курса с заданиями:
	# задания с названиями уроков — одной выборкой, порядок уроков — пятью
	# на все курсы (какие из них по релизу — тоже выборкой, не кэшем курса),
	# сроки назначений — одной, названия неопубликованных курсов — одной, а не
	# запросом на назначение.
	"allocations": 14,
	# Просмотр релиза автором (learning-services#512): курс, запись релиза,
	# главы, уроки, цели, пункты, вопросы, разделы документа и схема документа
	# — по одной выборке на релиз, а не на урок.
	"course_release": 9,
	# Урок релиза: курс, запись релиза, урок, его цели, пункты и вопросы, срез
	# пакета агента и рамка.
	"course_release_lesson": 8,
	# Заметки по ключам релиза (learning-services#512): курс, заметки с ключами
	# уроков (learning-services#514), ответы, версии релизов курса, действующий
	# релиз; уроки, срезы пакета нужных уроков, главы, разделы, рамка, цели,
	# пункты и вопросы — по выборке на релиз, а не на заметку. Узлы карты — из
	# кэша по дайджесту релиза: дайджест — выборка на релиз.
	"list_notes": 14,
	# Репорты (learning-services#512): курс и репорты страницы с ключами их
	# уроков — соединением с записью урока (learning-services#514), а не
	# выборкой на релиз или строку. Редакций директив у репортов курса из
	# релиза нет — их выборки тоже.
	"course_reports": 2,
	# Список курсов кабинета автора (learning-services#512): `list_courses`
	# (курсы, число уроков и какие курсы по релизу, действующие релизы),
	# открытые заметки и ответы в их нитях, тестеры — по выборке на весь
	# список, а не на курс; кэш документов курсов на замере холодный.
	"author_courses": 10,
	# Прохождения курса автору (learning-services#512): курс, инструктор ли
	# вызвавший, прохождения страницы с именами учеников и названиями записей
	# уроков — одной выборкой, цели и пункты страницы — по одной, а не на
	# прохождение и не на релиз.
	"goal_runs": 5,
	# Новая версия курса из релиза, правка названия урока (learning-services#514):
	# курс по ключу, его блокировка, действующий релиз и дайджест; записи
	# действующего релиза без ключа — одна выборка; под точкой сохранения —
	# действующий релиз и его документ, ключи на записях курса (две) и ключи
	# прежнего релиза (две); проекция — главы с их порядком, правленый урок,
	# курс с таблицами строк; шаблоны домашек, схема документа; номер версии,
	# серия, вставка релиза и строк индекса — по вставке на строку; карточка
	# курса; после точки — сверка прохождений и ответ. Открытые попытки курса с
	# блокировкой — одна выборка и при их отсутствии (learning-services#514).
	# Освобождение прежней версии (learning-services#514): отбор неосвобождённых,
	# запись в релизы и удаление строк — по запросу на таблицу индекса, а не на
	# версию или строку.
	"publish_release": 96,
	# Та же публикация при двух открытых попытках (learning-services#514), одна
	# переносится, другая аннулируется: сверх `publish_release` — релизы
	# попыток со снимком и их вопросы (по выборке на все попытки), запись
	# переноса и запись аннулирования (по одной на вид, а не на попытку),
	# событие журнала на попытку (вставка и две проверки ссылок), а после
	# точки — сверка двух прохождений. Освобождение сбрасывает кэш значений
	# релизов, и сверка проверяет ссылку прохождения на релиз запросом.
	"publish_release_attempts": 143,
	# Новая версия снимает главу с уроком, по которому есть занятие
	# (learning-services#514): оба остаются. Сверх `publish_release` — порядок
	# глав курса и снятая глава; на каждую снятую запись — блокировка, чтение
	# записи и проверка ссылок, по запросу на поле Link сайта, ссылающееся на
	# её доктайп. Проверяются только записи, снятые этой версией.
	"publish_release_removed": 143,
	# Новая попытка квиза (learning-services#514): занятие, релиз и ключ урока,
	# доступ — четыре выборки; прохождение с блокировкой и сверкой; проверка
	# занятия и доступ ещё раз; занятие с блокировкой, действующий релиз,
	# открытая попытка занятия; урок и вопросы релиза; лимит и пауза (политика
	# — три, счёт, он же номер, последняя завершённая); последняя попытка урока —
	# не аннулирована ли без сообщения агенту (learning-services#523); вставка
	# попытки с порогом; занятие в «ждёт квиз», событие занятия; выданный
	# вопрос в журнал проверки.
	"request_quiz": 38,
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

		self.менеджер = создать_менеджера(f"qbm-{суффикс}@example.com", self.организация)

		frappe.set_user(self.ученик)
		# Документы заполняются от имени ученика: документ, заведённый
		# администратором, к ученику не относится, и бюджет прошёл бы мимо
		# чтения содержимого.
		self._артефакты(суффикс)

	# --- ворота ---

	def test_бюджет_course_outline_не_растёт_с_курсом(self):
		"""Два курса из релиза — три урока в двух главах и шесть в трёх, у ученика
		прохождения двух и четырёх уроков: бюджет один на оба."""
		малый = frappe.db.get_value("Course Lesson", self._курс_релиза()[0], "course")
		большой = frappe.db.get_value("Course Lesson", self._курс_релиза(глав_больше=True)[0], "course")
		for курс, ключи in ((малый, ("l-1", "l-2")), (большой, ("l-1", "l-2", "l-4", "l-5"))):
			for ключ in ключи:
				run = прохождения.прохождение(self.ученик, курс, ключ)
				прохождения.отметить(run.name, "term:T1", "done", "Назвал термин")
			with self.subTest(курс=курс):
				self._ворота("course_outline", lambda курс=курс: student.course_outline(курс))

	def test_бюджет_mark_goal(self):
		"""Обычная отметка: цель пункта не разобрана — сигналу нечего читать."""
		урок = self._курс_релиза()[0]
		занятие = student.start_lesson(lesson=урок)["data"]["session"]
		self._ворота("mark_goal", lambda: student.mark_goal(занятие, "refute:M1", "done", "Не проявилось"))

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
		student.submit_answer(попытка, "S1/l-1-D1", "V1", "слова ученика")
		self._ворота(
			"submit_answer",
			lambda: student.submit_answer(попытка, "S2/l-1-D1", "V1", "слова ученика"),
			прогреть=False,
		)

	def test_бюджет_request_quiz(self):
		"""Новая попытка по свежему занятию. Прогрев — попытка по соседнему занятию
		того же урока: продолжение открытой попытки — другой путь."""
		попытка = self._попытка()
		курс = frappe.db.get_value("Agent Quiz Attempt", попытка, "course")
		frappe.set_user(self.ученик)
		занятие = занятие_релиза(self.ученик, курс, "l-1")
		self._ворота(
			"request_quiz",
			lambda: self.assertTrue(student.request_quiz(занятие)["ok"]),
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
		"""Два занятия урока курса из релиза — со своим прохождением и без него."""
		урок = self._курс_релиза()[0]
		курс = frappe.db.get_value("Course Lesson", урок, "course")
		run = прохождения.прохождение(self.ученик, курс, "l-1")
		создать_занятие(self.ученик, урок, run=run.name)
		создать_занятие(self.ученик, урок)
		frappe.set_user(self.менеджер)
		self._ворота("student_detail", lambda: manager.student_detail(self.ученик))

	def test_бюджет_course_release_не_растёт_с_курсом(self):
		"""Релизы из трёх уроков в двух главах и из шести в трёх: бюджет один на оба."""
		frappe.set_user("Administrator")
		куратор = создать_куратора(f"qbc-{frappe.generate_hash(length=6)}@example.com")
		малый = frappe.db.get_value("Course Lesson", self._курс_релиза()[0], "course")
		большой = frappe.db.get_value("Course Lesson", self._курс_релиза(глав_больше=True)[0], "course")
		frappe.set_user(куратор)
		for курс in (малый, большой):
			with self.subTest(курс=курс):
				self._ворота("course_release", lambda курс=курс: authoring.course_release(курс))
				self._ворота(
					"course_release_lesson", lambda курс=курс: authoring.course_release(курс, lesson="l-2")
				)

	def test_бюджет_list_notes_не_растёт_с_заметками(self):
		"""Заметки на всех видах мест: по одной на вид — и вдвое больше, на двух уроках."""
		frappe.set_user("Administrator")
		куратор = создать_куратора(f"qbn-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(куратор)
		for уроков in (("l-1",), ("l-1", "l-2")):
			релиз = пример_релиза(f"qbn-{frappe.generate_hash(length=8)}")
			релиз["map"] = {"goals": {"G1": {"text": "Цель"}, "G2": {"text": "Вторая"}}}
			курс = authoring.publish_release(release=релиз)["data"]["course"]
			места = ["course", "chapter.ch-1", "section.log", "agent.frame", "map.G1", "map.G2"]
			for урок in уроков:
				места += [
					f"lesson.{урок}",
					f"objective.{урок}-D1",
					f"goal.{урок}/term:T1",
					f"question.S1/{урок}-D1",
					f"agent.lesson.{урок}",
					f"agent.item.{урок}/term:T1",
				]
			for место in места:
				заметка = authoring.add_note(course=курс, target=место, text="Заметка")["data"]["id"]
				authoring.reply_note(note=заметка, text="Ответ", via="agent")
			with self.subTest(уроков=len(уроков)):
				self._ворота("list_notes", lambda курс=курс: authoring.list_notes(course=курс))
				self.assertEqual(len(authoring.list_notes(course=курс)["data"]["notes"]), len(места))

	def test_бюджет_course_reports_не_растёт_с_релизами(self):
		"""Страница репортов одного релиза и трёх: ключи уроков — в выборке репортов."""
		frappe.set_user("Administrator")
		куратор = создать_куратора(f"qbr-{frappe.generate_hash(length=6)}@example.com")
		for релизов in (1, 3):
			ключ = f"qbr-{frappe.generate_hash(length=8)}"
			for номер in range(релизов):
				релиз = пример_релиза(ключ)
				релиз["course"]["summary"] = f"Версия {номер + 1}"
				курс, _ = курс_из_релиза(релиз=релиз)
				for урок in ("l-1", "l-2"):
					self._репорт(курс, урок)
			frappe.set_user(куратор)
			with self.subTest(релизов=релизов):
				self._ворота("course_reports", lambda курс=курс: authoring.course_reports(course=курс))
				self.assertEqual(len(authoring.course_reports(course=курс)["data"]["reports"]), релизов * 2)
			frappe.set_user("Administrator")

	def test_бюджет_списка_курсов_кабинета_не_растёт_с_курсами(self):
		"""Страница списка кабинета до и после ещё трёх курсов из релиза с
		заметками и тестерами: бюджет один на оба. Замер — на холодном кэше
		документов курсов: прогрев не должен прятать запрос на курс."""
		from lms_frappe_app.www.author import сведения

		frappe.set_user("Administrator")
		куратор = создать_куратора(f"qba-{frappe.generate_hash(length=6)}@example.com")
		for курсов in (1, 3):
			for _ in range(курсов):
				курс, _ = курс_из_релиза()
				frappe.set_user(куратор)
				заметка = authoring.add_note(course=курс, target="lesson.l-1", text="Заметка")["data"]["id"]
				authoring.reply_note(note=заметка, text="Ответ", via="agent")
				frappe.set_user("Administrator")
				ученик = создать_ученика(f"qba-{frappe.generate_hash(length=6)}@example.com")
				зачислить_на_курс(ученик, курс)
				frappe.db.set_value("LMS Enrollment", {"course": курс, "member": ученик}, "agent_tester", 1)
			frappe.set_user(куратор)
			with self.subTest(курсов=курсов):
				self._ворота(
					"author_courses",
					lambda: сведения(куратор),
					остудить=lambda: frappe.clear_document_cache("LMS Course"),
				)
				курсы = {к["id"]: к for к in сведения(куратор)["courses"]}
				self.assertEqual((курсы[курс]["testers_count"], курсы[курс]["open_notes"]), (1, 1))
			frappe.set_user("Administrator")

	def test_бюджет_goal_runs_не_растёт_со_страницей_и_релизами(self):
		"""Страница из одного прохождения одного релиза и из четырёх прохождений
		двух релизов: бюджет один на обе."""
		frappe.set_user("Administrator")
		куратор = создать_куратора(f"qbg-{frappe.generate_hash(length=6)}@example.com")
		ключ = f"qbg-{frappe.generate_hash(length=8)}"
		курс, _ = курс_из_релиза(куратор, релиз=пример_релиза(ключ))
		run = прохождения.прохождение(self.ученик, курс, "l-1")
		прохождения.отметить(run.name, "term:T1", "done", "Назвал термин")
		frappe.set_user(куратор)
		with self.subTest(прохождений=1, релизов=1):
			self._ворота("goal_runs", lambda: authoring.goal_runs(course=курс, limit=1))

		релиз = пример_релиза(ключ)
		релиз["lessons"][0]["title"] = "Урок первый, второе издание"
		# Без фоновой сверки: прошлое прохождение остаётся на первом релизе.
		with patch.object(frappe, "enqueue"):
			курс_из_релиза(куратор, релиз=релиз)
		frappe.set_user("Administrator")
		for номер, ключ_урока in enumerate(("l-1", "l-2", "l-3")):
			ученик = создать_ученика(f"qbg-{номер}-{frappe.generate_hash(length=6)}@example.com")
			run = прохождения.прохождение(ученик, курс, ключ_урока)
			прохождения.отметить(run.name, "term:T1", "done", "Назвал термин")
		self.assertEqual(
			len(set(frappe.get_all("Agent Lesson Run", filters={"course": курс}, pluck="release"))), 2
		)
		frappe.set_user(куратор)
		with self.subTest(прохождений=4, релизов=2):
			self._ворота("goal_runs", lambda: authoring.goal_runs(course=курс))

	def test_бюджет_publish_release(self):
		"""Новая версия курса образца — правка названия урока. Прогрев — такая же
		правка версией раньше: повтор того же релиза вернул бы `unchanged` и не
		мерил бы запись."""
		frappe.set_user("Administrator")
		куратор = создать_куратора(f"qbp-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(куратор)
		ключ = f"qbp-{frappe.generate_hash(length=8)}"
		релиз = пример_релиза(ключ)
		курс = authoring.publish_release(release=релиз)["data"]["course"]
		издания = iter(range(2, 4))

		def новая_версия():
			релиз["lessons"][0]["title"] = f"Урок первый, издание {next(издания)}"
			ответ = authoring.publish_release(release=релиз)
			self.assertEqual(ответ["data"]["lessons"]["updated"], ["l-1"], ответ)

		self._ворота("publish_release", новая_версия)
		действующий = frappe.db.get_value("LMS Course", курс, "active_release")
		self.assertEqual(frappe.db.get_value("Agent Course Release", действующий, "version"), 3)

	def test_бюджет_publish_release_с_открытыми_попытками(self):
		"""Новая версия курса образца при двух открытых попытках: квиз `l-1` тот же —
		перенос, у `l-2` правлен вопрос — аннулирование. Прогрев — версия, которая
		переносит обе."""
		frappe.set_user("Administrator")
		куратор = создать_куратора(f"qbq-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(куратор)
		релиз = пример_релиза(f"qbq-{frappe.generate_hash(length=8)}")
		курс = authoring.publish_release(release=релиз)["data"]["course"]
		frappe.set_user("Administrator")
		зачислить_на_курс(self.ученик, курс)
		попытки = []
		for ключ in ("l-1", "l-2"):
			занятие = занятие_релиза(self.ученик, курс, ключ)
			run = frappe.get_doc(
				прохождения.ПРОХОЖДЕНИЕ, frappe.db.get_value("Agent Learning Session", занятие, "run")
			)
			попытки.append(release_quiz.начать(run, занятие)["attempt"])
		frappe.set_user(куратор)
		издания = iter(range(2, 4))

		def новая_версия():
			издание = next(издания)
			релиз["lessons"][0]["title"] = f"Урок первый, издание {издание}"
			if издание == 3:
				релиз["lessons"][1]["quiz"]["questions"][0]["text"] = "Другая ситуация"
			ответ = authoring.publish_release(release=релиз)
			self.assertTrue(ответ["ok"], ответ)

		self._ворота("publish_release_attempts", новая_версия)
		self.assertEqual(
			[frappe.db.get_value("Agent Quiz Attempt", п, "status") for п in попытки],
			["In Progress", "Cancelled"],
		)

	def test_бюджет_publish_release_со_снятыми_со_ссылками(self):
		"""Новая версия курса образца снимает главу с уроком, по которому есть
		занятие: урок и глава остаются. Прогрев — такая же версия раньше, с
		другой главой."""
		frappe.set_user("Administrator")
		куратор = создать_куратора(f"qbr-{frappe.generate_hash(length=6)}@example.com")
		frappe.set_user(куратор)
		релиз = пример_релиза(f"qbr-{frappe.generate_hash(length=8)}")
		добавить_главу(добавить_главу(релиз, "ch-3", ["l-4"]), "ch-4", ["l-5"])
		курс = authoring.publish_release(release=релиз)["data"]["course"]
		уроки = [урок_релиза(курс, ключ) for ключ in ("l-4", "l-5")]
		frappe.set_user("Administrator")
		for урок in уроки:
			создать_занятие(self.ученик, урок)
		frappe.set_user(куратор)
		снимаемые = iter(("ch-3", "ch-4"))

		def новая_версия():
			глава = next(снимаемые)
			[урок] = next(г for г in релиз["chapters"] if г["key"] == глава)["lessons"]
			релиз["chapters"] = [г for г in релиз["chapters"] if г["key"] != глава]
			релиз["lessons"] = [у for у in релиз["lessons"] if у["key"] != урок]
			del релиз["agent"]["lessons"][урок]
			ответ = authoring.publish_release(release=релиз)
			self.assertEqual(
				(ответ["data"]["chapters"]["removed"], ответ["data"]["lessons"]["removed"]),
				([глава], [урок]),
				ответ,
			)

		self._ворота("publish_release_removed", новая_версия)
		self.assertEqual([bool(frappe.db.exists("Course Lesson", урок)) for урок in уроки], [True, True])

	# --- механика ворот ---

	def _старт_с_прогревом(self, метод: str, урок: str) -> None:
		прогрев = student.start_lesson(lesson=урок)
		self.assertTrue(прогрев["ok"], прогрев)
		frappe.db.set_value("Agent Learning Session", прогрев["data"]["session"], "status", "Abandoned")
		self._ворота(метод, lambda: student.start_lesson(lesson=урок), прогреть=False)

	def _ворота(self, метод: str, вызов, *, прогреть: bool = True, остудить=None) -> None:
		"""`остудить` — что сбросить после прогрева: кэш, который метод не
		должен считать прогретым."""
		if прогреть:
			вызов()
		if остудить:
			остудить()
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

	def _курс_релиза(self, *, с_домашкой: bool = False, глав_больше: bool = False) -> tuple[str, str]:
		"""Курс из релиза той же организации — две главы, три урока, документ с
		блоками первых двух уроков, заполненный учеником; первый и второй уроки.

		`start_lesson` открывает только курс из релиза (learning-services#506).
		С `с_домашкой` задания есть у первых двух уроков; с `глав_больше` — ещё
		третья глава с тремя уроками по образцу первого (`l-4`…`l-6`).
		"""
		frappe.set_user("Administrator")
		релиз = пример_релиза(f"qb-{frappe.generate_hash(length=8)}")
		if глав_больше:
			новые = [f"l-{номер}" for номер in (4, 5, 6)]
			релиз["chapters"].append(
				{"key": "ch-3", "title": "Глава третья", "description": "Что изменится.", "lessons": новые}
			)
			for номер, ключ in enumerate(новые, start=4):
				урок = json.loads(json.dumps(релиз["lessons"][0], ensure_ascii=False).replace("l-1", ключ))
				урок.update(chapter="ch-3", title=f"Урок {номер}")
				релиз["lessons"].append(урок)
		if с_домашкой:
			for урок in релиз["lessons"][:2]:
				урок["homework"] = {"title": "Задание", "description": "Сделайте пример.", "answer_mode": "text", "due_days": None}
		курс, _ = курс_из_релиза(релиз=релиз)
		self._назначить(курс)
		frappe.set_user(self.ученик)
		student.update_artifact(курс, "notebook", "log", rows=[{"topic": "Первая встреча"}])
		return урок_релиза(курс, "l-1"), урок_релиза(курс, "l-2")

	def _репорт(self, курс: str, ключ: str) -> None:
		"""Репорт по уроку действующего релиза курса — с этим релизом."""
		урок = урок_релиза(курс, ключ)
		frappe.get_doc(
			{
				"doctype": "Agent Course Report",
				"session": создать_занятие(self.ученик, урок),
				"course": курс,
				"lesson": урок,
				"release": frappe.db.get_value("LMS Course", курс, "active_release"),
				"kind": "Stuck",
				"text": "Встал",
			}
		).insert(ignore_permissions=True)

	def _попытка(self) -> str:
		"""Попытка квиза урока из релиза с четырьмя вопросами; ответ меряется посреди попытки."""
		frappe.set_user("Administrator")
		курс, _ = курс_из_релиза(релиз=релиз_двух_целей(f"qb-quiz-{frappe.generate_hash(length=8)}", вопросов=4))
		self._назначить(курс)
		frappe.set_user(self.ученик)
		занятие = занятие_релиза(self.ученик, курс, "l-1")
		run = frappe.db.get_value("Agent Learning Session", занятие, "run")
		отметить_все_пункты(run)
		return student.request_quiz(занятие)["data"]["attempt"]
