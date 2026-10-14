// Подпись о согласии с документами в карточках входа и регистрации
// (learning-services#577). Принимает их тот, кто создаёт аккаунт: формой
// регистрации или первым входом через Google — кнопка «Войти» там и есть
// регистрация, поэтому подпись стоит и в карточке входа. Запись согласия
// делает сервер (`agent_learning/consent.py`); подпись — то, что человек видел.
//
// Подключён ко всем веб-страницам Frappe (web_include_js) и работает только
// на /login. Текст по языку страницы: переводов для JS веб-страницы Frappe не
// получают, `__()` здесь вернул бы английский и на русской странице.
(() => {
	const ТЕКСТ = {
		ru: [
			"Продолжая, вы принимаете ",
			"Условия использования",
			" и ",
			"Политику конфиденциальности",
			".",
		],
		en: ["By continuing, you accept the ", "Terms of Use", " and the ", "Privacy Policy", "."],
	};
	// Карточка регистрации при выключенной регистрации — сообщение «Signup
	// Disabled»: соглашаться там не с чем.
	const КАРТОЧКИ = [".for-login", ".for-email-login", ".for-signup:not(.signup-disabled)"];

	const ссылка = (адрес, текст) => {
		const a = document.createElement("a");
		a.href = адрес;
		a.target = "_blank";
		a.rel = "noopener";
		a.textContent = текст;
		return a;
	};

	const подпись = () => {
		const язык = (document.documentElement.lang || "").slice(0, 2) === "ru" ? "ru" : "en";
		const [начало, условия, союз, политика, конец] = ТЕКСТ[язык];
		const p = document.createElement("p");
		p.className = "consent-notice";
		p.append(начало, ссылка("/terms", условия), союз, ссылка("/privacy", политика), конец);
		return p;
	};

	const вставить = () => {
		if (document.body.dataset.path !== "login") return;
		for (const селектор of КАРТОЧКИ) {
			const карточка = document.querySelector(`${селектор} .page-card`);
			if (карточка && !карточка.querySelector(".consent-notice")) {
				карточка.appendChild(подпись());
			}
		}
	};

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", вставить);
	} else {
		вставить();
	}
})();
