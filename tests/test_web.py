"""Identidade visual, páginas, módulos JavaScript e proteção das rotas."""
import base64
import re
import unittest
from pathlib import Path
from email.message import Message
from unittest.mock import patch

from credenciamento import auth, event_theme, settings
from credenciamento import db as database
from credenciamento.web.routes import ROUTES
from credenciamento.web.server import App
from support import CredenciamentoTestCase


class WebTest(CredenciamentoTestCase):
    def test_brand_assets_and_pages_are_served_locally(self):
        event = event_theme.event_config()
        logo_type = "image/svg+xml" if event["logo"].endswith(".svg") else "image/png"
        assets = [(f"/assets/{event['logo']}", logo_type),
                  *((f"/assets/{font}", "font/woff2") for font in event["fonts"].values())]
        if event.get("decoration"):
            assets.append((f"/assets/{event['decoration']}", "image/svg+xml"))
        for path, expected_type in (("/", "text/html"), ("/painel/resumo", "text/html"),
                                    ("/app.css", "text/css"),
                                    *assets):
            code, body, headers = self.request(path, json_response=False)
            self.assertEqual(code, 200)
            self.assertTrue(headers["Content-Type"].startswith(expected_type))
            self.assertTrue(body)
        self.assertIn(event["name"].encode(), self.request("/", json_response=False)[1])

    def test_event_yaml_changes_theme_and_public_brand(self):
        config_file = Path(self.temp.name) / "evento.yaml"
        current = "\n".join(line for line in settings.EVENT_CONFIG.read_text(encoding="utf-8").splitlines()
                            if not line.startswith("registration_hint_")) + "\n"
        event = event_theme.event_config()
        config_file.write_text(current.replace(event["name"], "Encontro Exemplo")
                          .replace(f"short_name: {event['short_name']}", "short_name: Encontro")
                          .replace(f'"{event["colors"]["primary"]}"', '"#123456"'), encoding="utf-8")
        with patch.object(settings, "EVENT_CONFIG", config_file):
            code, event, _ = self.request("/api/event")
            self.assertEqual((code, event["name"], event["short_name"]),
                             (200, "Encontro Exemplo", "Encontro"))
            self.assertEqual(event["registration_hints"], {"en": None, "pt-BR": None})
            code, css, headers = self.request("/theme.css", json_response=False)
            self.assertEqual(code, 200)
            self.assertIn(b"--primary: #123456", css)
            self.assertTrue(headers["Content-Type"].startswith("text/css"))
            code, page, _ = self.request("/", json_response=False)
            self.assertEqual(code, 200)
            self.assertIn(b"Encontro Exemplo", page)
            self.assertIn(b"ARRIVED AT Encontro?", page)
            self.assertNotIn(b"nome completo", page)
            self.assertNotIn(b"{{EVENT_NAME}}", page)
            admin = self.login("admin")[0]
            for route in ("/busca", "/fila", "/painel"):
                code, team_page, _ = self.request(route, cookie=admin, json_response=False)
                self.assertEqual(code, 200)
                self.assertNotIn(b'class="page-context"', team_page)
            self.assertEqual(self.request("/assets/unlisted.svg", json_response=False)[0], 404)

    def test_event_yaml_allows_missing_decoration(self):
        config_file = Path(self.temp.name) / "evento.yaml"
        current = settings.EVENT_CONFIG.read_text(encoding="utf-8")
        decoration = "event-decoration.svg"
        current_with_decoration = "\n".join(
            line for line in current.splitlines() if not line.startswith("decoration:"))
        config_file.write_text(
            (current_with_decoration + f"\ndecoration: {decoration}\n").replace(
                f"decoration: {decoration}\n", ""), encoding="utf-8")
        with patch.object(settings, "EVENT_CONFIG", config_file):
            self.assertNotIn("decoration", event_theme.event_config())
            code, css, _ = self.request("/theme.css", json_response=False)
            self.assertEqual(code, 200)
            self.assertIn(b"--event-decoration: none", css)
            self.assertIn(b"--event-decoration-opacity: 0", css)
            self.assertEqual(self.request(f"/assets/{decoration}", json_response=False)[0], 404)

    def test_event_yaml_rejects_unsafe_asset_and_bad_color(self):
        config_file = Path(self.temp.name) / "evento.yaml"
        original = settings.EVENT_CONFIG.read_text(encoding="utf-8")
        event = event_theme.event_config()
        with patch.object(settings, "EVENT_CONFIG", config_file):
            config_file.write_text(original.replace(event["logo"], "../outside.png"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "logo"):
                event_theme.event_config()
            config_file.write_text(original.replace(f'"{event["colors"]["primary"]}"', '"red; background:url(evil)"'), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "color"):
                event_theme.event_config()

    def test_every_staff_route_requires_login_csrf_and_role(self):
        volunteer = self.login("vol1")
        protected = {key: route for key, route in ROUTES.items() if route.roles}
        self.assertGreater(len(protected), 10)
        for (method, path), route in protected.items():
            target = path + "claim" if route.prefix else path
            with self.subTest(method=method, path=path):
                code, result, _ = self.request(target, None if method == "GET" else {})
                self.assertEqual(code, 401)
                if method == "POST":
                    code, result, _ = self.request(target, {}, cookie=volunteer[0])
                    self.assertEqual((code, result["error"]), (403, "Sessão inválida. Recarregue a página."))
                    if "volunteer" not in route.roles:
                        code, result, _ = self.request(target, {}, *volunteer)
                        self.assertEqual((code, result["error"]), (403, route.forbidden))
                elif "volunteer" not in route.roles:
                    self.assertEqual(self.request(target, cookie=volunteer[0])[0], 401)

    def test_staff_pages_are_not_sent_without_session_or_role(self):
        for page in ("/painel", "/busca", "/fila"):
            code, body, headers = self.request(page, json_response=False)
            self.assertEqual((code, headers["Location"], body), (302, "/login", b""), page)
        volunteer, attendant = self.login("vol1")[0], self.login("att1")[0]
        self.assertEqual(self.request("/painel", cookie=volunteer, json_response=False)[2]["Location"], "/busca")
        self.assertEqual(self.request("/painel", cookie=attendant, json_response=False)[2]["Location"], "/fila")
        for page in ("/busca", "/fila"):
            self.assertEqual(self.request(page, cookie=volunteer, json_response=False)[0], 200)
        with database.connect() as db:
            auth.end_user_sessions(db, "vol1")
        self.assertEqual(self.request("/busca", cookie=volunteer, json_response=False)[2]["Location"], "/login")
        for page in ("/", "/login", "/painel/resumo"):
            self.assertEqual(self.request(page, json_response=False)[0], 200)

    def test_pages_load_existing_javascript_modules_only_from_static_js(self):
        admin = self.login("admin")[0]
        for page in ("/", "/login", "/busca", "/fila", "/painel", "/painel/resumo"):
            code, html, _ = self.request(page, cookie=admin, json_response=False)
            scripts = re.findall(rb'<script type="module" src="([^"]+)"', html)
            self.assertEqual((code, len(scripts)), (200, 1), page)
            code, body, headers = self.request(scripts[0].decode(), json_response=False)
            self.assertEqual(code, 200)
            self.assertTrue(headers["Content-Type"].startswith("text/javascript"))
        code, body, _ = self.request("/js/dashboard/table.js", json_response=False)
        self.assertEqual(code, 200)
        for path in ("/js/../../app.py", "/js/%2e%2e/app.css", "/js/missing.js", "/app.js"):
            handler_code = self.request(path, json_response=False)[0]
            self.assertEqual(handler_code, 404, path)

    def test_forwarded_ip_is_used_only_from_trusted_proxies(self):
        def client_ip(peer, forwarded="203.0.113.7"):
            handler = App.__new__(App)
            handler.client_address = (peer, 0)
            handler.headers = Message()
            handler.headers["X-Forwarded-For"] = forwarded
            return handler.client_ip()

        for value, peer, expected in (("", "127.0.0.1", "127.0.0.1"),
                                      ("1", "127.0.0.1", "203.0.113.7"),
                                      ("1", "172.18.0.1", "172.18.0.1"),
                                      ("172.16.0.0/12", "172.18.0.1", "203.0.113.7"),
                                      ("10.0.0.5, 172.16.0.0/12", "10.0.0.5", "203.0.113.7"),
                                      ("172.16.0.0/12", "198.51.100.9", "198.51.100.9")):
            with self.subTest(value=value, peer=peer), patch.object(settings, "TRUSTED_PROXY", value):
                self.assertEqual(client_ip(peer), expected)
        with patch.object(settings, "TRUSTED_PROXY", "1"):
            self.assertEqual(client_ip("127.0.0.1", "not-an-ip"), "127.0.0.1")
            self.assertEqual(client_ip("127.0.0.1", ""), "127.0.0.1")
        # Proxy que acrescenta ao cabeçalho: o valor inventado pelo cliente fica à esquerda.
        with patch.object(settings, "TRUSTED_PROXY", "10.0.0.5"):
            self.assertEqual(client_ip("10.0.0.5", "198.51.100.1, 203.0.113.7"), "203.0.113.7")
        # Dois proxies confiáveis em sequência (proxy da instituição e rede do Docker).
        with patch.object(settings, "TRUSTED_PROXY", "172.16.0.0/12,10.0.0.5,"):
            self.assertEqual(client_ip("172.18.0.1", "198.51.100.1, 203.0.113.7, 10.0.0.5"), "203.0.113.7")
            self.assertEqual(client_ip("172.18.0.1", "10.0.0.5"), "10.0.0.5")
        with self.assertRaises(ValueError):
            settings.trusted_proxy_networks("rede-docker")

    def test_logout_and_new_password_end_sessions(self):
        cookie, csrf = self.login("vol1")
        other_cookie, other_csrf = self.login("vol1")
        self.assertNotEqual(csrf, other_csrf)
        # O token CSRF de uma sessão não vale em outra.
        self.assertEqual(self.request("/api/logout", {}, cookie=cookie, csrf=other_csrf)[0], 403)
        self.assertEqual(self.request("/api/logout", {}, cookie=cookie, csrf=csrf)[0], 200)
        self.assertIsNone(self.request("/api/me", cookie=cookie)[1]["user"])
        self.assertEqual(self.request("/api/me", cookie=other_cookie)[1]["user"]["username"], "vol1")
        with database.connect() as db:
            self.assertEqual(auth.end_user_sessions(db, "vol1"), 1)
        self.assertIsNone(self.request("/api/me", cookie=other_cookie)[1]["user"])
        for forged in ("session=", "session=abc", "session=" + "x" * 43):
            self.assertIsNone(self.request("/api/me", cookie=forged)[1]["user"])

    def test_login_limits_count_only_wrong_passwords_per_ip_and_account(self):
        settings.LIMITS.update(login_ip="4/5", login_user="2/15")
        for _ in range(6):
            self.login("vol1")
        wrong = {"username": "vol2", "password": "wrong-password"}
        self.assertEqual([self.request("/api/login", wrong)[0] for _ in range(3)], [401, 401, 429])
        # A conta fica bloqueada mesmo com a senha certa; as outras contas seguem entrando.
        self.assertEqual(self.request("/api/login", {"username": "vol2", "password": "strong-password"})[0], 429)
        self.login("att1")
        # Usuários inexistentes contam do mesmo jeito, sem revelar que não existem.
        missing = {"username": "nobody", "password": "wrong-password"}
        self.assertEqual([self.request("/api/login", missing)[0] for _ in range(3)], [401, 401, 429])
        # Quatro senhas erradas vindas do mesmo IP bloqueiam o IP para qualquer conta.
        self.assertEqual(self.request("/api/login", {"username": "admin", "password": "strong-password"})[0], 429)

    def test_strict_transport_security_only_with_https_public_url(self):
        self.assertNotIn("Strict-Transport-Security", self.request("/api/event")[2])
        with patch.object(settings, "PUBLIC_URL", "https://checkin.example.org"):
            for path in ("/api/event", "/"):
                headers = self.request(path, json_response=path != "/")[2]
                self.assertEqual(headers["Strict-Transport-Security"], "max-age=31536000", path)

    def test_invalid_limits_are_rejected(self):
        for value in ("", "10", "0/5", "5/0", "abc/5"):
            with self.subTest(value=value), patch.dict(settings.LIMITS, search=value):
                with self.assertRaises(ValueError):
                    settings.rate_limit("search")
        self.assertEqual(settings.rate_limit("search_miss"), (20, 600))

    def test_admin_customizes_names_colors_and_hints_from_the_dashboard(self):
        admin = self.login("admin")
        self.assertEqual(self.request("/api/theme", cookie=self.login("vol1")[0])[0], 401)
        current = self.request("/api/theme", cookie=admin[0])[1]
        self.assertFalse(current["customized"])
        self.assertEqual(current["current"], current["default"])
        values = dict(current["current"], name="BRACIS 2026", short_name="BRACIS",
                      registration_hint_en="Use your CPF.", registration_hint_pt="Use seu CPF.")
        values["colors"] = dict(values["colors"], primary="#AA0011")
        self.assertEqual(self.request("/api/theme", values, *self.login("vol1"))[0], 403)
        code, saved, _ = self.request("/api/theme", values, *admin)
        self.assertEqual((code, saved["customized"], saved["current"]["colors"]["primary"]), (200, True, "#aa0011"))
        self.assertIn("--primary: #aa0011;", self.request("/theme.css", json_response=False)[1].decode())
        self.assertIn(b"BRACIS 2026", self.request("/", json_response=False)[1])
        event = self.request("/api/event")[1]
        self.assertEqual((event["name"], event["registration_hints"]["pt-BR"]), ("BRACIS 2026", "Use seu CPF."))
        for invalid in (dict(values, name=""), dict(values, name="x" * 101),
                        dict(values, colors=dict(values["colors"], text="red")),
                        dict(values, colors={"primary": "#000000"}),
                        dict(values, registration_hint_en="")):
            code, error, _ = self.request("/api/theme", invalid, *admin)
            self.assertEqual(code, 400, invalid)
            self.assertTrue(error["error"].startswith(("Verifique", "Preencha")))
        # Sem instrução nos dois idiomas, a página pública volta ao texto genérico.
        self.request("/api/theme", dict(values, registration_hint_en="", registration_hint_pt=""), *admin)
        self.assertIsNone(self.request("/api/event")[1]["registration_hints"]["pt-BR"])
        code, reset, _ = self.request("/api/theme/reset", {}, *admin)
        self.assertEqual((code, reset["customized"], reset["current"]), (200, False, reset["default"]))

    def test_admin_uploads_logo_that_is_served_without_running_code(self):
        admin = self.login("admin")
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        upload = lambda data: self.request("/api/theme/logo", {"data": base64.b64encode(data).decode()}, *admin)
        code, saved, _ = upload(png)
        logo = saved["current"]["logo"]
        self.assertEqual((code, saved["custom_logo"]), (200, True))
        self.assertRegex(logo, r"^/assets/upload-logo-[0-9a-f]{16}\.png$")
        self.assertEqual(self.request("/api/event")[1]["logo"], logo)
        self.assertIn(logo.encode(), self.request("/painel/resumo", json_response=False)[1])
        code, body, headers = self.request(logo, json_response=False)
        self.assertEqual((code, body, headers["Content-Type"]), (200, png, "image/png"))
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        # Só uma logo enviada fica guardada; nomes inventados não são servidos.
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'
        new_logo = upload(svg)[1]["current"]["logo"]
        self.assertTrue(new_logo.endswith(".svg"))
        self.assertEqual(self.request(logo, json_response=False)[0], 404)
        self.assertEqual(self.request("/assets/upload-logo-0000000000000000.png", json_response=False)[0], 404)
        for bad in (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                    b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)">x</a></svg>',
                    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
                    b"GIF89a....", b"just text", b"", png + b"\x00" * 1_000_000):
            code, error, _ = upload(bad)
            self.assertEqual(code, 400, bad[:40])
        self.assertEqual(self.request("/api/theme/logo", {"data": "not base64!"}, *admin)[0], 400)
        self.assertEqual(self.request("/api/theme/logo", {"data": ""}, *self.login("vol1"))[0], 403)
        # Trocar as cores mantém a logo; voltar à logo padrão mantém as cores.
        current = self.request("/api/theme", cookie=admin[0])[1]["current"]
        current["colors"]["action"] = "#123456"
        self.assertEqual(self.request("/api/theme", current, *admin)[1]["current"]["logo"], new_logo)
        code, restored, _ = self.request("/api/theme/logo/remove", {}, *admin)
        self.assertEqual((code, restored["custom_logo"], restored["current"]["colors"]["action"]), (200, False, "#123456"))
        self.assertEqual(restored["current"]["logo"], restored["default"]["logo"])
        self.assertEqual(self.request(new_logo, json_response=False)[0], 404)


if __name__ == "__main__":
    unittest.main()
