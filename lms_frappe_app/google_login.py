# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Вход через Google: адрес подтверждён и аккаунт Google тот же (learning-services#666).

`Why:` Frappe склеивает вход через Google с аккаунтом платформы по одному
адресу почты (`frappe.utils.oauth.login_oauth_user`). Подтверждён ли адрес у
Google, он не смотрит — его проверка `email_verified or email` проходит по
одному наличию почты, — а идентификатор аккаунта Google, записанный при
первом входе, со следующими не сверяет. Аккаунт Google с чужим
неподтверждённым адресом или пересозданный на адресе бывшего сотрудника
входил бы в чужой аккаунт платформы (ASVS 6.8.1, самооценка
learning-services#664).

Получение данных и сам вход — функциями Frappe; здесь только проверка между
ними, на месте `login_via_oauth2`.

К `login_via_oauth2("google", …)` во Frappe ведут три метода, подменены все:
`oauth2_logins.login_via_google`, его старое имя `frappe.www.login.login_via_google`
(подмена самого Frappe, её перекрывает наша) и `oauth2_logins.custom` с
провайдером в пути — `/api/method/frappe.integrations.oauth2_logins.custom/google`.
"""

import frappe
from frappe.integrations import oauth2_logins
from frappe.utils.oauth import consume_oauth_state, get_email, get_info_via_oauth, login_oauth_user

from lms_frappe_app.passwords import ВХОД_ЧЕРЕЗ_GOOGLE

ПРОВАЙДЕР = "google"

ЗАГОЛОВОК = "Не удалось войти через Google"
НЕ_ПОДТВЕРЖДЁН = (
	"Google не подтвердил адрес почты этого аккаунта. Подтвердите адрес в настройках Google "
	"или войдите по почте с паролем."
)
ДРУГОЙ_АККАУНТ = (
	"Этот адрес почты на платформе привязан к другому аккаунту Google. Войдите тем аккаунтом "
	"Google, которым входили раньше, или по почте с паролем."
)


def причина_отказа(данные: dict, привязанный: str | None) -> str | None:
	"""Текст отказа или `None`; `привязанный` — идентификатор Google, записанный
	у пользователя платформы с этим адресом.

	Frappe берёт данные из `oauth2/v2/userinfo` (`Social Login Key` Google): там
	флаг подтверждения — `verified_email`, а идентификатор — `id`. В OpenID
	Connect те же поля называются `email_verified` и `sub`, принимаются оба
	написания. Флаг — только булево `True`: строка или его отсутствие — отказ.
	"""
	if данные.get("email_verified") is not True and данные.get("verified_email") is not True:
		return НЕ_ПОДТВЕРЖДЁН
	идентификатор = str(данные.get("sub") or данные.get("id") or "")
	if привязанный and идентификатор != str(привязанный):
		return ДРУГОЙ_АККАУНТ
	return None


def привязанный_идентификатор(данные: dict) -> str | None:
	"""Идентификатор Google у пользователя с адресом из данных Google.

	Имя пользователя — адрес в нижнем регистре: так его ищет и `login_oauth_user`.
	"""
	адрес = (get_email(данные) or "").lower()
	if not адрес:
		return None
	return frappe.db.get_value(
		"User Social Login",
		{"parenttype": "User", "parent": адрес, "provider": ПРОВАЙДЕР},
		"userid",
	)


@frappe.whitelist(allow_guest=True)
def login_via_google(code: str, state: str):
	"""Подмена `frappe.integrations.oauth2_logins.login_via_google`.

	Отказ гасит `state`: попытка входа одноразовая, как и при успешном входе.
	"""
	данные = get_info_via_oauth(ПРОВАЙДЕР, code, decoder=oauth2_logins.decoder_compat)
	if отказ := причина_отказа(данные, привязанный_идентификатор(данные)):
		consume_oauth_state(state)
		frappe.logger("lms_frappe_app.google_login").warning(
			f"Вход через Google отклонён: {отказ} ({get_email(данные)})"
		)
		frappe.respond_as_web_page(ЗАГОЛОВОК, отказ, http_status_code=403, indicator_color="red")
		return
	# Случайный пароль аккаунта, который заведёт первый вход, не проверяется.
	frappe.flags[ВХОД_ЧЕРЕЗ_GOOGLE] = True
	try:
		login_oauth_user(данные, provider=ПРОВАЙДЕР, state=state)
	finally:
		frappe.flags.pop(ВХОД_ЧЕРЕЗ_GOOGLE, None)


def провайдер_из_пути(путь: str) -> str | None:
	"""Провайдер из пути `custom` — так же, как его берёт Frappe:
	`/api/method/<метод>/<провайдер>`."""
	части = путь[1:].split("/")
	return части[3] if len(части) == 4 and части[3] else None


@frappe.whitelist(allow_guest=True)
def custom(code: str, state: str):
	"""Подмена `frappe.integrations.oauth2_logins.custom`: Google — через проверку.

	`Why:` `custom` впускает любого провайдера из `Social Login Key` по имени
	из пути, и `custom/google` шёл в `login_via_oauth2("google", …)` мимо
	подмены `login_via_google`: адрес возврата у Google тот же, и обмен кода
	проходит (learning-services#666). Имя сравнивается без регистра и пробелов
	по краям: MariaDB находит запись `google` и по `Google `, и такой путь
	не должен уходить во Frappe. Остальные провайдеры — во Frappe как есть.
	"""
	провайдер = провайдер_из_пути(frappe.request.path) if frappe.request else None
	if провайдер and провайдер.strip().lower() == ПРОВАЙДЕР:
		return login_via_google(code, state)
	return oauth2_logins.custom(code, state)
