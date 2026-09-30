"""Servidor HTTP: páginas estáticas e despacho da API pela tabela de rotas (routes.py)."""
import hmac
import html
import ipaddress
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from .. import settings
from ..auth import csrf_token, unsign
from ..db import connect
from ..event_theme import event_config, theme_css
from . import admin, public, staff  # noqa: F401  (importar registra as rotas)
from .routes import STAFF, find


class App(BaseHTTPRequestHandler):
    server_version = "CheckIn/1"

    def log_message(self, fmt, *args):
        # Avoid logging lookup data or query strings.
        print(f"{self.address_string()} - {fmt % args}")

    def client_ip(self):
        peer = self.client_address[0]
        if settings.TRUST_PROXY and peer in ("127.0.0.1", "::1"):
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        return peer

    def respond(self, status, data, headers=None):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_csv(self, filename, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self, max_bytes=16384):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise ValueError("Envie dados em JSON.")
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > max_bytes:
            raise ValueError("Dados inválidos ou muito grandes.")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError("Dados inválidos.")
        return data

    def session(self):
        cookies = self.headers.get("Cookie", "").split(";")
        token = next((x.strip()[8:] for x in cookies if x.strip().startswith("session=")), "")
        data = unsign(token)
        if not data or data.get("kind") != "session":
            return None
        with connect() as db:
            user = db.execute("SELECT username,role,guiche FROM users WHERE username=?", (data.get("user"),)).fetchone()
        return dict(user) if user else None

    def check_csrf(self, user):
        return hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), csrf_token(user["username"]))

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self.dispatch("GET", path)
        if path == "/theme.css":
            body = theme_css(event_config()).encode("utf-8")
            mime = "text/css"
        elif path.startswith("/assets/"):
            asset = path.removeprefix("/assets/")
            config = event_config()
            allowed = {config["logo"], *config["fonts"].values()}
            if config.get("decoration"):
                allowed.add(config["decoration"])
            if asset not in allowed:
                return self.send_error(404)
            body = (settings.STATIC / "assets" / asset).read_bytes()
            mime = ("image/png" if asset.endswith(".png") else
                    "image/jpeg" if asset.endswith((".jpg", ".jpeg")) else
                    "image/webp" if asset.endswith(".webp") else
                    "image/svg+xml" if asset.endswith(".svg") else "font/woff2")
        else:
            routes = {"/": "index.html", "/busca": "busca.html", "/fila": "fila.html",
                      "/painel": "painel.html",
                      "/painel/resumo": "resumo.html", "/login": "login.html",
                      "/app.css": "app.css"}
            # Módulos JavaScript: só arquivos .js que existem dentro de static/js.
            scripts = settings.STATIC / "js"
            if path.startswith("/js/") and path.endswith(".js"):
                candidate = (settings.STATIC / path.lstrip("/")).resolve()
                if candidate.is_file() and candidate.is_relative_to(scripts.resolve()):
                    routes[path] = candidate.relative_to(settings.STATIC.resolve()).as_posix()
            filename = routes.get(path)
            if not filename:
                return self.send_error(404)
            mime = ("text/css" if filename.endswith(".css") else
                    "text/javascript" if filename.endswith(".js") else "text/html")
            body = (settings.STATIC / filename).read_bytes()
            if mime == "text/html":
                config = event_config()
                body = (body.decode("utf-8")
                        .replace("{{EVENT_NAME}}", html.escape(config["name"]))
                        .replace("{{EVENT_SHORT_NAME}}", html.escape(config["short_name"]))
                        .replace("{{EVENT_LOGO}}", "/assets/" + config["logo"])).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        return self.dispatch("POST", urlparse(self.path).path)

    def dispatch(self, method, path):
        """Encontra a rota na tabela e aplica, num lugar só, corpo JSON, login, CSRF e papel."""
        route = find(method, path)
        if not route:
            return self.respond(404, {"error": "Rota não encontrada."})
        data = {}
        if method == "POST":
            try:
                data = self.body(route.max_body)
            except (ValueError, json.JSONDecodeError) as exc:
                return self.respond(400, {"error": str(exc)})
        user = None
        if route.roles:
            user = self.session()
            allowed_roles = route.roles if method == "GET" else STAFF
            if not user or user["role"] not in allowed_roles:
                return self.respond(HTTPStatus.UNAUTHORIZED, {"error": "Entre com uma conta autorizada."})
            if method == "POST":
                if not self.check_csrf(user):
                    return self.respond(403, {"error": "Sessão inválida. Recarregue a página."})
                if user["role"] not in route.roles:
                    return self.respond(403, {"error": route.forbidden})
        self.api_path = path
        return route.handler(self, user, data)
