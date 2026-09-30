"""Identidade visual, páginas, módulos JavaScript e proteção das rotas."""
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from credenciamento import event_theme, settings
from credenciamento.web.routes import ROUTES
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
            for route in ("/busca", "/fila", "/painel"):
                code, team_page, _ = self.request(route, json_response=False)
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

    def test_pages_load_existing_javascript_modules_only_from_static_js(self):
        for page in ("/", "/login", "/busca", "/fila", "/painel", "/painel/resumo"):
            code, html, _ = self.request(page, json_response=False)
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


if __name__ == "__main__":
    unittest.main()
