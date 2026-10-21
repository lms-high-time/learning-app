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
"""

import frappe
from frappe.integrations.oauth2_logins import decoder_compat
from frappe.utils.oauth import consume_oauth_state, get_email, get_info_via_oauth, login_oauth_user

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
	данные = get_info_via_oauth(ПРОВАЙДЕР, code, decoder=decoder_compat)
	if отказ := причина_отказа(данные, привязанный_идентификатор(данные)):
		consume_oauth_state(state)
		frappe.logger("lms_frappe_app.google_login").warning(
			f"Вход через Google отклонён: {отказ} ({get_email(данные)})"
		)
		frappe.respond_as_web_page(ЗАГОЛОВОК, отказ, http_status_code=403, indicator_color="red")
		return
	login_oauth_user(данные, provider=ПРОВАЙДЕР, state=state)
