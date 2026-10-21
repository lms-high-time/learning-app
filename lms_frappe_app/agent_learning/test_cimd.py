# Copyright (c) 2026, NikoMusaev and Contributors
# See license.txt
"""Клиенты OAuth по адресу документа: проверки без базы (learning-services#661)."""

import socket
from unittest import mock

from frappe.tests import UnitTestCase

from lms_frappe_app.agent_learning import cimd
from lms_frappe_app.agent_learning.oauth_server import адрес_возврата_разрешён

АДРЕС = "https://claude.ai/oauth/claude-code-client-metadata"

#: Документ Claude Code — как его отдаёт claude.ai.
ДОКУМЕНТ = {
	"client_id": АДРЕС,
	"client_name": "Claude Code",
	"client_uri": "https://claude.ai",
	"redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
	"grant_types": ["authorization_code", "refresh_token"],
	"response_types": ["code"],
	"token_endpoint_auth_method": "none",
}


def _адреса(*ip):
	return [
		(socket.AF_INET6 if ":" in а else socket.AF_INET, socket.SOCK_STREAM, 6, "", (а, 443)) for а in ip
	]


class UnitTestCIMD(UnitTestCase):
	def test_адрес_клиента(self):
		for client_id, ожидание in (
			(АДРЕС, True),
			("https://agent.example.com:443/meta.json", True),
			("abc123", False),
			("http://claude.ai/oauth/meta", False),
			("https://claude.ai", False),
			("https://claude.ai/", False),
			("https://claude.ai/meta?x=1", False),
			("https://claude.ai/meta#x", False),
			("https://user@claude.ai/meta", False),
			("https://claude.ai:8443/meta", False),
			("https://claude.ai/<b>", False),
			("https://claude.ai/a/../meta", False),
			("https://claude.ai/./meta", False),
			("https://claude.ai/a/%2e%2E/meta", False),
			("https://claude.ai/" + "a" * 140, False),
			(None, False),
		):
			with self.subTest(client_id):
				self.assertIs(cimd.это_адрес_клиента(client_id), ожидание)

	def test_поля_клиента_из_документа(self):
		поля = cimd.поля_клиента(АДРЕС, ДОКУМЕНТ)
		self.assertEqual(поля["app_name"], "Claude Code")
		self.assertEqual(поля["redirect_uris"], "http://localhost/callback http://127.0.0.1/callback")
		self.assertEqual(поля["default_redirect_uri"], "http://localhost/callback")
		self.assertEqual(поля["client_uri"], "https://claude.ai")
		self.assertEqual(поля["token_endpoint_auth_method"], "None")

	def test_документ_не_принимается(self):
		"""Отказы спецификации: чужой client_id, нет имени или адресов, секрет."""
		for что, правка in (
			("client_id не совпадает с адресом", {"client_id": "https://evil.example/meta"}),
			("client_id с другим регистром", {"client_id": АДРЕС.replace("claude", "Claude")}),
			("нет имени", {"client_name": " "}),
			("нет адресов возврата", {"redirect_uris": []}),
			("адрес возврата http не на этот компьютер", {"redirect_uris": ["http://evil.example/cb"]}),
			("адрес возврата с якорем", {"redirect_uris": ["https://agent.example/cb#x"]}),
			("клиент с секретом", {"token_endpoint_auth_method": "client_secret_basic"}),
			(
				"private_key_jwt без none в поддержанных",
				{
					"token_endpoint_auth_method": "private_key_jwt",
					"token_endpoint_auth_methods_supported": ["private_key_jwt"],
				},
			),
			(
				"поддержанные — не список",
				{
					"token_endpoint_auth_method": "private_key_jwt",
					"token_endpoint_auth_methods_supported": "none",
				},
			),
			("секрет в документе", {"client_secret": "s"}),
			("нет authorization_code", {"grant_types": ["client_credentials"]}),
			("grant_types не список", {"grant_types": 1}),
			("response_types не список", {"response_types": "code"}),
		):
			with self.subTest(что), self.assertRaises(cimd.ДокументНеПринят):
				cimd.поля_клиента(АДРЕС, {**ДОКУМЕНТ, **правка})
		with self.subTest("не объект"), self.assertRaises(cimd.ДокументНеПринят):
			cimd.поля_клиента(АДРЕС, [ДОКУМЕНТ])

	def test_документ_chatgpt_принимается_публичным(self):
		"""ChatGPT предпочитает private_key_jwt, но умеет и none — клиент
		заводится публичным; без этого ChatGPT не подключался вовсе
		(learning-services#663). Документ — как его отдаёт chatgpt.com."""
		адрес = "https://chatgpt.com/oauth/okEdNMt96pKW/client.json"
		документ = {
			"client_id": адрес,
			"client_uri": "https://chatgpt.com/",
			"redirect_uris": ["https://chatgpt.com/connector/oauth/okEdNMt96pKW"],
			"token_endpoint_auth_method": "private_key_jwt",
			"token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
			"grant_types": ["authorization_code", "refresh_token"],
			"response_types": ["code"],
			"client_name": "ChatGPT",
			"logo_uri": "https://persistent.oaistatic.com/sonic/misc/openai-logo.png",
			"token_endpoint_auth_signing_alg": "RS256",
			"jwks_uri": "https://chatgpt.com/oauth/jwks.json",
		}
		поля = cimd.поля_клиента(адрес, документ)
		self.assertEqual(поля["token_endpoint_auth_method"], "None")

	def test_разметка_и_длинные_ссылки_не_берутся(self):
		поля = cimd.поля_клиента(
			АДРЕС,
			{**ДОКУМЕНТ, "logo_uri": "javascript:alert(1)", "client_uri": "https://x.example/" + "a" * 140},
		)
		self.assertIsNone(поля["logo_uri"])
		self.assertIsNone(поля["client_uri"])

	def test_срок_кэша(self):
		for заголовок, срок in (
			("public, max-age=300", 300),
			("max-age=5", cimd.КЭШ_НЕ_МЕНЬШЕ),
			("max-age=999999999", cimd.КЭШ_НЕ_БОЛЬШЕ),
			("no-store", cimd.КЭШ_НЕ_МЕНЬШЕ),
			('max-age="abc"', cimd.КЭШ_ПО_УМОЛЧАНИЮ),
			(None, cimd.КЭШ_ПО_УМОЛЧАНИЮ),
		):
			with self.subTest(заголовок):
				self.assertEqual(cimd.срок_кэша(заголовок), срок)

	def test_внутренние_адреса_хоста_отклоняются(self):
		"""Запрос по чужой ссылке не уходит в сеть стенда: ни один адрес хоста
		не может быть внутренним, иначе подключение ходило бы к базе и redis."""
		for что, адреса in (
			("частная сеть", ("10.0.0.5",)),
			("этот компьютер", ("127.0.0.1",)),
			("метаданные облака", ("169.254.169.254",)),
			("IPv6 локальный", ("::1",)),
			("публичный вместе с внутренним", ("93.184.216.34", "192.168.1.1")),
			("служебная сеть IPv4", ("192.0.0.9",)),
			("NAT64", ("64:ff9b::a00:1",)),
			("6to4", ("2002:a00:1::1",)),
		):
			with self.subTest(что), mock.patch("socket.getaddrinfo", return_value=_адреса(*адреса)):
				with self.assertRaises(cimd.ДокументНеПринят):
					cimd.публичный_ip("agent.example")
		# Имя, которое не кодируется в IDNA, — отказ, а не ошибка сервера.
		for хост in ("a" * 64 + ".example", "a..example"):
			with self.subTest(хост), self.assertRaises(cimd.ДокументНеПринят):
				cimd.публичный_ip(хост)
		with mock.patch("socket.getaddrinfo", return_value=_адреса("2606:4700::1", "93.184.216.34")):
			self.assertEqual(cimd.публичный_ip("agent.example"), "93.184.216.34")

	def test_загрузка_документа(self):
		def ответ(status=200, тело=b"{}", заголовки=None):
			о = mock.Mock(status=status, headers=заголовки or {})
			о.read.side_effect = lambda n: тело[:n]
			return о

		def загрузить(о):
			пул = mock.Mock()
			пул.request.return_value = о
			with (
				mock.patch.object(cimd, "публичный_ip", return_value="93.184.216.34"),
				mock.patch.object(cimd, "_Пул", return_value=пул) as создать,
			):
				результат = cimd.загрузить(АДРЕС)
			return результат, создать, пул

		with self.subTest("ошибка сервера — документ недоступен, а не негоден"):
			with self.assertRaises(cimd.ДокументНеДоступен):
				загрузить(ответ(status=503))
		(документ, срок), создать, пул = загрузить(
			ответ(тело=b'{"a": 1}', заголовки={"Cache-Control": "max-age=300"})
		)
		self.assertEqual((документ, срок), ({"a": 1}, 300))
		# Соединение — на проверенный IP, сертификат — по имени хоста.
		адрес, порт = создать.call_args.args
		self.assertEqual((адрес, порт), ("93.184.216.34", 443))
		self.assertEqual(создать.call_args.kwargs["assert_hostname"], "claude.ai")
		self.assertEqual(пул.request.call_args.kwargs["headers"]["Host"], "claude.ai")
		self.assertIs(пул.request.call_args.kwargs["redirect"], False)

		for что, о in (
			("переадресация", ответ(status=302)),
			("не JSON", ответ(тело=b"<html>")),
			("ошибка сервера — пройдёт", ответ(status=503)),
			("больше предела", ответ(тело=b" " * (cimd.ПРЕДЕЛ_ДОКУМЕНТА + 1))),
			("вложенность в тысячи уровней", ответ(тело=b"[" * 60000)),
		):
			with self.subTest(что), self.assertRaises(cimd.ДокументНеПринят):
				загрузить(о)

	def test_сторож_обрывает_соединения_пула(self):
		"""Таймаут чтения — на каждое чтение: сокет зависшей загрузки по сроку
		закрывает сторож, и поток пула освобождается."""
		пул = cimd._Пул("93.184.216.34", 443)
		сокет = mock.Mock()
		пул.соединения.extend([mock.Mock(sock=сокет), mock.Mock(sock=None)])
		cimd.оборвать(пул)
		сокет.shutdown.assert_called_once_with(socket.SHUT_RDWR)

	def test_адрес_возврата(self):
		"""Локальный адрес — с любым портом (RFC 8252 §7.3), остальное — строка в строку."""
		свои = [
			"http://localhost/callback",
			"http://127.0.0.1/callback",
			"https://claude.ai/api/mcp/auth_callback",
		]
		for адрес, ожидание in (
			("https://claude.ai/api/mcp/auth_callback", True),
			("http://localhost:54321/callback", True),
			("http://127.0.0.1:8080/callback", True),
			("http://localhost/callback", True),
			("http://localhost:54321/other", False),
			("http://localhost:54321/callback?x=1", False),
			("http://127.0.0.1:54321/callback#x", False),
			("https://localhost:54321/callback", False),
			("https://claude.ai:444/api/mcp/auth_callback", False),
			("http://evil.example:54321/callback", False),
			("http://localhost:99999/callback", False),
			(None, False),
		):
			with self.subTest(адрес):
				self.assertIs(адрес_возврата_разрешён(адрес, свои), ожидание)
