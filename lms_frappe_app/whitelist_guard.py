# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Подменённые методы — только под своим HTTP-именем (learning-services#692).

У каждой функции, подменённой `override_whitelisted_methods`, оригинал снят
из реестров Frappe (`frappe.whitelisted`, `frappe.guest_methods`,
`frappe.xss_safe_methods`): под исходным именем запрос уходит в подмену, под
другим путём модуля — отказ «не whitelisted». `Why:` Frappe сверяет с реестром
объект функции, а подмена — строку вызова. Подмены зовут оригинал прямым
вызовом Python, реестр им не нужен.

Реестр живёт в памяти процесса и заполняется при импорте модулей, поэтому
снятие идёт на каждом запросе и задаче (`before_request`, `before_job`): оно
идемпотентно и сначала импортирует модуль оригинала — иначе тот
зарегистрировался бы уже после снятия.

Чего не делает: не разделяет сайты. Реестр общий на процесс, и на bench с
несколькими сайтами, где у части приложение не стоит, снятие задело бы и их:
там исходный метод перестал бы отвечать. На стенде cenda.app сайт один.

Методы приложения payments снимаются из реестров целиком, его страницы
отвечают 404 (`снять_методы_payments`, `СтраницаPayments`,
learning-services#679): его требует Learning (`required_apps`), а оплаты
через его шлюзы на платформе нет. Снятие — только на запросе: HTTP в
фоновых задачах нет, а воркер rq форкает процесс на каждую задачу, и импорт
payments шёл бы в каждой. Оговорка про несколько сайтов — та же.
Включат платные курсы — снимать: ссылка на оплату Learning
(`get_payment_link`) ведёт на страницу шлюза.
"""

import functools
import pkgutil
import threading
from collections.abc import Callable
from importlib import import_module
from pathlib import Path

import frappe
from frappe.website.page_renderers.not_found_page import NotFoundPage

# Модули payments, импортированные в этом процессе; `None` — ещё нет.
_модули_payments: list[str] | None = None
_импорт_payments = threading.Lock()


def снять_подменённые(*args, **kwargs) -> None:
	"""Убирает из реестров whitelisted-методов оригиналы всех подменённых функций.

	Функцию, которая сама — цель какой-нибудь подмены, не трогает: иначе
	цепочка подмен (A → B, B → C) лишилась бы рабочего метода.
	"""
	цели, оригиналы = set(), []
	for исходное, подмены in frappe.get_hooks("override_whitelisted_methods").items():
		if подмены and (цель := _функция(подмены[-1])) is not None:
			цели.add(цель)
		if (оригинал := _функция(исходное)) is not None:
			оригиналы.append(оригинал)
	for функция in оригиналы:
		if функция in цели:
			continue
		frappe.whitelisted.discard(функция)
		frappe.guest_methods.discard(функция)
		frappe.xss_safe_methods.discard(функция)
		frappe.allowed_http_methods_for_whitelisted_func.pop(функция, None)


def снять_методы_payments(*args, **kwargs) -> None:
	"""Убирает из реестров whitelisted-методов все функции приложения payments.

	Из питона они по-прежнему вызываются — реестр нужен только HTTP. Отправка
	веб-формы, которую payments подменял, идёт штатным методом Frappe
	(подмена в `hooks.py`).

	Реестр заполняется при импорте модуля, поэтому сначала импортируются
	модули payments: модуль, импортированный уже после снятия, зарегистрировал
	бы свои методы заново. Каталоги без `__init__.py` обход не видит — что в
	них нет whitelisted-функций, проверяет тест.
	"""
	_импортировать_payments()
	for функция in list(frappe.whitelisted):
		if not _из_payments(функция):
			continue
		frappe.whitelisted.discard(функция)
		frappe.guest_methods.discard(функция)
		frappe.xss_safe_methods.discard(функция)
		frappe.allowed_http_methods_for_whitelisted_func.pop(функция, None)


class СтраницаPayments(NotFoundPage):
	"""Страница сайта из приложения payments (шлюзы, итог оплаты) — 404: её
	`get_context` исполнял бы код шлюза по запросу без входа.

	Рендерер создаётся на каждый запрос страницы сайта, поэтому до
	`can_render` он только сверяет путь со списком страниц payments, а
	страницу «не найдено» собирает уже в `render`. Опубликованная Web Form
	или Web Page с тем же маршрутом тоже ответит 404.
	"""

	def __init__(self, path, http_status_code=None):
		self._путь = path.strip("/ ")

	def can_render(self):
		return self._путь in _страницы_payments()

	def render(self):
		# Frappe передаёт рендерерам статус 200, а `NotFoundPage` оставляет
		# переданный: страница «не найдено» ушла бы с кодом 200.
		super().__init__(self._путь, 404)
		return super().render()


@functools.cache
def _страницы_payments() -> frozenset[str]:
	"""Маршруты страниц payments — имена шаблонов `templates/pages`."""
	try:
		каталог = Path(frappe.get_app_path("payments", "templates", "pages"))
	except Exception:
		return frozenset()
	return frozenset(файл.stem for файл in каталог.glob("*") if файл.suffix in (".html", ".md"))


def _из_payments(функция) -> bool:
	return (getattr(функция, "__module__", None) or "").split(".")[0] == "payments"


def _импортировать_payments() -> None:
	"""Импортирует все модули payments, кроме тестов, — один раз на процесс.

	Под замком: в потоковом воркере второй запрос иначе прошёл бы мимо
	незаконченного импорта и снял бы методы с неполного реестра. Модуль,
	который не импортируется (нет библиотеки шлюза), не вызывается и по HTTP —
	его пропускаем.
	"""
	global _модули_payments
	if _модули_payments is not None:
		return
	with _импорт_payments:
		if _модули_payments is not None:
			return
		модули = []
		try:
			payments = import_module("payments")
		except ImportError:
			_модули_payments = модули
			return
		for модуль in pkgutil.walk_packages(payments.__path__, "payments.", onerror=lambda имя: None):
			части = модуль.name.split(".")
			if "tests" in части or части[-1].startswith("test_"):
				continue
			try:
				import_module(модуль.name)
			except Exception:
				continue
			модули.append(модуль.name)
		_модули_payments = модули


def _функция(путь: str) -> Callable | None:
	"""Функция по пути или `None`, если её в установке нет.

	Путь без точки Frappe ищет в `frappe.handler` (`handler.get_attr`), так же
	ищется и здесь.

	`frappe.get_attr` отказывает через `frappe.throw`, и сообщение осталось бы в
	ответе запроса — его убираем.
	"""
	if "." not in путь:
		from frappe import handler

		return getattr(handler, путь, None)
	журнал = getattr(frappe.local, "message_log", None)
	было = len(журнал) if журнал is not None else 0
	try:
		return frappe.get_attr(путь)
	except Exception:
		if журнал is not None:
			del журнал[было:]
		return None
