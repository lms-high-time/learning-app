"""Прогон одной части серверных тестов на машине CI-матрицы.

Запуск из каталога `sites` интерпретатором окружения bench:

    <bench>/env/bin/python run_tests_part.py <сайт> <приложение> <часть> <всего частей>

Модули с тестами ищутся по тем же правилам, что у `bench run-tests`, и
раскладываются по весу: самый тяжёлый модуль — в самую лёгкую часть. Вес —
число `def test_` в модуле с интеграционными тестами, у остальных — единица:
юнит-тесты идут миллисекунды, время части делают интеграционные. Раскладка
зависит только от файлов, поэтому каждая машина считает её одинаково, и
модуль попадает ровно в одну часть.

Why: `bench run-parallel-tests` гоняет тесты другим раннером — без деления на
unit и integration и без подготовки интеграционных тестов, которую делает
`run-tests`. Здесь часть запускается тем же `main`, что вызывает
`bench run-tests`, только со списком модулей вместо одного.
"""

import importlib
import os
import sys
from pathlib import Path

# Каталоги, которые discovery Frappe (frappe.testing.discovery) не обходит.
SKIP_DIRS = {"node_modules", "locals", "public", "__pycache__"}


def test_modules(app: str) -> dict[str, int]:
	"""Модули с тестами приложения и их вес."""
	app_path = Path(importlib.import_module(app).__path__[0])
	modules = {}
	for path, folders, files in os.walk(app_path):
		folders[:] = [f for f in folders if not f.startswith(".") and f not in SKIP_DIRS]
		if os.path.sep.join(["doctype", "doctype", "boilerplate"]) in path:
			continue
		for filename in files:
			if filename.startswith("test_") and filename.endswith(".py") and filename != "test_runner.py":
				file = Path(path, filename)
				name = ".".join(file.relative_to(app_path.parent).with_suffix("").parts)
				source = file.read_text()
				weight = source.count("def test_") if "IntegrationTestCase" in source else 0
				modules[name] = max(weight, 1)
	return modules


def split(modules: dict[str, int], parts: int) -> list[list[str]]:
	"""Раскладка модулей по частям: тяжёлые первыми, каждый — в самую лёгкую часть."""
	buckets = [[] for _ in range(parts)]
	weights = [0] * parts
	for name in sorted(modules, key=lambda m: (-modules[m], m)):
		lightest = weights.index(min(weights))
		buckets[lightest].append(name)
		weights[lightest] += modules[name]
	return [sorted(b) for b in buckets]


def main() -> None:
	site, app, part, parts = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
	modules = test_modules(app)
	buckets = split(modules, parts)

	# Пустой список модулей `run-tests` понимает как «все тесты приложения».
	assert all(buckets), "часть без модулей"
	assert sorted(m for b in buckets for m in b) == sorted(modules), "раскладка теряет или дублирует модули"

	mine = buckets[part - 1]
	print(f"Часть {part}/{parts}: модулей {len(mine)} из {len(modules)}, вес {sum(modules[m] for m in mine)}")
	for name in mine:
		print(f"  {name}")
	sys.stdout.flush()

	import frappe
	from frappe.commands.testing import main as run_tests

	frappe.init(site)
	if not frappe.conf.allow_tests:
		sys.exit(f"На сайте {site} тесты выключены: allow_tests")
	run_tests(site=site, app=app, module=mine)


if __name__ == "__main__":
	main()
