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

Вход через внешний провайдер — только Google (learning-services#691): методы
входа прочих провайдеров Frappe и `custom` с другим провайдером отвечают
отказом. `Why:` подтверждён ли адрес почты, Frappe не проверяет ни у одного
провайдера, а проверка выше написана под ответ Google. Ролям со вторым
фактором (`РОЛИ_С_2FA`) вход через Google закрыт: кода второго фактора он не
спрашивает, и эти роли входят паролем и кодом.
"""

import frappe
from frappe.integrations import oauth2_logins
from frappe.utils import cint
from frappe.utils.oauth import consume_oauth_state, get_email, get_info_via_oauth, login_oauth_user

from lms_frappe_app.install import БЕЗ_2FA, РОЛИ_С_2FA
from lms_frappe_app.passwords import ВХОД_ЧЕРЕЗ_GOOGLE

ПРОВАЙДЕР = "google"
#: Провайдеры, у которых во Frappe свой метод входа `login_via_<провайдер>`,
#: кроме Google. Оба имени каждого метода подменены отказом (hooks.py).
ПРОВАЙДЕРЫ_FRAPPE = ("github", "facebook", "frappe", "office365", "salesforce", "fairlogin", "keycloak")

ЗАГОЛОВОК = "Не удалось войти через Google"
НЕ_ПОДТВЕРЖДЁН = (
	"Google не подтвердил адрес почты этого аккаунта. Подтвердите адрес в настройках Google "
	"или войдите по почте с паролем."
)
ДРУГОЙ_АККАУНТ = (
	"Этот адрес почты на платформе привязан к другому аккаунту Google. Войдите тем аккаунтом "
	"Google, которым входили раньше, или по почте с паролем."
)
СО_ВТОРЫМ_ФАКТОРОМ = (
	"Для этой учётной записи вход через Google закрыт: войдите по почте с паролем и кодом "
	"из приложения-генератора."
)
ДРУГОЙ_ПРОВАЙДЕР = (
	"Этот способ входа на платформе не используется. Войдите через Google или по почте с паролем."
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


def со_вторым_фактором(данные: dict) -> bool:
	"""Есть ли у пользователя с адресом из данных Google роль из `РОЛИ_С_2FA`.

	На сайте с выключенным вторым фактором (`БЕЗ_2FA`, локальная разработка)
	ни у кого его нет, и закрывать Google незачем."""
	if cint(frappe.conf.get(БЕЗ_2FA)):
		return False
	адрес = (get_email(данные) or "").lower()
	return bool(
		адрес
		and frappe.db.exists("Has Role", {"parenttype": "User", "parent": адрес, "role": ["in", РОЛИ_С_2FA]})
	)


@frappe.whitelist(allow_guest=True)
def login_via_google(code: str, state: str):
	"""Подмена `frappe.integrations.oauth2_logins.login_via_google`.

	Отказ гасит `state`: попытка входа одноразовая, как и при успешном входе.
	"""
	данные = get_info_via_oauth(ПРОВАЙДЕР, code, decoder=oauth2_logins.decoder_compat)
	отказ = причина_отказа(данные, привязанный_идентификатор(данные))
	if not отказ and со_вторым_фактором(данные):
		отказ = СО_ВТОРЫМ_ФАКТОРОМ
	if отказ:
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
	"""Подмена `frappe.integrations.oauth2_logins.custom`: Google — через
	проверку, остальные — отказ.

	`Why:` `custom` впускает любого провайдера из `Social Login Key` по имени
	из пути, и `custom/google` шёл в `login_via_oauth2("google", …)` мимо
	подмены `login_via_google`: адрес возврата у Google тот же, и обмен кода
	проходит (learning-services#666). Имя сравнивается без регистра и пробелов
	по краям: MariaDB находит запись `google` и по `Google `.
	"""
	провайдер = провайдер_из_пути(frappe.request.path) if frappe.request else None
	if провайдер and провайдер.strip().lower() == ПРОВАЙДЕР:
		return login_via_google(code, state)
	return отклонить_провайдера(code, state)


@frappe.whitelist(allow_guest=True)
def отклонить_провайдера(code: str | None = None, state: str | None = None):
	"""Подмена методов входа провайдеров, кроме Google: отказ страницей.

	`state` гасится, как и при отказе Google: попытка входа одноразовая.
	"""
	if state:
		consume_oauth_state(state)
	frappe.respond_as_web_page(ЗАГОЛОВОК, ДРУГОЙ_ПРОВАЙДЕР, http_status_code=403, indicator_color="red")
