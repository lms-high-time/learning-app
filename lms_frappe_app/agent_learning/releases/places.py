# Copyright (c) 2026, NikoMusaev and contributors
# For license information, please see license.txt

"""Места релиза по ключам — для заметок автора (learning-services#512).

Место — адрес из `notes.разобрать_адрес`. Здесь оно сверяется с релизом:
есть ли такой ключ, к какому уроку относится, как его назвать словами. Всё,
кроме карты, читается из индекса релиза (`index`): каждая часть — одной
выборкой на релиз и только когда она нужна. Карта есть только в снимке;
снимок большой, а релиз неизменяем, поэтому узлы карты читаются из снимка
один раз на релиз и живут в `frappe.cache` по имени релиза.
"""

from functools import cached_property

import frappe

from lms_frappe_app.agent_learning.releases import index

#: Ключ кэша узлов карты: к нему дописывается имя релиза.
КЭШ_УЗЛОВ = "lms_frappe_app:release_map_nodes"

#: Длина цитаты в подписи: текст цели, вопроса и узла карты.
ДЛИНА_ЦИТАТЫ = 80

#: Подпись места, которого в релизе нет, — по виду адреса и ключу.
ВИД_СЛОВАМИ = {
	"agent.frame": "Пакет агента · рамка курса",
	"chapter": "Глава",
	"lesson": "Урок",
	"objective": "Цель",
	"goal": "Пункт",
	"question": "Вопрос",
	"section": "Раздел документа",
	"agent.lesson": "Пакет агента · урок",
	"agent.item": "Пакет агента · пункт",
	"map": "Карта · узел",
}


class Места:
	"""Места одного релиза курса; `релиз` — `None`, у курса релиза нет и места нет никакого."""

	def __init__(self, релиз: str | None):
		self.релиз = релиз
		self._пакеты: dict[str, dict] = {}

	def место(self, разобранный: dict) -> dict:
		"""`{label, missing, lesson_key, lesson}` места: подпись словами, нет ли его
		в релизе, ключ урока места и его запись `Course Lesson` (у мест вне урока — `None`)."""
		return self.места([разобранный])[0]

	def места(self, адреса: list[dict]) -> list[dict]:
		"""`место` для каждого адреса; срезы пакета агента нужных уроков — одной выборкой."""
		уроки = {self._урок_и_пункт(а["key"])[0] for а in адреса if а["kind"] == "agent.item"}
		нужны = sorted(урок for урок in уроки if урок in self.уроки and урок not in self._пакеты)
		if нужны:
			self._пакеты.update(index.пакеты_уроков(self.релиз, нужны))
		return [self._место(а) for а in адреса]

	# --- части индекса: каждая читается один раз ---

	@cached_property
	def уроки(self) -> dict[str, dict]:
		"""Ключ урока → `{number, title, lesson}`; номер — по порядку релиза."""
		if not self.релиз:
			return {}
		return {
			у.lesson_key: {"number": номер, "title": у.title, "lesson": у.lesson}
			for номер, у in enumerate(index.уроки(self.релиз), start=1)
		}

	@cached_property
	def главы(self) -> dict[str, dict]:
		if not self.релиз:
			return {}
		return {г["key"]: {"number": номер, **г} for номер, г in enumerate(index.главы(self.релиз), start=1)}

	@cached_property
	def цели(self) -> tuple[dict[str, tuple[str, str]], dict[tuple[str, str], str]]:
		"""(ключ цели → (ключ урока, текст), (ключ урока, ключ пункта) → название пункта)."""
		цели: dict[str, tuple[str, str]] = {}
		пункты: dict[tuple[str, str], str] = {}
		for урок, список in (index.цели_с_пунктами(self.релиз) if self.релиз else {}).items():
			for цель in список:
				цели[цель["key"]] = (урок, цель["text"])
				for пункт in цель["goals"]:
					пункты[(урок, пункт["key"])] = пункт["title"]
		return цели, пункты

	@cached_property
	def вопросы(self) -> dict[str, tuple[str, str]]:
		"""Ключ вопроса → (ключ урока, текст)."""
		return {
			вопрос["key"]: (урок, вопрос["text"])
			for урок, список in (index.вопросы(self.релиз) if self.релиз else {}).items()
			for вопрос in список
		}

	@cached_property
	def разделы(self) -> dict[str, dict]:
		return index.разделы(self.релиз) if self.релиз else {}

	@cached_property
	def рамка(self) -> bool:
		return bool(self.релиз and index.рамка(self.релиз))

	@cached_property
	def узлы(self) -> dict[str, str]:
		return узлы_карты(self.релиз) if self.релиз else {}

	# --- подпись ---

	def _место(self, а: dict) -> dict:
		вид, ключ = а["kind"], а["key"]
		урок = None
		if вид == "course":
			подпись = "Курс"
		elif вид == "agent.frame":
			подпись = ВИД_СЛОВАМИ[вид] if self.рамка else None
		elif вид == "chapter":
			г = self.главы.get(ключ)
			подпись = f"Глава {г['number']} «{г['title']}»" if г else None
		elif вид in ("lesson", "agent.lesson"):
			урок = ключ
			хвост = " · пакет агента" if вид == "agent.lesson" else ""
			подпись = self._урок(урок) + хвост if урок in self.уроки else None
		elif вид == "objective":
			урок, текст = self.цели[0].get(ключ, (None, None))
			подпись = f"{self._урок(урок)} · цель «{коротко(текст)}»" if урок in self.уроки else None
		elif вид == "question":
			урок, текст = self.вопросы.get(ключ, (None, None))
			подпись = f"{self._урок(урок)} · вопрос «{коротко(текст)}»" if урок in self.уроки else None
		elif вид == "goal":
			урок, пункт = self._урок_и_пункт(ключ)
			название = self.цели[1].get((урок, пункт))
			подпись = f"{self._урок(урок)} · пункт «{название}»" if название is not None else None
		elif вид == "agent.item":
			урок, пункт = self._урок_и_пункт(ключ)
			пакет = self._пакеты.get(урок) or {}
			есть = урок in self.уроки and isinstance(пакет.get("items"), dict) and пункт in пакет["items"]
			название = self.цели[1].get((урок, пункт), пункт) if есть else None
			подпись = f"{self._урок(урок)} · пакет агента · пункт «{название}»" if есть else None
		elif вид == "section":
			р = self.разделы.get(ключ)
			подпись = f"Документ · раздел «{р['title']}»" if р else None
		elif вид == "map":
			текст = self.узлы.get(ключ)
			подпись = f"Карта · «{текст}»" if текст else None
		else:
			подпись = None
		нет = подпись is None
		if нет:
			# Вид не из `ВИД_СЛОВАМИ` — адрес не по ключам релиза: подпись — он сам.
			слово = ВИД_СЛОВАМИ.get(вид)
			подпись = (f"{слово} {ключ}" if ключ else слово) if слово else вид
		запись = self.уроки.get(урок) if урок else None
		return {
			"label": подпись,
			"missing": нет,
			"lesson_key": урок if запись else None,
			"lesson": запись["lesson"] if запись else None,
		}

	def _урок(self, ключ: str) -> str:
		урок = self.уроки[ключ]
		return f"Урок {урок['number']} «{урок['title']}»"

	def _урок_и_пункт(self, ключ: str) -> tuple[str, str]:
		"""`<урок>/<пункт>` по ключам уроков релиза: ключ урока сам бывает с `/`.
		Ни один урок не подошёл — по первому `/`."""
		подходят = [урок for урок in self.уроки if ключ.startswith(урок + "/") and len(ключ) > len(урок) + 1]
		if подходят:
			урок = max(подходят, key=len)
			return урок, ключ[len(урок) + 1 :]
		урок, _, пункт = ключ.partition("/")
		return урок, пункт


def узлы_карты(релиз: str) -> dict[str, str]:
	"""Узлы карты релиза: ключ → текст для подписи.

	Карта непрозрачна: узел — объект с непустым строковым `text` или `title`
	под своим ключом на любой глубине карты; ключ встретился дважды — главнее
	первый. Читается из снимка один раз на релиз, дальше — из кэша.
	"""
	ключ_кэша = f"{КЭШ_УЗЛОВ}:{релиз}"
	узлы = frappe.cache.get_value(ключ_кэша)
	if узлы is None:
		узлы = {}
		_собрать_узлы(index.снимок(релиз).get("map"), узлы)
		frappe.cache.set_value(ключ_кэша, узлы)
	return узлы


def _собрать_узлы(значение, узлы: dict[str, str]) -> None:
	if isinstance(значение, dict):
		for ключ, вложенное in значение.items():
			if isinstance(вложенное, dict):
				текст = next(
					(т for т in (вложенное.get("text"), вложенное.get("title")) if isinstance(т, str) and т.strip()),
					None,
				)
				if текст:
					узлы.setdefault(str(ключ), коротко(текст))
			_собрать_узлы(вложенное, узлы)
	elif isinstance(значение, list):
		for элемент in значение:
			_собрать_узлы(элемент, узлы)


def коротко(текст: str | None, предел: int = ДЛИНА_ЦИТАТЫ) -> str:
	текст = " ".join((текст or "").split())
	return текст if len(текст) <= предел else текст[: предел - 1].rstrip() + "…"
