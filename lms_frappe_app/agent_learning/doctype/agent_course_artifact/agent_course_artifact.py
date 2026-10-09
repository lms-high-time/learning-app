# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from lms_frappe_app.agent_learning.errors import запретить_угловые

РАСКЛАДКИ = ("sections", "canvas")


def нормализовать_ключ(значение: str | None) -> str:
	"""Ключи блоков и документов сравниваются без учёта регистра и пробелов."""
	return (значение or "").strip().lower()


class AgentCourseArtifact(Document):
	"""Схема артефакта: из каких блоков он состоит и как рисуется.

	Схема версионируется: правка схемы не должна менять смысл того, что
	ученик уже заполнил. Содержимое хранится по ключу блока, поэтому
	добавленный блок появится пустым, а убранный — исчезнет со страницы, не
	стирая написанного. Версии считаются по паре «курс + ключ документа»: в
	одном курсе несколько документов, и версии у каждого свои. Действующая
	версия у документа одна; прежние остаются в базе.

	Схема адресована автору: роль `LMS Student` прав на неё не имеет, ученик
	получает блоки вместе с содержимым через whitelisted-метод.
	"""

	def before_insert(self):
		# Версию всегда считает система: поле read_only, и переданное значение
		# намеренно затирается. Иначе импорт схемы с проставленной версией
		# тихо создаст вторую «версию 1» того же документа.
		self.version = self._следующая_версия()

	def validate(self):
		запретить_угловые(self.title, "title")
		if self.version < 1:
			frappe.throw(frappe._("Версия схемы начинается с единицы"))
		self.slug = нормализовать_ключ(self.slug)
		if not self.slug:
			frappe.throw(frappe._("Ключ артефакта обязателен"))
		if self.layout not in РАСКЛАДКИ:
			self.layout = РАСКЛАДКИ[0]

		ключи = [нормализовать_ключ(блок.block_key) for блок in self.blocks]
		if any(not ключ for ключ in ключи):
			frappe.throw(frappe._("У каждого блока артефакта есть ключ"))
		if len(ключи) != len(set(ключи)):
			frappe.throw(frappe._("Ключи блоков в артефакте не повторяются"))
		for блок, ключ in zip(self.blocks, ключи, strict=True):
			блок.block_key = ключ
			# Ширина меньше единицы — блок, которого на канвасе не видно.
			блок.span = max(1, int(блок.span or 1))

	def on_update(self):
		if self.is_active:
			self._снять_с_действия_прочие()

	@property
	def _документ(self) -> dict:
		"""Поля, по которым версии считаются версиями одного документа."""
		return {"course": self.course, "slug": нормализовать_ключ(self.slug)}

	def _следующая_версия(self) -> int:
		прошлые = frappe.get_all(
			self.doctype,
			filters=self._документ,
			pluck="version",
			order_by="version desc",
			limit=1,
		)
		return (прошлые[0] if прошлые else 0) + 1

	def _снять_с_действия_прочие(self) -> None:
		прочие = frappe.get_all(
			self.doctype,
			filters={**self._документ, "is_active": 1, "name": ("!=", self.name)},
			pluck="name",
		)
		for имя in прочие:
			# set_value, а не сохранение документа: чужие версии трогаем точечно
			# и без повторного запуска хуков, иначе получим рекурсию.
			frappe.db.set_value(self.doctype, имя, "is_active", 0, update_modified=False)
