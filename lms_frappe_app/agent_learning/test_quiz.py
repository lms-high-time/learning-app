# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_system_timezone, now_datetime

from lms_frappe_app.agent_learning.access import НЕ_ЗАЧИСЛЕН, ОРГАНИЗАЦИЯ_ПРИОСТАНОВЛЕНА
from lms_frappe_app.agent_learning.quiz import (
	КВИЗА_НЕТ,
	ПОПЫТКА_ЗАВЕРШЕНА,
	ПОПЫТКИ_ИСЧЕРПАНЫ,
	СЛИШКОМ_РАНО,
	НЕЧЕГО_ПРОВЕРЯТЬ,
	ЧУЖОЙ_ВОПРОС,
	начать_попытку,
	принять_ответ,
)
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.tests.sample_data import (
	настроить_квиз,
	добавить_в_организацию,
	политика_по_умолчанию,
	создать_занятие,
	создать_вопрос,
	создать_занятие,
	создать_квиз,
	создать_организацию,
	создать_ученика,
	создать_урок,
	зачислить,
)

ЭТАЛОННЫЕ_ПОЛЯ = ("is_correct", "possibility", "explanation_")
ПОЯСНЕНИЕ_ВЕРНОГО = "Потому что счётчик начинается с нуля"
ПОЯСНЕНИЕ_НЕВЕРНОГО = "Один — это число проходов, а не последнее значение"


class IntegrationTestQuiz(IntegrationTestCase):
	"""Серверный квиз: выдача вопросов, сверка, итог, лимиты."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"q-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		# У первого неверного варианта своё пояснение, у третьего — нет.
		self.вопрос_выбор = создать_вопрос(
			"Что выведет цикл?",
			варианты=[("раз", False, ПОЯСНЕНИЕ_НЕВЕРНОГО), ("два", True), ("три", False)],
			пояснение=ПОЯСНЕНИЕ_ВЕРНОГО,
		)
		self.вопрос_ввод = создать_вопрос(
			"Как называется оператор повторения?", возможные_ответы=["цикл", "loop"]
		)
		self.квиз = создать_квиз(self.урок, [self.вопрос_выбор, self.вопрос_ввод])
		# Зачисление обязательно: начать_попытку проверяет доступ к курсу —
		# раньше квиз сдавался и без него.
		зачислить(self.ученик, self.урок)
		self.занятие = создать_занятие(self.ученик, self.урок)

	def начать(self):
		return начать_попытку(self.занятие)

	def назначить_политику(self, **поля):
		"""Правила квиза — платформы, одни на всех (learning-services#353)."""
		настроить_квиз(**поля)

	def пройти_целиком(self, верно_выбор=True, верно_ввод=True):
		начало = self.начать()
		попытка = начало["attempt"]
		принять_ответ(попытка, self.вопрос_выбор, "2" if верно_выбор else "1", "слова ученика")
		итог = принять_ответ(попытка, self.вопрос_ввод, "цикл" if верно_ввод else "мимо", "слова ученика")
		return попытка, итог

	# --- главное: эталоны не покидают сервер ---

	def test_в_выданном_вопросе_нет_ни_одного_поля_эталона(self):
		"""Несущая проверка всей схемы зачёта.

		Утечка эталона обесценивает серверный квиз целиком, а с ним и
		устойчивость к пересказу директивы агентом.
		"""
		выдано = json.dumps(self.начать(), ensure_ascii=False, default=str)

		for поле in ЭТАЛОННЫЕ_ПОЛЯ:
			self.assertNotIn(поле, выдано)

	def test_варианты_выдаются_без_признака_правильности(self):
		вопрос = self.начать()["question"]
		self.assertEqual([в["id"] for в in вопрос["options"]], ["1", "2", "3"])
		for вариант in вопрос["options"]:
			self.assertEqual(set(вариант), {"id", "text"})

	def test_пояснение_приходит_только_вместе_с_вердиктом(self):
		# До ответа пояснение было бы подсказкой.
		начало = self.начать()
		self.assertNotIn("explanation", json.dumps(начало["question"], ensure_ascii=False))

		ответ = принять_ответ(начало["attempt"], self.вопрос_выбор, "2", "слова ученика")

		# После верного ответа — приходит, и это разные состояния одного
		# вопроса, а не ссылка на соседний тест.
		self.assertTrue(ответ["verdict"]["correct"])
		self.assertEqual(ответ["verdict"]["explanation"], ПОЯСНЕНИЕ_ВЕРНОГО)
		self.assertNotIn("why_wrong", ответ["verdict"])
		self.assertIsNotNone(ответ["next_question"])

	def test_неверный_ответ_поясняется_выбранным_вариантом_а_не_верным(self):
		"""Пояснение верного варианта ошибившемуся — готовый ответ до пересдачи."""
		попытка = self.начать()["attempt"]

		вердикт = принять_ответ(попытка, self.вопрос_выбор, "1", "слова ученика")["verdict"]

		self.assertFalse(вердикт["correct"])
		self.assertEqual(вердикт["why_wrong"], ПОЯСНЕНИЕ_НЕВЕРНОГО)
		self.assertNotIn("explanation", вердикт)
		self.assertNotIn(ПОЯСНЕНИЕ_ВЕРНОГО, json.dumps(вердикт, ensure_ascii=False))

	def test_неверный_вариант_без_пояснения_не_даёт_why_wrong(self):
		попытка = self.начать()["attempt"]

		вердикт = принять_ответ(попытка, self.вопрос_выбор, "3", "слова ученика")["verdict"]

		self.assertEqual(вердикт, {"correct": False})

	def test_множественный_выбор_поясняет_только_выбранные_неверные(self):
		# Верный вариант среди выбранных не поясняется: пояснение назвало бы,
		# какой из выбранных угадан.
		вопрос = создать_вопрос(
			"Выберите чётные",
			варианты=[
				("2", True, "Два делится на два"),
				("3", False, "Три нечётное"),
				("4", True, "Четыре делится на два"),
				("5", False, "Пять нечётное"),
			],
		)
		урок = создать_урок(f"Урок {frappe.generate_hash(length=6)}")
		создать_квиз(урок, [вопрос])
		зачислить(self.ученик, урок)
		попытка = начать_попытку(создать_занятие(self.ученик, урок))["attempt"]

		вердикт = принять_ответ(попытка, вопрос, "1,2", "слова ученика")["verdict"]

		self.assertFalse(вердикт["correct"])
		self.assertEqual(вердикт["why_wrong"], "Три нечётное")
		self.assertNotIn("делится", json.dumps(вердикт, ensure_ascii=False))

	# --- сверка ---

	def test_неверный_выбор_не_засчитывается(self):
		попытка = self.начать()["attempt"]
		self.assertFalse(принять_ответ(попытка, self.вопрос_выбор, "1", "слова ученика")["verdict"]["correct"])

	def test_свободный_ввод_сверяется_без_учёта_регистра_и_пробелов(self):
		попытка = self.начать()["attempt"]
		принять_ответ(попытка, self.вопрос_выбор, "2", "слова ученика")
		ответ = принять_ответ(попытка, self.вопрос_ввод, "  ЦИКЛ ", "слова ученика")
		self.assertTrue(ответ["verdict"]["correct"])

	def test_множественный_выбор_требует_полного_совпадения(self):
		"""Защита от очевидного чита: сверка требует точного совпадения."""
		# Два одинаковых вопроса в одной попытке: вторая попытка подряд упёрлась
		# бы в паузу перед повтором.
		вопросы = [
			создать_вопрос("Выберите чётные", варианты=[("2", True), ("3", False), ("4", True)])
			for _ in range(2)
		]
		урок = создать_урок(f"Урок {frappe.generate_hash(length=6)}")
		создать_квиз(урок, вопросы)
		зачислить(self.ученик, урок)
		попытка = начать_попытку(создать_занятие(self.ученик, урок))["attempt"]

		частично = принять_ответ(попытка, вопросы[0], "1", "слова ученика")
		self.assertFalse(частично["verdict"]["correct"])

		все_варианты = принять_ответ(попытка, вопросы[1], "1,2,3", "слова ученика")
		self.assertFalse(все_варианты["verdict"]["correct"])

	def test_ответ_принимается_и_строкой_и_списком(self):
		# Агенты форматируют по-разному; отказ из-за запятой выглядел бы как
		# неверный ответ.
		# Два отдельных квиза: две попытки подряд на одном упёрлись бы в паузу
		# перед повтором — и это правильное поведение, а не помеха тесту.
		for ответ in ("1,3", ["1", "3"]):
			вопрос = создать_вопрос(
				"Чётные", варианты=[("2", True), ("3", False), ("4", True)]
			)
			урок = создать_урок(f"Урок {frappe.generate_hash(length=6)}")
			создать_квиз(урок, [вопрос])
			зачислить(self.ученик, урок)
			попытка = начать_попытку(создать_занятие(self.ученик, урок))["attempt"]

			self.assertTrue(принять_ответ(попытка, вопрос, ответ, "слова ученика")["verdict"]["correct"])

	# --- ответ текстом варианта (lms-platform#148) ---
	# Модели передают в квиз текст выбранного варианта вместо его номера.
	# Верный ответ ученика не должен сгорать из-за формата, который выбрал агент.

	def test_текст_верного_варианта_засчитывается(self):
		попытка = self.начать()["attempt"]
		ответ = принять_ответ(попытка, self.вопрос_выбор, "  Два ", "слова ученика")
		self.assertTrue(ответ["verdict"]["correct"])

	def test_текст_неверного_варианта_не_засчитывается(self):
		попытка = self.начать()["attempt"]
		self.assertFalse(принять_ответ(попытка, self.вопрос_выбор, "раз", "слова ученика")["verdict"]["correct"])

	def test_все_варианты_текстом_не_засчитываются(self):
		# Строгое равенство множеств держится и для текстов.
		попытка = self.начать()["attempt"]
		ответ = принять_ответ(попытка, self.вопрос_выбор, "раз, два, три", "слова ученика")
		self.assertFalse(ответ["verdict"]["correct"])

	def test_неизвестный_текст_неверен_а_не_ошибка(self):
		попытка = self.начать()["attempt"]
		self.assertFalse(принять_ответ(попытка, self.вопрос_выбор, "четыре", "слова ученика")["verdict"]["correct"])

	def test_текст_варианта_с_запятой_засчитывается_целиком(self):
		вопрос = создать_вопрос(
			"Столица?", варианты=[("Москва, Россия", True), ("Тула, Россия", False)]
		)
		урок = создать_урок(f"Урок {frappe.generate_hash(length=6)}")
		создать_квиз(урок, [вопрос])
		зачислить(self.ученик, урок)
		попытка = начать_попытку(создать_занятие(self.ученик, урок))["attempt"]

		self.assertTrue(принять_ответ(попытка, вопрос, "москва, россия", "слова ученика")["verdict"]["correct"])

	def test_номера_важнее_текстов_похожих_на_номер(self):
		# У варианта №2 текст «3»: ответ «1,3» — это номера, а не текст.
		вопрос = создать_вопрос("Чётные", варианты=[("2", True), ("3", False), ("4", True)])
		урок = создать_урок(f"Урок {frappe.generate_hash(length=6)}")
		создать_квиз(урок, [вопрос])
		зачислить(self.ученик, урок)
		попытка = начать_попытку(создать_занятие(self.ученик, урок))["attempt"]

		self.assertTrue(принять_ответ(попытка, вопрос, "1,3", "слова ученика")["verdict"]["correct"])

	# --- итог ---

	def test_пройденный_квиз_закрывает_занятие_и_пишет_прогресс(self):
		попытка, итог = self.пройти_целиком()

		self.assertTrue(итог["attempt_finished"])
		self.assertTrue(итог["result"]["passed"])
		self.assertEqual(итог["result"]["session_status"], "Completed")
		# Пересдавать зачтённое незачем: ни паузы, ни остатка попыток.
		self.assertNotIn("retry_after", итог["result"])
		self.assertNotIn("attempts_left", итог["result"])
		self.assertTrue(
			frappe.db.exists(
				"LMS Course Progress",
				{"member": self.ученик, "lesson": self.урок, "status": "Complete"},
			)
		)

	def test_итог_виден_в_стандартной_записи_frappe(self):
		# Оба канала обязаны сходиться в одной записи: ученик занимается с
		# агентом, а результат видит в браузере.
		попытка, _ = self.пройти_целиком()
		submission = frappe.db.get_value("Agent Quiz Attempt", попытка, "submission")

		self.assertTrue(submission)
		запись = frappe.get_doc("LMS Quiz Submission", submission)
		self.assertEqual(запись.member, self.ученик)
		self.assertEqual(запись.score, 2)
		self.assertEqual(запись.score_out_of, 2)
		self.assertEqual(len(запись.result), 2)

	def test_проваленный_квиз_не_закрывает_занятие(self):
		попытка, итог = self.пройти_целиком(верно_выбор=False, верно_ввод=False)

		self.assertFalse(итог["result"]["passed"])
		self.assertNotEqual(итог["result"]["session_status"], "Completed")
		self.assertFalse(
			frappe.db.exists(
				"LMS Course Progress",
				{"member": self.ученик, "lesson": self.урок, "status": "Complete"},
			)
		)

	# --- отказы ---

	def test_повторный_ответ_на_вопрос_отклоняется(self):
		попытка = self.начать()["attempt"]
		принять_ответ(попытка, self.вопрос_выбор, "2", "слова ученика")

		with self.assertRaises(Отказ) as отказ:
			принять_ответ(попытка, self.вопрос_выбор, "1", "слова ученика")
		self.assertEqual(отказ.exception.код, ЧУЖОЙ_ВОПРОС)

	def test_чужой_вопрос_отклоняется(self):
		# Минимум два варианта: Frappe Learning иначе не сохранит вопрос.
		чужой = создать_вопрос("Не из этого квиза", варианты=[("да", True), ("нет", False)])
		попытка = self.начать()["attempt"]

		with self.assertRaises(Отказ) as отказ:
			принять_ответ(попытка, чужой, "1", "слова ученика")
		self.assertEqual(отказ.exception.код, ЧУЖОЙ_ВОПРОС)

	def test_ответ_в_завершённую_попытку_отклоняется(self):
		попытка, _ = self.пройти_целиком()

		with self.assertRaises(Отказ) as отказ:
			принять_ответ(попытка, self.вопрос_выбор, "2", "слова ученика")
		self.assertEqual(отказ.exception.код, ПОПЫТКА_ЗАВЕРШЕНА)

	def test_урок_без_квиза_даёт_внятный_код(self):
		урок = создать_урок(f"Без квиза {frappe.generate_hash(length=6)}")
		зачислить(self.ученик, урок)
		занятие = создать_занятие(self.ученик, урок)

		with self.assertRaises(Отказ) as отказ:
			начать_попытку(занятие)
		self.assertEqual(отказ.exception.код, КВИЗА_НЕТ)

	# --- политика организации ---

	def test_исчерпанные_попытки_отклоняются_с_числом(self):
		self.назначить_политику(max_attempts=1, retry_delay_minutes=0)

		_, итог = self.пройти_целиком(верно_выбор=False, верно_ввод=False)

		# Последняя попытка сгорела — итог не обещает пересдачи.
		self.assertEqual(итог["result"]["attempts_left"], 0)
		self.assertIsNone(итог["result"]["retry_after"])

		with self.assertRaises(Отказ) as отказ:
			начать_попытку(создать_занятие(self.ученик, self.урок))
		self.assertEqual(отказ.exception.код, ПОПЫТКИ_ИСЧЕРПАНЫ)
		self.assertEqual(отказ.exception.подробности["attempts_used"], 1)

	def test_повтор_раньше_паузы_отклоняется_с_временем(self):
		self.назначить_политику(max_attempts=5, retry_delay_minutes=1440)

		_, итог = self.пройти_целиком(верно_выбор=False, верно_ввод=False)

		with self.assertRaises(Отказ) as отказ:
			начать_попытку(создать_занятие(self.ученик, self.урок))
		self.assertEqual(отказ.exception.код, СЛИШКОМ_РАНО)
		# Отказ называет тот же момент, что итог попытки, и тоже с поясом.
		self.assertEqual(отказ.exception.подробности["retry_after"], итог["result"]["retry_after"])

	def test_проваленная_попытка_называет_паузу_и_остаток(self):
		"""Момент пересдачи — с часовым поясом сайта: без него строка читается как UTC."""
		self.назначить_политику(max_attempts=3, retry_delay_minutes=1440)

		попытка, итог = self.пройти_целиком(верно_выбор=False, верно_ввод=False)

		self.assertEqual(итог["result"]["attempts_left"], 2)
		момент = datetime.fromisoformat(итог["result"]["retry_after"])
		self.assertIsNotNone(момент.tzinfo, "retry_after без часового пояса")
		закончена = frappe.db.get_value("Agent Quiz Attempt", попытка, "finished_at")
		self.assertEqual(
			момент,
			закончена.replace(tzinfo=ZoneInfo(get_system_timezone())) + timedelta(hours=24),
		)

	def test_без_лимита_остаток_попыток_не_число(self):
		# «Без лимита» задаётся только в общих настройках: ноль у организации
		# наследует ограничение.
		self.addCleanup(политика_по_умолчанию)
		frappe.db.set_single_value("Agent Learning Settings", "max_attempts", 0)
		frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")

		_, итог = self.пройти_целиком(верно_выбор=False, верно_ввод=False)

		self.assertIsNone(итог["result"]["attempts_left"])
		self.assertIsNotNone(итог["result"]["retry_after"])

	# --- открытые вопросы ---

	def test_квиз_из_открытых_вопросов_даёт_внятный_отказ(self):
		"""Их проверка требует человека, а зачёт от агента — не зачёт.

		Смешать открытые вопросы с проверяемыми Frappe Learning не позволяет:
		«make sure each question in the quiz is of open ended type». Значит
		квиз либо весь открытый, либо проверяемый целиком.
		"""
		открытый = frappe.get_doc(
			{"doctype": "LMS Question", "question": "Расскажите своими словами", "type": "Open Ended"}
		).insert(ignore_permissions=True)
		урок = создать_урок(f"Открытый {frappe.generate_hash(length=6)}")
		создать_квиз(урок, [открытый.name])
		зачислить(self.ученик, урок)

		with self.assertRaises(Отказ) as отказ:
			начать_попытку(создать_занятие(self.ученик, урок))
		self.assertEqual(отказ.exception.код, НЕЧЕГО_ПРОВЕРЯТЬ)


class IntegrationTestQuizIntegrity(IntegrationTestCase):
	"""Целостность зачёта: лимиты, доступ, порог, кривой контент."""

	def setUp(self):
		суффикс = frappe.generate_hash(length=6)
		self.ученик = создать_ученика(f"qi-{суффикс}@example.com")
		self.урок = создать_урок(f"Урок {суффикс}")
		self.курс = frappe.db.get_value(
			"Course Chapter", frappe.db.get_value("Course Lesson", self.урок, "chapter"), "course"
		)
		self.вопрос = создать_вопрос(
			"Столица?", варианты=[("Москва", True), ("Тула", False), ("Клин", False)]
		)
		self.квиз = создать_квиз(self.урок, [self.вопрос])
		зачислить(self.ученик, self.урок)

	def занятие(self):
		return создать_занятие(self.ученик, self.урок)

	def организация_с_политикой(self, **поля):
		"""Курс даёт организация, а правила квиза — платформы, одни на всех (#353)."""
		if поля:
			настроить_квиз(**поля)
		организация = создать_организацию(f"Компания {frappe.generate_hash(length=6)}")
		добавить_в_организацию(self.ученик, организация)
		frappe.get_doc(
			{"doctype": "Course Allocation", "organization": организация, "course": self.курс}
		).insert(ignore_permissions=True)
		return организация

	# --- лимит попыток ---

	def test_брошенная_попытка_расходует_лимит(self):
		"""Иначе ответы перебираются: ответил, увидел вердикт, бросил, начал заново."""
		self.организация_с_политикой(max_attempts=1, retry_delay_minutes=60)

		первая = начать_попытку(self.занятие())["attempt"]
		принять_ответ(первая, self.вопрос, "2", "слова ученика")  # неверно, попытка остаётся открытой

		with self.assertRaises(Отказ) as отказ:
			начать_попытку(self.занятие())
		self.assertEqual(отказ.exception.код, ПОПЫТКИ_ИСЧЕРПАНЫ)

	def test_повторный_запрос_возвращает_ту_же_попытку(self):
		# Не новую: иначе счётчик попыток растёт от каждого переподключения.
		занятие = self.занятие()
		первая = начать_попытку(занятие)["attempt"]
		вторая = начать_попытку(занятие)["attempt"]
		self.assertEqual(первая, вторая)

	def test_возвращённая_попытка_помнит_отвеченное(self):
		# Квиз из двух вопросов: после одного ответа попытка ещё открыта, и
		# повторное подключение агента обязано продолжить её, а не начать
		# заново с первого вопроса.
		второй = создать_вопрос("Крупнейший город?", варианты=[("Москва", True), ("Клин", False)])
		урок = создать_урок(f"Два вопроса {frappe.generate_hash(length=6)}")
		создать_квиз(урок, [self.вопрос, второй])
		зачислить(self.ученик, урок)
		занятие = создать_занятие(self.ученик, урок)

		попытка = начать_попытку(занятие)["attempt"]
		принять_ответ(попытка, self.вопрос, "1", "слова ученика")

		повтор = начать_попытку(занятие)

		self.assertEqual(повтор["attempt"], попытка)
		self.assertEqual(повтор["question"]["id"], второй)

	# --- доступ ---

	def test_квиз_по_курсу_приостановленной_организации_не_начать(self):
		организация = self.организация_с_политикой()
		занятие = self.занятие()
		frappe.db.set_value("Learning Organization", организация, "status", "Suspended")

		with self.assertRaises(Отказ) as отказ:
			начать_попытку(занятие)
		self.assertEqual(отказ.exception.код, ОРГАНИЗАЦИЯ_ПРИОСТАНОВЛЕНА)

	def test_квиз_без_зачисления_не_начать(self):
		посторонний = создать_ученика(f"qx-{frappe.generate_hash(length=6)}@example.com")
		занятие = создать_занятие(посторонний, self.урок)

		with self.assertRaises(Отказ) as отказ:
			начать_попытку(занятие)
		self.assertEqual(отказ.exception.код, НЕ_ЗАЧИСЛЕН)

	# --- брошенное занятие ---

	def test_квиз_завершается_даже_если_занятие_закрыли_по_бездействию(self):
		"""Ученик вернулся к последнему вопросу через сутки — результат не теряется."""
		self.организация_с_политикой()
		занятие = self.занятие()
		попытка = начать_попытку(занятие)["attempt"]
		frappe.db.set_value("Agent Learning Session", занятие, "status", "Abandoned")

		итог = принять_ответ(попытка, self.вопрос, "1", "слова ученика")

		self.assertTrue(итог["result"]["passed"])
		self.assertTrue(frappe.db.get_value("Agent Quiz Attempt", попытка, "submission"))

	# --- порог ---

	def test_порог_в_стандартной_записи_совпадает_с_политикой(self):
		# Иначе в нашей записи «зачтено», а в браузерной — ниже проходного.
		self.организация_с_политикой(pass_threshold=0.5)
		попытка = начать_попытку(self.занятие())["attempt"]
		принять_ответ(попытка, self.вопрос, "1", "слова ученика")

		submission = frappe.db.get_value("Agent Quiz Attempt", попытка, "submission")
		self.assertEqual(
			frappe.db.get_value("LMS Quiz Submission", submission, "passing_percentage"), 50
		)

	# --- кривой контент ---

	def test_вопрос_без_эталона_не_выдаётся_ученику(self):
		"""Кривой контент мешает одному вопросу, а не всему курсу.

		Сам Frappe Learning вопрос без верного варианта сохранить не даёт, но
		запись может приехать импортом. Пробовалось отвечать на него отказом —
		попытка оставалась открытой навсегда, каждый следующий ответ падал, и
		ученик запирался в квизе без выхода: ни завершить, ни начать заново.
		Теперь такой вопрос просто не выдаётся.
		"""
		кривой = создать_вопрос("Кривой", варианты=[("раз", True), ("два", False)])
		годный = создать_вопрос("Годный", варианты=[("да", True), ("нет", False)])
		урок = создать_урок(f"Кривой {frappe.generate_hash(length=6)}")
		создать_квиз(урок, [кривой, годный])
		зачислить(self.ученик, урок)
		# Портим данные мимо валидации — так же, как это сделал бы импорт.
		frappe.db.set_value("LMS Question", кривой, "is_correct_1", 0)
		frappe.clear_document_cache("LMS Question", кривой)

		начало = начать_попытку(создать_занятие(self.ученик, урок))

		self.assertEqual(начало["question"]["id"], годный)
		self.assertEqual(начало["question"]["total"], 1)

		итог = принять_ответ(начало["attempt"], годный, "1", "слова ученика")
		self.assertTrue(итог["attempt_finished"], "квиз должен завершаться без кривого вопроса")
