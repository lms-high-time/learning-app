// Согласие с документами на /login (learning-services#577, #624).
// Форма регистрации — две обязательные галочки: «мне есть 18 лет» и
// «принимаю Условия и Политику». Без них отправка останавливается, с ними
// ставится cookie `cenda_signup_marks`, по которому сервер запишет согласие явным
// (`agent_learning/consent.py`). В карточке входа — подпись: вход через Google
// создаёт аккаунт при возврате от провайдера, отметок до него не спросить, и
// новый пользователь отметит их на экране `/consent`.
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
	const ОТМЕТКИ = {
		ru: {
			возраст: "Мне исполнилось 18 лет",
			документы: ["Я принимаю ", "Условия использования", " и ", "Политику конфиденциальности"],
			ошибка: "Чтобы зарегистрироваться, подтвердите возраст и примите условия.",
		},
		en: {
			возраст: "I am 18 or older",
			документы: ["I accept the ", "Terms of Use", " and the ", "Privacy Policy"],
			ошибка: "To sign up, confirm your age and accept the terms.",
		},
	};
	const КАРТОЧКИ = [".for-login", ".for-email-login"];
	const ФОРМА = ".for-signup:not(.signup-disabled) form";
	// Короткий срок: cookie нужна одному запросу регистрации, сервер её сразу
	// стирает. Имя своё: `cenda_consent` — выбор по аналитике (analytics.js).
	const COOKIE = "cenda_signup_marks=1; path=/; max-age=600; SameSite=Lax; Secure";

	const ссылка = (адрес, текст) => {
		const a = document.createElement("a");
		a.href = адрес;
		a.target = "_blank";
		a.rel = "noopener";
		a.textContent = текст;
		return a;
	};

	const язык = () => ((document.documentElement.lang || "").slice(0, 2) === "ru" ? "ru" : "en");

	const галочка = (имя, ...содержимое) => {
		const label = document.createElement("label");
		label.className = "consent-check";
		const поле = document.createElement("input");
		поле.type = "checkbox";
		поле.name = имя;
		поле.required = true;
		const текст = document.createElement("span");
		текст.append(...содержимое);
		label.append(поле, текст);
		return label;
	};

	const галочки = (форма) => {
		const т = ОТМЕТКИ[язык()];
		const [начало, условия, союз, политика] = т.документы;
		const блок = document.createElement("div");
		блок.className = "consent-checks";
		const ошибка = document.createElement("p");
		ошибка.className = "field-error text-danger consent-error";
		ошибка.setAttribute("role", "alert");
		блок.append(
			галочка("cenda_age", т.возраст),
			галочка("cenda_documents", начало, ссылка("/terms", условия), союз, ссылка("/privacy", политика)),
			ошибка,
		);
		(форма.querySelector(".page-card-body") || форма).appendChild(блок);
	};

	// Перехват — на документе в фазе захвата: раньше обработчика формы Frappe
	// Learning, который сам шлёт запрос регистрации. Отправку через jQuery
	// `.trigger("submit")` он не увидит — для этой формы её никто не зовёт.
	const проверить = (событие) => {
		const форма = событие.target.closest && событие.target.closest(ФОРМА);
		if (!форма) return;
		const отмечено = [...форма.querySelectorAll(".consent-check input")].every((поле) => поле.checked);
		const ошибка = форма.querySelector(".consent-error");
		if (!отмечено) {
			событие.preventDefault();
			событие.stopImmediatePropagation();
			if (ошибка) ошибка.textContent = ОТМЕТКИ[язык()].ошибка;
			return;
		}
		if (ошибка) ошибка.textContent = "";
		document.cookie = COOKIE;
	};

	const подпись = () => {
		const [начало, условия, союз, политика, конец] = ТЕКСТ[язык()];
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
		const форма = document.querySelector(ФОРМА);
		if (форма && !форма.querySelector(".consent-checks")) галочки(форма);
		document.addEventListener("submit", проверить, true);
		// Форма Learning при смене карточки снимает отметки — снимаем и ошибку.
		window.addEventListener("hashchange", () => {
			document.querySelectorAll(".consent-error").forEach((ошибка) => (ошибка.textContent = ""));
		});
	};

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", вставить);
	} else {
		вставить();
	}
})();
