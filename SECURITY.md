# Безопасность

## Как сообщить об уязвимости

Пишите на **support@cenda.app** с темой «Security: lms_frappe_app». Не
открывайте публичный issue и не присылайте PR с исправлением, пока мы не
ответили: репозиторий публичный, и описание уязвимости в нём видно всем
раньше, чем выйдет исправление.

В письме полезно указать:

- что уязвимо — метод контракта, DocType, права, хранение;
- как воспроизвести: запрос, роль или токен, с которым он отправлен, и ответ;
- что можно получить или изменить — чужие сессии, результаты, данные другой
  команды;
- ревизию (`git rev-parse HEAD`) или дату, на которой проверяли.

Постараемся ответить в течение нескольких рабочих дней: скажем, подтверждается
ли уязвимость и когда ждать исправления. Об исправлении сообщим; если хотите,
упомянем вас в описании изменения.

## Что входит

Код этого репозитория: whitelisted-методы контракта
([CONTRACT.md](CONTRACT.md)), DocType и их права, изоляция команд, проверки
занятия и квиза. Особенно интересно всё, что позволяет ученику своим токеном
обойти правило, которое приложение обязано принуждать (см.
[CONTRIBUTING.md](CONTRIBUTING.md)), или увидеть данные чужой команды.

## Что не входит

- **Frappe Framework** — сообщайте в Frappe: https://frappe.io/security.
- **Frappe Learning** — уязвимость, которая есть и в upstream
  [frappe/lms](https://github.com/frappe/lms), — в Frappe по той же ссылке;
  уязвимость только нашего форка
  [learning-fork](https://github.com/lms-high-time/learning-fork) — на адрес
  выше.
- Закрытый MCP-сервис и стенд — на адрес выше, но вне этого репозитория.

## English

Please report vulnerabilities privately to **support@cenda.app** (subject
"Security: lms_frappe_app") rather than in a public issue or pull request. We
aim to reply within a few business days. Issues in Frappe Framework or upstream
Frappe Learning go to https://frappe.io/security.
