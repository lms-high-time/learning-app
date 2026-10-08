# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Шаблоны домашек уроков — по действующему релизу каждого курса (learning-services#504).

Публикация проецирует домашку релиза в шаблон урока, а релизы, вышедшие
раньше, шаблонов не получили. Повторная публикация того же файла их не
создаст: тот же дайджест — `unchanged` до любой проекции. Патч безопасно
запускать снова: проекция пишет только расхождения.
"""

import frappe

from lms_frappe_app.agent_learning.releases import homework


def execute():
	for курс in frappe.get_all("LMS Course", filters={"active_release": ("is", "set")}, pluck="name"):
		homework.спроецировать_действующий(курс)
