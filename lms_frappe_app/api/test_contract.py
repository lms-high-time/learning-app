# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Ворота на контракт: состав методов и ключи ответов.

Первые ворота — состав: множество whitelisted-методов `api/` равно множеству
разделов `CONTRACT.md`. Пропуск в любую сторону валит прогон, и сообщение
называет метод поимённо.

Вторые — ключи: пример ответа в разделе разбирается как JSON, и его поля
верхнего уровня сверяются с настоящим ответом метода на фикстуре.

`Why:` контракт отстал от кода на дюжину методов, и заметили это только при
сверке руками. Ворота стоят там же, где возникает дрейф: в PR этого
приложения.

Параметры и списки отказов остаются на ревью: их не из чего вывести, не
переписав контракт в схему.
"""

import importlib
import json
import pkgutil
import re
import unittest
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase

import lms_frappe_app.api
from lms_frappe_app.agent_learning.releases import schema as схема_релиза
from lms_frappe_app.agent_learning.releases import service as релизы
from lms_frappe_app.api import authoring, manager, public, review, student
from lms_frappe_app.tests.release_sample import пример_релиза
from lms_frappe_app.tests.sample_data import (
	добавить_в_организацию,
	политика_по_умолчанию,
	создать_куратора,
	создать_менеджера,
	создать_организацию,
	создать_ученика,
	урок_релиза,
	зачислить_на_курс,
	привязать_главу,
	привязать_урок,
	создать_домашку,
	схема_документа,
)

#: Документ контракта лежит в корне репозитория, рядом с README и CONTRIBUTING.
КОНТРАКТ = Path(__file__).resolve().parents[2] / "CONTRACT.md"

#: Раздел метода — заголовок второго уровня с полным именем в обратных кавычках:
#: `## \`lms_frappe_app.api.student.start_lesson\``. Прочие `##` — это проза
#: контракта (транспорт, формат ответа, версионирование), и методов в них нет.
ИМЯ_МЕТОДА = re.compile(r"`(lms_frappe_app\.api\.[a-z_]+\.[a-z_]+)`")


def методы_кода() -> set[str]:
	"""Полные имена whitelisted-методов всех модулей `api/`."""
	return set(функции_кода())


def функции_кода() -> dict:
	"""Whitelisted-методы всех модулей `api/`: полное имя → функция.

	Берётся реестр Frappe, а не разбор текста: whitelisted метод — тот, что
	лежит в `frappe.whitelisted` после импорта модуля, и именно он доступен
	снаружи. Отбор по `__module__` отсекает методы, импортированные в модуль
	из соседнего: считать их дважды нельзя.
	"""
	найденные = {}
	for модуль in _модули_api():
		for имя, значение in vars(модуль).items():
			if not callable(значение) or значение not in frappe.whitelisted:
				continue
			if getattr(значение, "__module__", None) != модуль.__name__:
				continue
			найденные[f"{модуль.__name__}.{имя}"] = значение
	return найденные


def _модули_api() -> list:
	"""Модули пакета `api`, кроме тестовых."""
	модули = []
	for сведения in pkgutil.iter_modules(lms_frappe_app.api.__path__):
		if сведения.name.startswith("test_"):
			continue
		модули.append(importlib.import_module(f"lms_frappe_app.api.{сведения.name}"))
	return модули


def заголовки_контракта() -> list[list[str]]:
	"""Имена методов из каждого заголовка-раздела, в порядке документа.

	Список на заголовок, а не плоский: по нему видно и повторы, и заголовок,
	склеивший два метода в один раздел.
	"""
	текст = КОНТРАКТ.read_text(encoding="utf-8")
	разделы = []
	for строка in текст.splitlines():
		if not строка.startswith("## "):
			continue
		имена = ИМЯ_МЕТОДА.findall(строка)
		if имена:
			разделы.append(имена)
	return разделы


class IntegrationTestContractCoverage(IntegrationTestCase):
	"""Состав контракта совпадает с составом методов."""

	def setUp(self):
		self.assertTrue(
			КОНТРАКТ.exists(),
			f"Документа контракта нет на месте: {КОНТРАКТ}",
		)
		self.в_коде = методы_кода()
		self.разделы = заголовки_контракта()
		self.в_контракте = [имя for раздел in self.разделы for имя in раздел]

	def test_каждый_метод_описан_в_контракте(self):
		пропущены = sorted(self.в_коде - set(self.в_контракте))
		self.assertFalse(
			пропущены,
			"Методы есть в api/, но не описаны в CONTRACT.md — заведите раздел "
			"`## `<полное имя>`` с параметрами, ответом и отказами:\n"
			+ "\n".join(пропущены),
		)

	def test_контракт_не_описывает_несуществующих_методов(self):
		лишние = sorted(set(self.в_контракте) - self.в_коде)
		self.assertFalse(
			лишние,
			"Разделы CONTRACT.md описывают методы, которых нет в api/ — "
			"уберите раздел или верните метод:\n" + "\n".join(лишние),
		)

	def test_у_метода_ровно_один_раздел(self):
		"""Раздел на метод: склейка двух методов в один заголовок оставляет
		второй без параметров, ответа и отказов — так однажды и потерялось
		описание трёх методов."""
		склеенные = [раздел for раздел in self.разделы if len(раздел) > 1]
		self.assertFalse(
			склеенные,
			"Заголовок описывает сразу несколько методов — разнесите по разделам:\n"
			+ "\n".join(" · ".join(раздел) for раздел in склеенные),
		)

		повторы = sorted(
			{имя for имя in self.в_контракте if self.в_контракте.count(имя) > 1}
		)
		self.assertFalse(
			повторы,
			"У метода больше одного раздела — две копии описания разъедутся:\n"
			+ "\n".join(повторы),
		)


#: Пометка в разделе метода, которого зовёт страница, а не агент. Остальным
#: контракт обещает POST — раздел «Транспорт».
ТОЛЬКО_GET = "Только `GET`"


def методы_только_get() -> set[str]:
	"""Методы, в разделе которых стоит пометка «Только `GET`»."""
	найденные = set()
	метод = None
	for строка in КОНТРАКТ.read_text(encoding="utf-8").splitlines():
		if строка.startswith("## "):
			имена = ИМЯ_МЕТОДА.findall(строка)
			метод = имена[0] if имена else None
		elif метод and ТОЛЬКО_GET in строка:
			найденные.add(метод)
	return найденные


class IntegrationTestContractTransport(IntegrationTestCase):
	"""Метод принимает тот глагол, который обещает контракт.

	MCP-клиент зовёт Frappe только POST. Метод с `methods=["GET"]` отвечает
	на него 403, и агент говорит куратору «нет доступа к этим данным» — так
	`course_reports` с появления не отдал ни одного репорта
	(lms-high-time/learning-services#281). Глагол не видит ни один слой:
	здесь метод зовут из Python, в тестах MCP клиент подменён.
	"""

	def setUp(self):
		self.функции = функции_кода()
		self.только_get = методы_только_get()

	def глаголы(self, имя: str) -> set[str]:
		return set(frappe.allowed_http_methods_for_whitelisted_func[self.функции[имя]])

	def test_метод_принимает_post(self):
		без_post = sorted(
			имя
			for имя in self.функции
			if имя not in self.только_get and "POST" not in self.глаголы(имя)
		)
		self.assertFalse(
			без_post,
			"Метод не принимает POST, а MCP зовёт только им — агент получит 403. "
			"Уберите ограничение глагола или, если метод зовёт страница, а не "
			f"агент, пометьте его раздел CONTRACT.md «{ТОЛЬКО_GET}»:\n" + "\n".join(без_post),
		)

	def test_пометка_только_get_правдива(self):
		расходятся = sorted(
			имя
			for имя in self.только_get
			if имя in self.функции and self.глаголы(имя) != {"GET"}
		)
		self.assertFalse(
			расходятся,
			f"Раздел помечен «{ТОЛЬКО_GET}», а метод принимает и другие глаголы — "
			"поправьте пометку или декоратор:\n" + "\n".join(расходятся),
		)


def примеры_ответов(успех: bool = True) -> dict[str, list[frozenset]]:
	"""Ключи `data` из примеров успешного ответа — по методам; с `успех=False` —
	ключи `error` из примеров отказа.

	Примером считается блок ```json с `"ok": true` (`"ok": false`): в разделах
	есть и блоки параметров (`questions`, `blocks`), и их сверять не с чем.
	Примеров у метода бывает несколько — `artifact` отвечает по-разному на
	перечень и на один документ, `submit_answer` — на ход и на конец попытки;
	подходит любой.
	"""
	признак, часть = ('"ok": true', "data") if успех else ('"ok": false', "error")
	строки = КОНТРАКТ.read_text(encoding="utf-8").splitlines()
	примеры: dict[str, list[frozenset]] = {}
	метод = None
	номер = 0
	while номер < len(строки):
		строка = строки[номер]
		if строка.startswith("## "):
			имена = ИМЯ_МЕТОДА.findall(строка)
			метод = имена[0] if имена else None
		elif строка.strip() == "```json" and метод:
			конец = номер + 1
			while конец < len(строки) and строки[конец].strip() != "```":
				конец += 1
			блок = "\n".join(строки[номер + 1 : конец])
			номер = конец
			if признак in блок:
				данные = json.loads(блок).get(часть)
				if isinstance(данные, dict):
					примеры.setdefault(метод, []).append(frozenset(данные))
		номер += 1
	return примеры


class IntegrationTestContractExamples(IntegrationTestCase):
	"""Пример ответа в контракте — те же ключи, что у настоящего ответа.

	Ворота на состав ловят метод без раздела; эти — раздел, разошедшийся с
	методом внутри. Сверяются ключи верхнего уровня `data`: вложенные
	структуры в примерах намеренно сокращены многоточием, и требовать от них
	полноты значит писать в контракте схему вместо примера.

	Полноты обхода здесь нет: метод, который дорого позвать на фикстуре,
	остаётся на ревью — его раздел всё равно обязан существовать.
	"""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		политика_по_умолчанию()
		self.примеры = примеры_ответов()
		self.примеры_отказов = примеры_ответов(успех=False)
		суффикс = frappe.generate_hash(length=6)
		self.куратор = создать_куратора(f"contract-{суффикс}@example.com")
		self.ученик = создать_ученика(f"contract-pupil-{суффикс}@example.com")
		self.организация = создать_организацию(f"Контракт {суффикс}")
		добавить_в_организацию(self.ученик, self.организация)
		self.менеджер = создать_менеджера(f"contract-mg-{суффикс}@example.com", self.организация)
		self.суффикс = суффикс

	def сверить(self, метод: str, ответ: dict) -> dict:
		"""Ключи ответа против примеров раздела. Возвращает `data` вызывающему."""
		полное = f"lms_frappe_app.api.{метод}"
		self.assertNotIn("error", ответ, f"{метод} отказал: {ответ.get('error')}")
		ожидаемые = self.примеры.get(полное)
		self.assertTrue(ожидаемые, f"В CONTRACT.md нет примера ответа для {метод}")
		настоящие = frozenset(ответ["data"])
		if настоящие in ожидаемые:
			return ответ["data"]

		ближний = min(ожидаемые, key=lambda пример: len(пример ^ настоящие))
		self.fail(
			f"Ключи ответа {метод} разошлись с примером в CONTRACT.md.\n"
			f"  нет в контракте: {sorted(настоящие - ближний) or '—'}\n"
			f"  нет в ответе:    {sorted(ближний - настоящие) or '—'}"
		)

	def сверить_отказ(self, метод: str, ответ: dict, код: str) -> None:
		"""Отказ с кодом `код` и ключи `error` против примера отказа в разделе метода."""
		self.assertFalse(ответ["ok"], f"{метод} не отказал: {ответ}")
		self.assertEqual(ответ["error"]["code"], код, ответ)
		ожидаемые = self.примеры_отказов.get(f"lms_frappe_app.api.{метод}", [])
		self.assertIn(
			frozenset(ответ["error"]),
			ожидаемые,
			f"Ключи отказа {метод} разошлись с примером в CONTRACT.md: {sorted(ответ['error'])}",
		)

	def test_ключи_ответов_совпадают_с_примерами_контракта(self):
		курс, уроки = self._собрать_курс()
		курс_релиза = self._опубликовать_релиз()
		репорт, сдача = self._пройти_курс(курс, уроки, курс_релиза)
		self._посмотреть_прохождения(курс_релиза)
		self._разобрать_репорт(курс_релиза, репорт)
		self._посмотреть_отчёты()
		self._проверить_домашку(сдача, уроки[0])

	# --- релиз курса ---

	def _опубликовать_релиз(self) -> str:
		frappe.set_user(self.куратор)
		релиз = пример_релиза(f"contract-{self.суффикс}")
		курс = self.сверить(
			"authoring.publish_release",
			authoring.publish_release(release=релиз, instructors=[self.куратор], commit="ab" * 20),
		)["course"]
		# Повтор отвечает теми же ключами, что и публикация, и `commit` в нём есть.
		self.сверить("authoring.publish_release", authoring.publish_release(release=релиз))
		self.сверить("authoring.course_release", authoring.course_release(course=курс))
		self.сверить("authoring.course_release", authoring.course_release(course=курс, lesson="l-1"))
		self.сверить("authoring.course_releases", authoring.course_releases(course=курс))
		self.сверить("authoring.course_revision", authoring.course_revision(course=курс))
		заметка = self.сверить(
			"authoring.add_note",
			authoring.add_note(course=курс, target="goal.l-1/term:T1", text="Проверь пример"),
		)["id"]
		self.сверить("authoring.reply_note", authoring.reply_note(note=заметка, text="Шаг 3"))
		self.сверить(
			"authoring.set_note_status",
			authoring.set_note_status(note=заметка, status="done", text="Поправил", via="agent"),
		)
		self.сверить("authoring.list_notes", authoring.list_notes(course=курс))
		self.сверить("authoring.publish_course", authoring.publish_course(course=курс))
		self.сверить("authoring.unpublish_course", authoring.unpublish_course(course=курс))
		authoring.publish_course(course=курс)
		return курс

	# --- курс без релиза ---

	def _собрать_курс(self) -> tuple[str, list[str]]:
		"""Курс без релиза: анонс, его правка и программа, заведённая в Learning.

		Методы ученика вне урока релиза (`lesson_entry`, `homework`, документ)
		читают и такой курс; программу ему даёт не авторинг, а записи Learning.
		"""
		frappe.set_user(self.куратор)
		суффикс = self.суффикс

		курс = self.сверить(
			"authoring.create_course",
			authoring.create_course(title=f"Контракт {суффикс}", summary="Курс для сверки"),
		)["id"]
		self.сверить("authoring.list_courses", authoring.list_courses(published=False))
		self.сверить(
			"authoring.update_course",
			authoring.update_course(course=курс, summary="Курс для сверки контракта"),
		)

		frappe.set_user("Administrator")
		с_квизом = self._урок(курс, "Начало", "Циклы")
		без_квиза = self._урок(курс, "Функции", "Функции и возврат")
		создать_домашку(
			с_квизом,
			title="Цикл из жизни",
			description="Найдите у себя на работе повторяющееся действие и опишите его циклом.",
			due_mode="relative",
			due_days=3,
		)
		frappe.set_user(self.куратор)

		# Документ курса — тем же `записать_схему`, что пишет его из релиза.
		схема_документа(
			course=курс,
			artifact="summary",
			title="Резюме проекта",
			blocks=[{"key": "goal", "title": "Цель", "hint": "Одной фразой", "lesson": с_квизом}],
		)

		self.сверить("authoring.course_revision", authoring.course_revision(course=курс))
		# Тестеры до публикации: методы кабинета автора (learning-services#393).
		тестер = создать_ученика(f"contract-tester-{суффикс}@example.com")
		self.сверить("authoring.add_testers", authoring.add_testers(course=курс, users=тестер))
		self.сверить("authoring.course_testers", authoring.course_testers(course=курс))
		self.сверить(
			"authoring.remove_tester", authoring.remove_tester(course=курс, user=тестер)
		)
		# Анонс до выхода — курс без уроков: у него своя карта и подписка на
		# письмо о выходе.
		анонс = authoring.create_course(title=f"Анонс {суффикс}", summary="Скоро")["data"]["id"]
		self.сверить(
			"authoring.announce_course",
			authoring.announce_course(course=анонс, objectives="Писать циклы\nВыделять функции"),
		)
		frappe.set_user(self.ученик)
		self.сверить(
			"student.notify_when_released", student.notify_when_released(course=анонс)
		)
		self.сверить("public.course_map", public.course_map(course=анонс))
		# Открывает `publish_course` только курс из релиза; этот курс — запись напрямую.
		frappe.set_user("Administrator")
		frappe.db.set_value("LMS Course", курс, {"published": 1, "upcoming": 0})
		frappe.set_user(self.куратор)
		return курс, [с_квизом, без_квиза]

	def _урок(self, курс: str, название_главы: str, название: str) -> str:
		"""Глава с одним уроком в конце программы курса — записями Learning."""
		глава = frappe.get_doc({"doctype": "Course Chapter", "course": курс, "title": название_главы}).insert()
		привязать_главу(курс, глава.name)
		урок = frappe.get_doc(
			{"doctype": "Course Lesson", "chapter": глава.name, "title": название, "body": f"# {название}"}
		).insert()
		привязать_урок(глава.name, урок.name)
		return урок.name

	def _назначить_курс(self, курс: str) -> None:
		"""Назначение организации: оно приносит дедлайн ученику и строки
		отчёту руководителя.

		Заводится после самозаписи: сохранение назначения само выдаёт
		зачисления, и вызванный раньше `enroll` отвечал бы `already_enrolled`.
		"""
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Course Allocation",
				"organization": self.организация,
				"course": курс,
				"deadline": "2026-12-31",
			}
		).insert(ignore_permissions=True)

	# --- учебный поток ---

	def _пройти_курс(self, курс: str, уроки: list[str], курс_релиза: str) -> tuple[str, str]:
		с_квизом, _ = уроки
		frappe.set_user(self.ученик)

		self.сверить("student.whoami", student.whoami())
		self.сверить("student.list_catalog", student.list_catalog())
		self.сверить("student.enroll", student.enroll(course=курс))

		self._назначить_курс(курс)
		frappe.set_user(self.ученик)
		self.сверить("student.list_my_courses", student.list_my_courses())
		self.сверить("public.lesson_entry", public.lesson_entry(lesson=с_квизом))
		self.сверить("public.course_map", public.course_map(course=курс))

		# Методы агента по уроку — только курс из релиза (learning-services#506).
		frappe.set_user("Administrator")
		зачислить_на_курс(self.ученик, курс_релиза)
		frappe.set_user(self.ученик)
		занятие = self._пройти_урок_релиза(курс_релиза)
		self.сверить("student.course_outline", student.course_outline(course=курс_релиза))
		self.сверить("student.lesson_session", student.lesson_session(lesson=урок_релиза(курс_релиза, "l-1")))
		репорт = self.сверить(
			"student.report_issue",
			student.report_issue(session=занятие, kind="stuck", text="Встал на примере"),
		)["report"]
		self.сверить(
			"student.remember",
			student.remember(kind="fact", key="role", text="Руководитель отдела"),
		)
		self.сверить("student.my_notes", student.my_notes(course=курс))
		self.сверить("student.my_profile", student.my_profile())
		self.сверить("student.my_profile", student.my_profile(summary=1))
		self.сверить("student.forget", student.forget(key="role"))
		self.сверить(
			"student.update_artifact",
			student.update_artifact(
				course=курс, artifact="summary", key="goal", content="Открыть седьмую кофейню"
			),
		)
		self.сверить("student.artifact", student.artifact(course=курс))
		self.сверить("student.artifact", student.artifact(course=курс, artifact="summary"))
		self.сверить(
			"student.save_chat_state",
			student.save_chat_state(session=занятие, state=json.dumps({"messages": []}), version="1"),
		)
		self.сверить("student.chat_state", student.chat_state(session=занятие))
		self.сверить(
			"student.save_scenario_state",
			student.save_scenario_state(key="profile", state=json.dumps({"messages": []}), version="1"),
		)
		self.сверить("student.scenario_state", student.scenario_state(key="profile"))
		self.сверить("student.count_scenario_turn", student.count_scenario_turn(key="profile"))
		self.сверить("student.reset_scenario_state", student.reset_scenario_state(key="profile"))
		self.сверить("student.homework", student.homework(lesson=с_квизом))
		сдача = self.сверить(
			"student.submit_homework",
			student.submit_homework(lesson=с_квизом, answer="Сверка отчётов — цикл по отделам"),
		)["submission"]["id"]
		self.сверить("student.my_homework", student.my_homework())
		self.сверить("student.my_homework", student.my_homework(lesson=с_квизом))

		self.сверить("student.get_my_progress", student.get_my_progress())
		return репорт, сдача

	def _пройти_урок_релиза(self, курс: str) -> str:
		"""Урок с квизом — от старта до итога, урок без вопросов — до закрытия.

		Отдаёт занятие урока с квизом."""
		старт = self.сверить("student.start_lesson", student.start_lesson(lesson=урок_релиза(курс, "l-1")))
		занятие = старт["session"]
		self.сверить("student.lesson_item", student.lesson_item(session=занятие, goal="term:T1"))
		self._отметить_обязательные(занятие, старт["lesson_map"])
		прежняя = self.сверить("student.request_quiz", student.request_quiz(session=занятие))["attempt"]
		self._гонка_с_публикацией(курс, прежняя)
		попытка = self.сверить("student.request_quiz", student.request_quiz(session=занятие))
		self.assertEqual(попытка["previous_attempt_cancelled"]["attempt"], прежняя)
		self.сверить_отказ(
			"student.submit_answer",
			student.submit_answer(
				attempt=прежняя, question=попытка["question"]["id"], answer="V1", student_words="Первый"
			),
			"quiz_attempt_cancelled",
		)
		вопрос = попытка["question"]
		while вопрос is not None:
			ответ = self.сверить(
				"student.submit_answer",
				student.submit_answer(
					attempt=попытка["attempt"], question=вопрос["id"], answer="V1", student_words="Первый вариант"
				),
			)
			вопрос = ответ["next_question"]
		self.assertTrue(ответ["result"]["passed"], "квиз не зачтён — дальше сверять нечего")

		frappe.set_user("Administrator")
		релиз = frappe.db.get_value("LMS Course", курс, "active_release")
		frappe.db.delete("Agent Release Question", {"parent": релиз, "lesson_key": "l-2"})
		frappe.set_user(self.ученик)
		старт = self.сверить("student.start_lesson", student.start_lesson(lesson=урок_релиза(курс, "l-2")))
		self._отметить_обязательные(старт["session"], старт["lesson_map"])
		self.сверить("student.complete_lesson", student.complete_lesson(session=старт["session"]))
		return занятие

	def _гонка_с_публикацией(self, курс: str, попытка: str) -> None:
		"""Попытка — как заведённая в гонке с публикацией: на версии курса,
		которую уже сменила новая (`release_quiz._аннулировать_отставшую`)."""
		frappe.set_user("Administrator")
		прежний = frappe.db.get_value("LMS Course", курс, "active_release")
		релиз = пример_релиза(f"contract-{self.суффикс}")
		релиз["course"]["title"] = "Пример курса, второе издание"
		релизы.опубликовать(релиз, None, "Administrator")
		frappe.db.set_value("Agent Quiz Attempt", попытка, "release", прежний)
		frappe.set_user(self.ученик)

	def _отметить_обязательные(self, занятие: str, карта: list[dict]) -> None:
		for цель in карта:
			for пункт in цель["goals"]:
				if пункт["required"]:
					self.сверить(
						"student.mark_goal",
						student.mark_goal(
							session=занятие, goal=пункт["key"], status="done", evidence="Ученик сделал сам"
						),
					)

	# --- прохождения уроков для автора ---

	def _посмотреть_прохождения(self, курс: str) -> None:
		frappe.set_user(self.куратор)
		прохождения = self.сверить("authoring.goal_runs", authoring.goal_runs(course=курс))["runs"]
		self.assertTrue(прохождения, "у ученика нет прохождений — сверять нечего")
		self.сверить("authoring.goal_runs", authoring.goal_runs(course=курс, lesson="l-1", start=0, limit=1))

	# --- репорт: разбор и итог ---

	def _разобрать_репорт(self, курс: str, репорт: str) -> None:
		frappe.set_user(self.куратор)
		self.сверить("authoring.course_reports", authoring.course_reports(course=курс))
		self.сверить(
			"authoring.resolve_report",
			authoring.resolve_report(report=репорт, status="fixed", resolution="Добавили пример"),
		)
		frappe.set_user(self.ученик)
		self.сверить("student.my_reports", student.my_reports(course=курс))

	# --- отчётность руководителя ---

	def _посмотреть_отчёты(self) -> None:
		frappe.set_user(self.менеджер)
		self.сверить("manager.org_report", manager.org_report())
		self.сверить("manager.student_detail", manager.student_detail(user=self.ученик))

	# --- проверка домашки куратором (learning-services#452) ---

	def _проверить_домашку(self, сдача: str, урок: str) -> None:
		"""Руководитель проверяет сдачу ученика в пространстве своей организации."""
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Agent Homework Submission", сдача, "organization"), self.организация)
		frappe.set_user(self.менеджер)
		self.сверить("review.pending_count", review.pending_count())
		очередь = self.сверить("review.queue", review.queue())
		self.assertIn(сдача, [с["id"] for с in очередь["items"]])
		self.сверить("review.submission", review.submission(submission=сдача))
		self.сверить(
			"review.send_back", review.send_back(submission=сдача, version=1, comment="Добавьте пример")
		)
		frappe.set_user(self.ученик)
		student.submit_homework(lesson=урок, answer="Сверка отчётов — цикл по отделам, пример")
		frappe.set_user(self.менеджер)
		self.сверить("review.accept", review.accept(submission=сдача, version=2))
		self.сверить("review.reopen", review.reopen(submission=сдача, version=2, comment="Принял рано"))


class TestContractRelease(unittest.TestCase):
	"""Раздел «Релиз курса» называет схему, которая есть, и формат, который
	принимает сервер. `Why:` схема — часть контракта, и раздел, указывающий
	в пустоту, отстал бы молча (learning-services#500)."""

	ПУТЬ_СХЕМЫ = "lms_frappe_app/agent_learning/releases/release.public.schema.json"

	def test_контракт_называет_схему_и_формат(self):
		текст = КОНТРАКТ.read_text(encoding="utf-8")
		self.assertIn("# Релиз курса", текст.splitlines())
		self.assertIn(f"`{self.ПУТЬ_СХЕМЫ}`", текст)
		self.assertIn(f"`{схема_релиза.ФОРМАТ}`", текст)

	def test_схема_лежит_по_названному_пути(self):
		путь = КОНТРАКТ.parent / self.ПУТЬ_СХЕМЫ
		self.assertTrue(путь.exists(), путь)
		self.assertEqual(путь.resolve(), схема_релиза.СХЕМА.resolve())
