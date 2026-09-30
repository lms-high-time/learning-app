# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Данные для тестов: минимальная цепочка курса и учебные пользователи.

Живёт отдельным модулем, потому что нужна и тестам директивы, и тестам
занятий, и всему, что придёт следом. Лежит среди тестов, а не в
`agent_learning`: фикстуры ничего не обслуживают в работающем приложении, и
рядом с рабочими модулями их однажды позовут из рабочего кода.
"""

import frappe
from frappe.utils import get_url

from lms_frappe_app.agent_learning.directives import действующая


def создать_урок(название: str = "Урок") -> str:
	"""Минимальная цепочка курс → глава → урок, возвращает имя урока."""
	course = frappe.get_doc(
		{
			"doctype": "LMS Course",
			"title": f"Курс для тестов ({название})",
			"short_introduction": "Курс для тестов",
			"description": "<p>Курс для тестов</p>",
			"published": 0,
			# instructors обязателен у LMS Course — без него вставка падает
			# с MandatoryError, а сообщение указывает на таблицу, а не на поле.
			"instructors": [{"instructor": "Administrator"}],
		}
	).insert(ignore_permissions=True)
	chapter = frappe.get_doc(
		{"doctype": "Course Chapter", "title": "Глава", "course": course.name}
	).insert(ignore_permissions=True)
	lesson = frappe.get_doc(
		{"doctype": "Course Lesson", "title": название, "chapter": chapter.name}
	).insert(ignore_permissions=True)
	# Ссылки главы и урока — то, чем Frappe Learning задаёт порядок. Без них
	# тестовые курсы отличались бы от настоящих там, где это важнее всего.
	привязать_главу(course.name, chapter.name)
	привязать_урок(chapter.name, lesson.name)
	return lesson.name


def привязать_главу(course: str, chapter: str) -> None:
	"""Добавляет главу в курс, задавая её место в порядке."""
	курс = frappe.get_doc("LMS Course", course)
	if not any(строка.chapter == chapter for строка in курс.chapters):
		курс.append("chapters", {"chapter": chapter})
		курс.save(ignore_permissions=True)


def привязать_урок(chapter: str, lesson: str) -> None:
	"""Добавляет урок в главу, задавая его место в порядке."""
	глава = frappe.get_doc("Course Chapter", chapter)
	if not any(строка.lesson == lesson for строка in глава.lessons):
		глава.append("lessons", {"lesson": lesson})
		глава.save(ignore_permissions=True)


def создать_ученика(почта: str) -> str:
	"""Пользователь с ролью ученика.

	`cache=False` обязателен: тесты откатывают транзакцию, а кеш документов
	переживает откат — проверка отвечала «есть», пользователь не создавался, и
	следующий тест падал на несуществующей ссылке.
	"""
	if not frappe.db.exists("User", почта, cache=False):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": почта,
				"first_name": почта.split("@")[0],
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		user.add_roles("LMS Student")
	return почта


def создать_куратора(почта: str, роль: str = "Course Creator") -> str:
	"""Пользователь с правом собирать курсы.

	Роль задаётся: удаление уроков Frappe Learning разрешает только
	`Moderator`, и тесты на него заводят куратора с этой ролью.
	"""
	if not frappe.db.exists("User", почта, cache=False):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": почта,
				"first_name": почта.split("@")[0],
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		user.add_roles(роль)
	return почта


def создать_организацию(название: str, **поля) -> str:
	"""Организация с политикой по умолчанию."""
	if frappe.db.exists("Learning Organization", название, cache=False):
		return название
	return frappe.get_doc(
		{"doctype": "Learning Organization", "organization_name": название, **поля}
	).insert(ignore_permissions=True).name


def добавить_в_организацию(user: str, organization: str, role: str = "Member") -> str:
	return frappe.get_doc(
		{
			"doctype": "Organization Membership",
			"user": user,
			"organization": organization,
			"role": role,
		}
	).insert(ignore_permissions=True).name


def создать_курс(название: str) -> str:
	"""Курс без глав — когда урок не нужен, а курс нужен."""
	return frappe.get_doc(
		{
			"doctype": "LMS Course",
			"title": название,
			"short_introduction": "Курс для тестов",
			"description": "<p>Курс для тестов</p>",
			"published": 0,
			"instructors": [{"instructor": "Administrator"}],
		}
	).insert(ignore_permissions=True).name


def создать_менеджера(почта: str, organization: str) -> str:
	"""Пользователь с ролью менеджера и членством в организации.

	Роль Frappe даёт возможность смотреть отчёты, членство — определяет, по
	каким именно организациям. Без второго роль не открывает ничего.
	"""
	создать_ученика(почта)
	пользователь = frappe.get_doc("User", почта)
	пользователь.add_roles("Organization Manager")
	if not frappe.db.exists(
		"Organization Membership", {"user": почта, "organization": organization}
	):
		frappe.get_doc(
			{
				"doctype": "Organization Membership",
				"user": почта,
				"organization": organization,
				"role": "Manager",
			}
		).insert(ignore_permissions=True)
	return почта


def создать_вопрос(
	текст: str,
	варианты: list[tuple] | None = None,
	возможные_ответы: list[str] | None = None,
	пояснение: str | None = None,
) -> str:
	"""Вопрос с вариантами (Choices) или со свободным вводом (User Input).

	Вариант — `(текст, верный)` или `(текст, верный, пояснение)`. `пояснение`
	без своего у варианта достаётся верным вариантам.
	"""
	поля = {"doctype": "LMS Question", "question": текст}
	if возможные_ответы is not None:
		поля["type"] = "User Input"
		for номер, ответ in enumerate(возможные_ответы, start=1):
			поля[f"possibility_{номер}"] = ответ
	else:
		поля["type"] = "Choices"
		верных = sum(1 for вариант in варианты or [] if вариант[1])
		поля["multiple"] = int(верных > 1)
		for номер, (вариант, верный, *своё) in enumerate(варианты or [], start=1):
			поля[f"option_{номер}"] = вариант
			поля[f"is_correct_{номер}"] = int(верный)
			пояснение_варианта = своё[0] if своё else (пояснение if верный else None)
			if пояснение_варианта:
				поля[f"explanation_{номер}"] = пояснение_варианта
	return frappe.get_doc(поля).insert(ignore_permissions=True).name


def создать_квиз(lesson: str, вопросы: list[str], баллов_за_вопрос: int = 1) -> str:
	"""Квиз урока. Привязывается и через quiz_id урока — так его ищет Frappe."""
	курс = frappe.db.get_value(
		"Course Chapter", frappe.db.get_value("Course Lesson", lesson, "chapter"), "course"
	)
	квиз = frappe.get_doc(
		{
			"doctype": "LMS Quiz",
			"title": f"Квиз {frappe.generate_hash(length=6)}",
			"lesson": lesson,
			"course": курс,
			"total_marks": len(вопросы) * баллов_за_вопрос,
			"passing_percentage": 80,
			"questions": [
				{"question": вопрос, "marks": баллов_за_вопрос} for вопрос in вопросы
			],
		}
	).insert(ignore_permissions=True)
	frappe.db.set_value("Course Lesson", lesson, "quiz_id", квиз.name)
	return квиз.name


def создать_занятие(student: str, lesson: str) -> str:
	"""Занятие с курсом и пространством — по тому же правилу, что у `start_lesson`."""
	from lms_frappe_app.agent_learning.spaces import пространство_курса

	глава = frappe.db.get_value("Course Lesson", lesson, "chapter")
	курс = frappe.db.get_value("Course Chapter", глава, "course")
	return frappe.get_doc(
		{
			"doctype": "Agent Learning Session",
			"student": student,
			"lesson": lesson,
			"course": курс,
			"organization": пространство_курса(student, курс) if курс else None,
		}
	).insert(ignore_permissions=True).name


def создать_домашку(lesson: str, **поля):
	"""Домашнее задание урока (learning-services#439); поля по умолчанию — без срока."""
	return frappe.get_doc(
		{
			"doctype": "Agent Lesson Homework",
			"lesson": lesson,
			"title": "Встреча со спонсором",
			"description": "Проведите встречу и опишите итог.",
			**поля,
		}
	).insert(ignore_permissions=True)


def сдать_отчёт(session: str) -> dict:
	"""Закрывает границу занятия: `covered` по всем целям урока.

	`Why:` тестам про квиз и закрытие урока отчёт не интересен, но без него
	сервер до них не допустит. Повторять сверку целей в каждом тесте значит
	прятать смысл теста за шумом.
	"""
	from lms_frappe_app.api import student

	занятие = frappe.get_doc("Agent Learning Session", session)
	цели = frappe.db.get_value(
		"Agent Lesson Directive",
		действующая("Agent Lesson Directive", {"lesson": занятие.lesson}),
		"objectives",
	)
	строки = [с.strip() for с in (цели or "").splitlines() if с.strip()]
	return student.report_outcomes(
		session, [{"objective": цель, "status": "covered"} for цель in строки]
	)


def зачислить(ученик: str, lesson: str) -> str:
	"""Зачисление на курс урока — основание доступа ко всему учебному потоку."""
	глава = frappe.db.get_value("Course Lesson", lesson, "chapter")
	курс = frappe.db.get_value("Course Chapter", глава, "course")
	if not frappe.db.exists("LMS Enrollment", {"member": ученик, "course": курс}):
		frappe.get_doc(
			{
				"doctype": "LMS Enrollment",
				"member": ученик,
				"course": курс,
				"member_type": "Student",
			}
		).insert(ignore_permissions=True)
	return курс


def политика_по_умолчанию() -> None:
	"""Возвращает общие настройки к значениям, на которые опираются тесты.

	`Why:` настройки — глобальный синглтон, читаемый через кеш документов на
	весь процесс. Первый же тест, изменивший их, ронял бы проверки в других
	файлах, и связь была бы неочевидной.
	"""
	настройки = frappe.get_doc("Agent Learning Settings")
	настройки.update(
		{
			"quiz_required": 1,
			"pass_threshold": 0.8,
			"max_attempts": 3,
			"retry_delay_minutes": 60,
			"session_timeout_hours": 6,
			"carry_over_depth": 3,
			"bridge_after_hours": 24,
			"student_notes_limit": 20,
			"lesson_segment_limit": 6000,
			"web_demo_lessons": 2,
			"agent_service_url": get_url().rstrip("/"),
			"authoring_guide_tool": "authoring_guide",
		}
	)
	настройки.save(ignore_permissions=True)
	frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")


def настроить_квиз(**поля) -> None:
	"""Порог, лимит попыток и паузу задаёт платформа — одна на всех (#353)."""
	настройки = frappe.get_doc("Agent Learning Settings")
	настройки.update(поля)
	настройки.save(ignore_permissions=True)
	frappe.clear_document_cache("Agent Learning Settings", "Agent Learning Settings")
