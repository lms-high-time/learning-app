# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import cint, getdate

from lms_frappe_app.agent_learning.constants import ЧЛЕНСТВО_ДЕЙСТВУЕТ
from lms_frappe_app.agent_learning.doctype.agent_lesson_homework.agent_lesson_homework import (
	НЕВЕРНЫЙ_СРОК,
	проверить_срок,
)
from lms_frappe_app.agent_learning.doctype.learning_organization.learning_organization import (
	LearningOrganization,
)
from lms_frappe_app.agent_learning.errors import Отказ
from lms_frappe_app.agent_learning.homework import ЗАДАНИЕ

ВСЕ_РОЛИ_УЧАСТНИКОВ = ("Member", "Manager", "Org Admin")

СРОКИ_ДОМАШЕК = "Course Allocation Homework Due"
ЗАДАНИЕ_НЕ_ИЗ_КУРСА = "homework_not_in_course"
#: «Как у автора» — отсутствие строки, а не режим `none`.
РЕЖИМЫ_СРОКА_НАЗНАЧЕНИЯ = ("relative", "absolute")


def _правило(строка) -> tuple:
	"""Строка срока как значение — чтобы узнать неизменённую среди прежних."""
	return (
		строка.homework,
		строка.due_mode,
		cint(строка.due_days) or None,
		str(getdate(строка.due_date)) if строка.due_date else None,
	)


class CourseAllocation(Document):
	"""Назначение курса организации или отдельным её участникам.

	Названо не `Assignment`: в Frappe Learning `LMS Assignment` — это домашнее
	задание ученика, и совпадение имён путало бы при каждом чтении кода.

	Сохранение порождает `LMS Enrollment` — зачисление остаётся единственным
	основанием доступа к курсу, и второго источника правды о том, кто на что
	записан, не появляется.
	"""

	def validate(self):
		self._проверить_курс_разрешён()
		self._проверить_курс_не_анонс()
		if self.audience == "Whole Organization":
			# Список адресатов при назначении на всю организацию только
			# вводит в заблуждение: состав считается на момент выдачи.
			self.members = []
		# Отметка метода — на одно сохранение: следующий `save()` проверяет снова.
		if not self.flags.pop("сроки_домашек_проверены", False):
			self.проверить_сроки_домашек()

	def on_update(self):
		self.выдать_зачисления()
		# Письмо — адресатам, кому ещё не уходило: и при создании, и когда в
		# поимённое назначение дописали людей (learning-services#365).
		from lms_frappe_app.agent_learning.notices import уведомить_о_назначении

		уведомить_о_назначении(self, self.адресаты())

	def _проверить_курс_разрешён(self) -> None:
		организация: LearningOrganization = frappe.get_doc(
			"Learning Organization", self.organization
		)
		if организация.status != "Active":
			frappe.throw(
				frappe._("Организация {0} приостановлена").format(self.organization)
			)
		if not организация.разрешает_курс(self.course):
			frappe.throw(
				frappe._("Курс {0} не открыт организации {1}").format(
					self.course, self.organization
				)
			)

	def _проверить_курс_не_анонс(self) -> None:
		"""Анонсированный курс не назначается: учиться в нём пока нечему.

		Проверяется при выборе курса, а не при каждом сохранении: правка срока
		у старого назначения не должна упираться в состояние курса.
		"""
		if not (self.is_new() or self.has_value_changed("course")):
			return
		from lms_frappe_app.agent_learning.announcements import анонсирован

		if анонсирован(self.course):
			frappe.throw(frappe._("Курс {0} ещё готовится").format(self.course))

	def проверить_сроки_домашек(self) -> None:
		"""Сроки домашек назначения: задание — урока этого курса, без повторов,
		правило — как у автора, но без `none` (learning-services#452).

		Чужое задание отклоняется только в новой или изменённой строке. Строку,
		чьё задание ушло из курса вместе с уроком (`move_lesson`), назначение
		молча выбрасывает. `Why:` иначе после переноса урока руководитель не
		поправил бы у назначения ничего — даже дедлайн курса.

		Зовётся и методом `team.update_allocation` до сохранения: у задания,
		которого нет вовсе, Frappe иначе отказал бы проверкой ссылки раньше
		`validate` — без кода контракта. Метод помечает проверку флагом
		`сроки_домашек_проверены`, и `validate` этого сохранения её не повторяет.
		"""
		if not self.homework_due:
			return
		уроки_курса = frappe.get_all("Course Lesson", filters={"course": self.course}, pluck="name")
		задания_курса = set(
			frappe.get_all(ЗАДАНИЕ, filters={"lesson": ("in", уроки_курса or [""])}, pluck="name")
		)
		прежние = (
			set()
			if self.is_new()
			else {
				_правило(строка)
				for строка in frappe.get_all(
					СРОКИ_ДОМАШЕК,
					filters={"parent": self.name, "parenttype": self.doctype, "parentfield": "homework_due"},
					fields=["homework", "due_mode", "due_days", "due_date"],
				)
			}
		)
		оставить = []
		встречены = set()
		for строка in self.homework_due:
			if строка.homework not in задания_курса:
				if _правило(строка) in прежние:
					continue
				raise Отказ(ЗАДАНИЕ_НЕ_ИЗ_КУРСА, "Это задание не из курса назначения", homework=строка.homework)
			if строка.homework in встречены:
				raise Отказ(НЕВЕРНЫЙ_СРОК, "У задания в назначении один срок", homework=строка.homework)
			встречены.add(строка.homework)
			строка.due_days, строка.due_date = проверить_срок(
				строка.due_mode, строка.due_days, строка.due_date, режимы=РЕЖИМЫ_СРОКА_НАЗНАЧЕНИЯ
			)
			оставить.append(строка)
		if len(оставить) != len(self.homework_due):
			self.set("homework_due", оставить)

	def адресаты(self) -> list[str]:
		"""Кому предназначено назначение."""
		return адресаты_назначения(self.name, self.organization, self.audience)

	def выдать_зачисления(self, участники: list[str] | None = None) -> int:
		"""Создаёт недостающие зачисления, возвращает число созданных.

		Существующие не трогает: у зачисления есть свой прогресс, и
		пересоздание обнулило бы пройденное.

		`участники` сужает выдачу до конкретных людей — так доприём одного
		сотрудника не заставляет перебирать всю организацию.
		"""
		создано = 0
		цели = участники if участники is not None else self.адресаты()
		for участник in цели:
			уже_записан = frappe.db.exists(
				"LMS Enrollment", {"member": участник, "course": self.course}
			)
			if уже_записан:
				continue
			записать_зачисление(участник, self.course)
			создано += 1
		return создано


def записать_зачисление(участник: str, course: str) -> None:
	"""Зачисление — единственное основание доступа к курсу, и заводится оно
	одним способом, из какого бы места ни пришёл повод."""
	frappe.get_doc(
		{
			"doctype": "LMS Enrollment",
			"member": участник,
			"course": course,
			"member_type": "Student",
		}
	).insert(ignore_permissions=True)


def адресаты_назначения(name: str, organization: str, audience: str) -> list[str]:
	"""Кому предназначено назначение — по его полям, без загрузки документа.

	`Why:` правило должно быть одно, но отчёту незачем поднимать документ с
	дочерними таблицами на каждое назначение: он и так ходит по списку.
	"""
	назначение = frappe._dict(name=name, organization=organization, audience=audience)
	return адресаты_назначений([назначение])[name]


def адресаты_назначений(назначения: list) -> dict[str, list[str]]:
	"""То же правило сразу для списка назначений — двумя запросами на весь список.

	`Why:` суточная сверка спрашивала адресатов у каждого назначения
	отдельно, а состав организации — по разу на каждое её назначение.
	Правило при этом одно: одиночный случай ходит сюда же.
	"""
	поимённые = [н.name for н in назначения if н.audience == "Selected Members"]
	# Состав нужен и поимённым: вписанный в список получает курс, только пока
	# он действующий участник — ни ушедший, ни посторонний (#345).
	организации = list({н.organization for н in назначения})

	по_назначениям: dict[str, list[str]] = {}
	if поимённые:
		строки = frappe.get_all(
			"Course Allocation Member",
			filters={"parent": ("in", поимённые), "parenttype": "Course Allocation"},
			fields=["parent", "user"],
			order_by="idx asc",
		)
		for строка in строки:
			по_назначениям.setdefault(строка.parent, []).append(строка.user)

	состав: dict[str, list[str]] = {}
	if организации:
		строки = frappe.get_all(
			"Organization Membership",
			filters={
				"organization": ("in", организации),
				"role": ("in", ВСЕ_РОЛИ_УЧАСТНИКОВ),
				"status": ЧЛЕНСТВО_ДЕЙСТВУЕТ,
			},
			fields=["organization", "user"],
		)
		for строка in строки:
			состав.setdefault(строка.organization, []).append(строка.user)

	действующие = {организация: set(люди) for организация, люди in состав.items()}
	return {
		н.name: (
			[у for у in по_назначениям.get(н.name, []) if у in действующие.get(н.organization, set())]
			if н.audience == "Selected Members"
			else состав.get(н.organization, [])
		)
		for н in назначения
	}


def досрочные_назначения_организации(organization: str) -> list[str]:
	"""Назначения на всю организацию — те, что достаются и новичкам.

	Поимённые сюда не входят: у них адресат задан явно, и человек, которого
	в списке нет, курс получить не должен.
	"""
	return frappe.get_all(
		"Course Allocation",
		filters={"organization": organization, "audience": "Whole Organization"},
		pluck="name",
	)


def сверить_зачисления() -> int:
	"""Ежесуточная сверка: выдаёт зачисления, которые не выдал хук.

	`Why:` членство может появиться в обход хука — импортом, миграцией или
	правкой в базе. Тогда сотрудник тихо остаётся без обязательного курса, и
	обнаруживается это в день дедлайна.

	Приостановленные организации пропускаются: их доступ отозван осознанно.

	Задача суточная и ходит по всей платформе, поэтому база опрашивается
	списками: адресаты всех назначений — двумя запросами, уже выданные
	зачисления — одним на все затронутые курсы. Прежняя редакция поднимала
	документ на каждое назначение и спрашивала базу про каждую пару
	«человек × курс» — тысяча сотрудников с десятью курсами давала
	десятки тысяч обходов за ночь.
	"""
	действующие = frappe.get_all(
		"Learning Organization", filters={"status": "Active"}, pluck="name"
	)
	if not действующие:
		return 0

	назначения = frappe.get_all(
		"Course Allocation",
		filters={"organization": ("in", действующие)},
		fields=["name", "organization", "course", "audience"],
	)
	if not назначения:
		return 0

	адресаты = адресаты_назначений(назначения)
	записаны = {
		(строка.member, строка.course)
		for строка in frappe.get_all(
			"LMS Enrollment",
			filters={"course": ("in", list({н.course for н in назначения}))},
			fields=["member", "course"],
		)
	}

	создано = 0
	for назначение in назначения:
		for участник in адресаты[назначение.name]:
			if (участник, назначение.course) in записаны:
				continue
			записать_зачисление(участник, назначение.course)
			записаны.add((участник, назначение.course))
			создано += 1
	return создано


def назначения_пользователя(user: str, course: str | None = None) -> list[dict]:
	"""Действующие назначения пользователя — с дедлайнами и обязательностью.

	Нужна и инструменту `list_my_courses`, и отчёту менеджера: дедлайн живёт
	в назначении, а не в зачислении, и без этой связки просрочку не показать.
	"""
	from lms_frappe_app.agent_learning.doctype.learning_organization.learning_organization import (
		организации_пользователя,
	)

	организации = организации_пользователя(user)
	if not организации:
		return []

	фильтры = {"organization": ("in", организации)}
	if course:
		фильтры["course"] = course

	назначения = frappe.get_all(
		"Course Allocation",
		filters=фильтры,
		fields=["name", "organization", "course", "audience", "deadline", "mandatory", "creation"],
	)
	свои = []
	for назначение in назначения:
		if назначение.audience == "Whole Organization" or frappe.db.exists(
			"Course Allocation Member", {"parent": назначение.name, "user": user}
		):
			свои.append(назначение)
	return свои
