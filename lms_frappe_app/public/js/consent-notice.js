// Подпись о согласии с документами под кнопками входа и регистрации
// (learning-services#577). Принимает их тот, кто создаёт аккаунт: формой
// регистрации или первым входом через Google — кнопка «Войти» там и есть
// регистрация, поэтому подпись стоит и в карточке входа. Запись согласия
// делает сервер (`agent_learning/consent.py`); подпись — то, что человек видел.
//
// Подключён ко всем веб-страницам Frappe (web_include_js) и работает только
// на /login. Текст по языку страницы: переводов для JS веб-страницы Frappe не
// получают, `__()` здесь вернул бы английский и на русской странице.
(function () {
	var ТЕКСТ = {
		ru: [
			"Продолжая, вы принимаете ",
			"Условия использования",
			" и ",
			"Политику конфиденциальности",
			".",
		],
		en: ["By continuing, you accept the ", "Terms of Use", " and the ", "Privacy Policy", "."],
	};
	var КАРТОЧКИ = [".for-login", ".for-email-login", ".for-signup"];

	function подпись() {
		var язык = (document.documentElement.lang || "").slice(0, 2) === "ru" ? "ru" : "en";
		var части = ТЕКСТ[язык];
		var p = document.createElement("p");
		p.className = "consent-notice";
		p.append(
			части[0],
			ссылка("/terms", части[1]),
			части[2],
			ссылка("/privacy", части[3]),
			части[4]
		);
		return p;
	}

	function ссылка(адрес, текст) {
		var a = document.createElement("a");
		a.href = адрес;
		a.target = "_blank";
		a.rel = "noopener";
		a.textContent = текст;
		return a;
	}

	function вставить() {
		if (document.body.dataset.path !== "login") return;
		КАРТОЧКИ.forEach(function (селектор) {
			var карточка = document.querySelector(селектор + " .page-card");
			if (карточка && !карточка.querySelector(".consent-notice")) {
				карточка.appendChild(подпись());
			}
		});
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", вставить);
	} else {
		вставить();
	}
})();
