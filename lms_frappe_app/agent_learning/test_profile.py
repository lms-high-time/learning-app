# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Набор ключей профиля ученика (learning-services#463)."""

from frappe.tests import UnitTestCase

from lms_frappe_app.agent_learning.profile import КЛЮЧИ_ПРОФИЛЯ, ПОРОГ_ЗАПОЛНЕННОСТИ, ПРОФИЛЬ


class TestProfileKeys(UnitTestCase):
	def test_ключ_живёт_в_одном_блоке(self):
		"""Ключ в двух блоках показал бы один факт дважды и посчитался бы дважды."""
		ключи = [ключ for _блок, _имя, поля in ПРОФИЛЬ for ключ, _подпись in поля]

		self.assertEqual(len(ключи), len(set(ключи)))

	def test_набор_ключей(self):
		self.assertEqual(len(КЛЮЧИ_ПРОФИЛЯ), 9)

	def test_блоки_не_повторяются(self):
		блоки = [блок for блок, _имя, _поля in ПРОФИЛЬ]

		self.assertEqual(len(блоки), len(set(блоки)))

	def test_порог_достижим(self):
		self.assertLessEqual(ПОРОГ_ЗАПОЛНЕННОСТИ, len(КЛЮЧИ_ПРОФИЛЯ))
