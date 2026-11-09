# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt

"""Секреты сайта с одной целью заводятся один раз (learning-services#690)."""

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from lms_frappe_app import site_secrets


class UnitTestSiteSecrets(UnitTestCase):
	def test_заводятся_один_раз_и_видны_в_процессе(self):
		with patch.dict(frappe.conf), patch("frappe.installer.update_site_config") as запись:
			for ключ in site_secrets.ВСЕ:
				frappe.conf.pop(ключ, None)

			site_secrets.обеспечить()
			site_secrets.обеспечить()

			self.assertEqual([вызов.args[0] for вызов in запись.call_args_list], list(site_secrets.ВСЕ))
			значения = [вызов.args[1] for вызов in запись.call_args_list]
			for ключ, значение in zip(site_secrets.ВСЕ, значения, strict=True):
				self.assertRegex(значение, r"^[0-9a-f]{64}$")
				self.assertEqual(frappe.conf[ключ], значение)
			self.assertEqual(len(set(значения)), len(значения), "у каждой цели свой секрет")

	def test_заведённый_не_трогается(self):
		with patch.dict(frappe.conf, {ключ: "ab" * 32 for ключ in site_secrets.ВСЕ}), patch(
			"frappe.installer.update_site_config"
		) as запись:
			site_secrets.обеспечить()

			запись.assert_not_called()

	def test_заводит_установка_и_миграция(self):
		"""Не лениво на запросе: веб-воркеры завели бы наперегонки разные значения."""
		import inspect

		from lms_frappe_app import install

		for хук in (install.after_install, install.after_migrate):
			with self.subTest(хук.__name__):
				self.assertIn("site_secrets.обеспечить()", inspect.getsource(хук))
