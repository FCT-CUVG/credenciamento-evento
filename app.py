#!/usr/bin/env python3
"""Servidor de credenciamento sem dependências, com SQLite."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import html
import io
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("CHECKIN_DATA_DIR", ROOT / "data"))
DB = DATA / "credenciamento.sqlite3"
CSV = DATA / "participantes.csv"
STATIC = ROOT / "static"
GOOGLE_SHEETS_SCRIPT = ROOT / "google-sheets" / "Code.gs"
RANGES = Path(os.environ.get("CHECKIN_RANGES_FILE", ROOT / "config" / "guiches.json"))
EVENT_CONFIG = Path(os.environ.get("CHECKIN_EVENT_CONFIG", ROOT / "config" / "evento.yaml"))
SECRET = os.environ.get("CHECKIN_SESSION_SECRET", "").encode()
SHEET_URL = os.environ.get("CHECKIN_SHEETS_URL", "")
SHEET_SECRET = os.environ.get("CHECKIN_SHEETS_SECRET", "")
PUBLIC_URL = os.environ.get("CHECKIN_PUBLIC_URL", "")
TRUST_PROXY = os.environ.get("CHECKIN_TRUSTED_PROXY", "") == "1"
RATE = defaultdict(deque)
RATE_LOCK = threading.Lock()
CSV_LOCK = threading.Lock()
RANGES_LOCK = threading.Lock()

COLOR_NAMES = ("primary", "navigation", "text", "text_muted",
               "action", "action_hover", "focus", "page_background", "surface")
COLOR_ALIASES = {
    "navy": "primary",
    "navy_header": "navigation",
    "ink": "text",
    "slate": "text_muted",
    "leaf": "action",
    "leaf_dark": "action_hover",
    "gold": "focus",
    "fog": "page_background",
    "white": "surface",
}


def read_event_yaml(path):
    """Parse the small mapping-only YAML format used by the event theme."""
    result = {}
    section = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"(  )?([A-Za-z_]+):(?: (.*))?", raw)
        if not match:
            raise ValueError(f"linha {number}: use chaves e recuo de dois espaços")
        nested, key, value = match.groups()
        if nested:
            if section is None:
                raise ValueError(f"linha {number}: seção ausente")
            target = result[section]
        else:
            target = result
        if key in target:
            raise ValueError(f"linha {number}: chave duplicada {key}")
        if value is None:
            if nested or key not in ("fonts", "colors"):
                raise ValueError(f"linha {number}: seção inválida")
            target[key] = {}
            section = key
        else:
            if value.startswith('"'):
                quoted = re.fullmatch(r'("(?:\\.|[^"\\])*")(?:\s+#.*)?', value)
                if not quoted:
                    raise ValueError(f"linha {number}: texto entre aspas inválido")
                value = json.loads(quoted.group(1))
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1].replace("''", "'")
            elif " #" in value:
                value = value.split(" #", 1)[0]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"linha {number}: valor vazio")
            target[key] = value
            if not nested:
                section = None
    return result


def event_config():
    """Read and validate public event appearance without exposing server settings."""
    try:
        config = read_event_yaml(EVENT_CONFIG)
        for key in ("name", "short_name"):
            name = config[key]
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise ValueError(key)
        hints = ("registration_hint_en", "registration_hint_pt")
        if any(key in config for key in hints) and not all(key in config for key in hints):
            raise ValueError("registration_hints")
        for key in hints:
            if key in config and (not isinstance(config[key], str) or not config[key].strip() or len(config[key]) > 300):
                raise ValueError(key)
        for key, suffixes in (("logo", (".png", ".jpg", ".jpeg", ".webp", ".svg")),
                              ("body", (".woff2",)), ("display", (".woff2",))):
            value = config["fonts"][key] if key in ("body", "display") else config[key]
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or not value.lower().endswith(suffixes):
                raise ValueError(key)
            if not (STATIC / "assets" / value).is_file():
                raise ValueError(f"asset ausente: {value}")
        if "decoration" in config:
            value = config["decoration"]
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or not value.lower().endswith(".svg"):
                raise ValueError("decoration")
            if not (STATIC / "assets" / value).is_file():
                raise ValueError(f"asset ausente: {value}")
        if set(config["colors"]) != set(COLOR_NAMES):
            raise ValueError("colors")
        for value in config["colors"].values():
            if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                raise ValueError("color")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Configuração visual inválida: {EVENT_CONFIG}: {exc}") from exc
    return config


def theme_css(config):
    colors = "\n".join(f"  --{key.replace('_', '-')}: {config['colors'][key]};" for key in COLOR_NAMES)
    aliases = "\n".join(f"  --{alias.replace('_', '-')}: var(--{name.replace('_', '-')});"
                        for alias, name in COLOR_ALIASES.items())
    body = config["fonts"]["body"]
    display = config["fonts"]["display"]
    decoration = config.get("decoration")
    decoration_css = f'url("/assets/{decoration}")' if decoration else "none"
    decoration_opacity = ".13" if decoration else "0"
    return (f'@font-face {{ font-family: "Event Body"; src: url("/assets/{body}") format("woff2"); font-style: normal; font-weight: 100 900; font-display: swap; }}\n'
            f'@font-face {{ font-family: "Event Display"; src: url("/assets/{display}") format("woff2"); font-style: normal; font-weight: 400; font-display: swap; }}\n'
            f':root {{\n{colors}\n{aliases}\n  --event-decoration: {decoration_css};\n  --event-decoration-opacity: {decoration_opacity};\n}}\n')


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value or "").strip())
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).casefold().split())


def guiche_for(name):
    initial = normalize(name)[:1].upper()
    try:
        config = json.loads(RANGES.read_text(encoding="utf-8"))
        ranges = config["ranges"]
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError(f"Configuração de guichês inválida: {RANGES}") from exc
    matches = [str(r["guiche"]).strip() for r in ranges
               if str(r["from"]).upper() <= initial <= str(r["to"]).upper()]
    if len(matches) != 1 or not matches[0]:
        raise ValueError(f"Inicial {initial or '?'} de {name!r} sem faixa única de guichê.")
    return matches[0]


def configured_ranges():
    try:
        ranges = json.loads(RANGES.read_text(encoding="utf-8"))["ranges"]
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError("Não foi possível ler a configuração dos guichês.") from exc
    if not isinstance(ranges, list):
        raise ValueError("Configuração de guichês inválida.")
    return ranges


def validate_ranges(ranges):
    if not isinstance(ranges, list) or not ranges or len(ranges) > 26:
        raise ValueError("Informe entre 1 e 26 faixas de letras.")
    normalized = []
    coverage = set()
    for index, item in enumerate(ranges, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Faixa {index}: dados inválidos.")
        start = str(item.get("from", "")).strip().upper()
        end = str(item.get("to", "")).strip().upper()
        desk = str(item.get("guiche", "")).strip()
        if not re.fullmatch(r"[A-Z]", start) or not re.fullmatch(r"[A-Z]", end) or start > end:
            raise ValueError(f"Faixa {index}: informe letras de A a Z em ordem.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", desk):
            raise ValueError(f"Faixa {index}: identificador de guichê inválido.")
        letters = set(chr(code) for code in range(ord(start), ord(end) + 1))
        if coverage & letters:
            raise ValueError(f"Faixa {index}: há letras atribuídas a mais de um guichê.")
        coverage.update(letters)
        normalized.append({"from": start, "to": end, "guiche": desk})
    missing = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ") - coverage
    if missing:
        raise ValueError("Todas as letras de A a Z precisam ter um guichê. Faltam: " + ", ".join(sorted(missing)))
    return normalized


def save_ranges(ranges):
    normalized = validate_ranges(ranges)
    with RANGES_LOCK:
        temporary = RANGES.with_suffix(RANGES.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps({"ranges": normalized}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, RANGES)
        finally:
            temporary.unlink(missing_ok=True)
    return normalized


def available_guiches(db):
    """List configured desks, including desks assigned explicitly to participants or staff."""
    try:
        ranges = json.loads(RANGES.read_text(encoding="utf-8"))["ranges"]
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError(f"Configuração de guichês inválida: {RANGES}") from exc
    desks = {}
    for item in ranges:
        desk = str(item["guiche"]).strip()
        if desk:
            desks.setdefault(desk, []).append(f'{str(item["from"]).upper()}–{str(item["to"]).upper()}')
    for row in db.execute("SELECT guiche FROM participants UNION SELECT guiche FROM users"):
        desk = row["guiche"].strip()
        if desk:
            desks.setdefault(desk, [])
    return [{"id": desk, "ranges": labels} for desk, labels in sorted(
        desks.items(), key=lambda entry: (0, int(entry[0])) if entry[0].isdigit() else (1, entry[0].casefold()))]


def connect():
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(DATA, 0o700)
    db = sqlite3.connect(DB, timeout=10, isolation_level=None)
    os.chmod(DB, 0o600)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS participants (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
                    badge_name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL, email_key TEXT NOT NULL,
                    cpf TEXT NOT NULL DEFAULT '', affiliation TEXT NOT NULL DEFAULT '',
                    paid INTEGER NOT NULL DEFAULT 0 CHECK(paid IN (0,1)),
                    priority INTEGER NOT NULL DEFAULT 0 CHECK(priority IN (0,1)), guiche TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'registered' CHECK(status IN
            ('registered','prechecked','searching','ready','completed')),
          claimed_by TEXT, prechecked_at TEXT, claimed_at TEXT, ready_at TEXT,
          completed_at TEXT, updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
          UNIQUE(name_key,email_key)
        );
        CREATE INDEX IF NOT EXISTS idx_queue ON participants(status,guiche,prechecked_at);
        CREATE TABLE IF NOT EXISTS users (
          username TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('volunteer','attendant','admin')),
          guiche TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, participant_id TEXT NOT NULL, action TEXT NOT NULL,
          actor TEXT NOT NULL, occurred_at TEXT NOT NULL,
          FOREIGN KEY(participant_id) REFERENCES participants(id)
        );
        CREATE TABLE IF NOT EXISTS sheet_outbox (
          event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, delivered_at TEXT,
          attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
          FOREIGN KEY(event_id) REFERENCES events(id)
        );
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(participants)")}
        for name, definition in (
            ("badge_name", "TEXT NOT NULL DEFAULT ''"),
            ("paid", "INTEGER NOT NULL DEFAULT 0 CHECK(paid IN (0,1))"),
            ("priority", "INTEGER NOT NULL DEFAULT 0 CHECK(priority IN (0,1))"),
        ):
            if name not in columns:
                db.execute(f"ALTER TABLE participants ADD COLUMN {name} {definition}")
        db.execute("UPDATE participants SET badge_name=name WHERE badge_name='' ")


def participant_dict(row, private=False):
    d = {k: row[k] for k in ("id", "name", "affiliation", "guiche", "status",
                                  "claimed_by", "prechecked_at", "claimed_at", "ready_at",
                                  "completed_at", "updated_at", "revision")}
    if private:
        d.update(email=row["email"], cpf=row["cpf"], badge_name=row["badge_name"],
                 paid=bool(row["paid"]), priority=bool(row["priority"]))
    return d


def record_event(db, row, action, actor):
    event = {"id": str(uuid.uuid4()), "participant_id": row["id"], "action": action,
             "actor": actor, "occurred_at": now()}
    db.execute("INSERT INTO events VALUES (:id,:participant_id,:action,:actor,:occurred_at)", event)
    payload = {"event": event, "participant": participant_dict(row, private=True)}
    db.execute("INSERT INTO sheet_outbox(event_id,payload) VALUES (?,?)",
               (event["id"], json.dumps(payload, ensure_ascii=False)))


def export_csv():
    """The local CSV is a restart-rebuilt mirror; SQLite remains the source of truth."""
    with CSV_LOCK, connect() as db:
        rows = db.execute("SELECT * FROM participants ORDER BY name_key").fetchall()
        fields = ["id", "name", "badge_name", "email", "cpf", "affiliation", "paid", "priority", "guiche", "status",
                  "claimed_by", "prechecked_at", "claimed_at", "ready_at", "completed_at",
                  "updated_at", "revision"]
        temp = CSV.with_suffix(".csv.tmp")
        with temp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                values = {key: row[key] for key in fields}
                for key in ("name", "badge_name", "email", "cpf", "affiliation", "guiche", "claimed_by"):
                    value = values[key]
                    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                        values[key] = "'" + value
                writer.writerow(values)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, CSV)


PARTICIPANT_COLUMNS = ("id", "nome", "nome_cracha", "afiliacao", "email", "cpf", "pago", "prioridade", "guiche")
PARTICIPANT_EXPORT_COLUMNS = PARTICIPANT_COLUMNS + ("situacao", "responsavel", "pre_checkin_em",
                                                      "busca_iniciada_em", "pronto_em", "retirado_em",
                                                      "atualizado_em", "revisao")
PARTICIPANT_TEMPLATE_COLUMNS = ("nome", "nome_cracha", "afiliacao", "email", "cpf", "pago", "prioridade")
EVENT_LOG_COLUMNS = ("evento_id", "participante_id", "nome", "nome_cracha", "email", "cpf", "guiche",
                     "acao", "responsavel", "data_hora")


def participants_download():
    """Export participant data, including workflow fields accepted on reimport."""
    with connect() as db:
        rows = db.execute("SELECT * FROM participants ORDER BY name_key").fetchall()
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=PARTICIPANT_EXPORT_COLUMNS)
    writer.writeheader()
    for row in rows:
        values = {"id": row["id"], "nome": row["name"], "nome_cracha": row["badge_name"],
                  "afiliacao": row["affiliation"], "email": row["email"], "cpf": row["cpf"],
                  "pago": row["paid"], "prioridade": row["priority"], "guiche": row["guiche"],
                  "situacao": row["status"], "responsavel": row["claimed_by"],
                  "pre_checkin_em": row["prechecked_at"], "busca_iniciada_em": row["claimed_at"],
                  "pronto_em": row["ready_at"], "retirado_em": row["completed_at"],
                  "atualizado_em": row["updated_at"], "revisao": row["revision"]}
        for key, value in values.items():
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                values[key] = "'" + value
        writer.writerow(values)
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def participants_template_download():
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=PARTICIPANT_TEMPLATE_COLUMNS)
    writer.writeheader()
    writer.writerow({"nome": "Maria da Silva", "nome_cracha": "Maria", "afiliacao": "Universidade Exemplo",
                     "email": "maria@example.org", "cpf": "12345678901", "pago": "1", "prioridade": "0"})
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def event_logs_download():
    with connect() as db:
        rows = db.execute("""
            SELECT e.id AS evento_id, e.participant_id AS participante_id, p.name AS nome,
                   p.badge_name AS nome_cracha, p.email, p.cpf, p.guiche,
                   e.action AS acao, e.actor AS responsavel, e.occurred_at AS data_hora
            FROM events e
            LEFT JOIN participants p ON p.id=e.participant_id
            ORDER BY e.occurred_at DESC, e.id DESC
        """).fetchall()
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=EVENT_LOG_COLUMNS)
    writer.writeheader()
    for row in rows:
        values = {key: row[key] or "" for key in EVENT_LOG_COLUMNS}
        for key, value in values.items():
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                values[key] = "'" + value
        writer.writerow(values)
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()


def sign(data):
    raw = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=")
    sig = hmac.new(SECRET, raw, hashlib.sha256).hexdigest()
    return raw.decode() + "." + sig


def unsign(token):
    try:
        raw, sig = token.rsplit(".", 1)
        expected = hmac.new(SECRET, raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return data if data.get("exp", 0) >= time.time() else None
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def allowed(ip, limit=12, window=60):
    with RATE_LOCK:
        q = RATE[ip]
        t = time.time()
        while q and q[0] < t - window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(t)
        return True


class App(BaseHTTPRequestHandler):
    server_version = "CheckIn/1"

    def log_message(self, fmt, *args):
        # Avoid logging lookup data or query strings.
        print(f"{self.address_string()} - {fmt % args}")

    def client_ip(self):
        peer = self.client_address[0]
        if TRUST_PROXY and peer in ("127.0.0.1", "::1"):
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

    def require(self, roles):
        user = self.session()
        if not user or user["role"] not in roles:
            self.respond(HTTPStatus.UNAUTHORIZED, {"error": "Entre com uma conta autorizada."})
            return None
        return user

    def check_csrf(self, user):
        expected = hmac.new(SECRET, (user["username"] + ":csrf").encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), expected)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self.api_get(path)
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
            body = (STATIC / "assets" / asset).read_bytes()
            mime = ("image/png" if asset.endswith(".png") else
                    "image/jpeg" if asset.endswith((".jpg", ".jpeg")) else
                    "image/webp" if asset.endswith(".webp") else
                    "image/svg+xml" if asset.endswith(".svg") else "font/woff2")
        else:
            routes = {"/": "index.html", "/busca": "busca.html", "/fila": "fila.html",
                      "/painel": "painel.html", "/painel/resumo": "resumo.html", "/login": "login.html",
                      "/app.css": "app.css", "/app.js": "app.js"}
            filename = routes.get(path)
            if not filename:
                return self.send_error(404)
            mime = ("text/css" if filename.endswith(".css") else
                    "text/javascript" if filename.endswith(".js") else "text/html")
            body = (STATIC / filename).read_bytes()
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

    def api_get(self, path):
        if path == "/api/event":
            config = event_config()
            return self.respond(200, {"name": config["name"], "short_name": config["short_name"],
                                      "logo": "/assets/" + config["logo"],
                                      "registration_hints": {
                                          "en": config.get("registration_hint_en"),
                                          "pt-BR": config.get("registration_hint_pt")
                                      }})
        if path == "/api/me":
            user = self.session()
            if not user:
                return self.respond(200, {"user": None})
            csrf = hmac.new(SECRET, (user["username"] + ":csrf").encode(), hashlib.sha256).hexdigest()
            return self.respond(200, {"user": user, "csrf": csrf})
        if path == "/api/guiches":
            user = self.require(("volunteer", "attendant", "admin"))
            if not user:
                return
            with connect() as db:
                desks = available_guiches(db)
            return self.respond(200, {"guiches": desks})
        if path == "/api/queue":
            user = self.require(("volunteer", "attendant", "admin"))
            if not user:
                return
            params = parse_qs(urlparse(self.path).query)
            requested = params.get("guiche", [""])[0].strip()
            attendant_view = params.get("view", [""])[0] == "attendant"
            guiche = "" if requested == "all" else requested
            if user["role"] == "attendant" and not requested:
                guiche = user["guiche"]
            with connect() as db:
                statuses = "('ready','completed')" if attendant_view else "('prechecked','searching','ready')"
                query = f"SELECT * FROM participants WHERE status IN {statuses}"
                args = []
                if guiche:
                    query += " AND guiche=?"
                    args.append(guiche)
                if attendant_view:
                    query += (" ORDER BY CASE WHEN status='ready' THEN 0 ELSE 1 END,"
                              " CASE WHEN status='ready' THEN ready_at END ASC,"
                              " CASE WHEN status='completed' THEN completed_at END DESC, name_key ASC")
                else:
                    query += " ORDER BY prechecked_at ASC, name_key ASC"
                rows = db.execute(query, args).fetchall()
            return self.respond(200, {"items": [participant_dict(r) for r in rows], "guiche": guiche})
        if path == "/api/dashboard/summary":
            with connect() as db:
                total, arrived, completed = db.execute("""
                    SELECT COUNT(*),
                           COALESCE(SUM(status <> 'registered'), 0),
                           COALESCE(SUM(status = 'completed'), 0)
                    FROM participants
                """).fetchone()
            return self.respond(200, {"total": total, "arrived": arrived, "completed": completed})
        if path == "/api/dashboard":
            user = self.require(("admin",))
            if not user:
                return
            with connect() as db:
                rows = db.execute("SELECT * FROM participants ORDER BY name_key").fetchall()
                pending = db.execute("SELECT COUNT(*) FROM sheet_outbox WHERE delivered_at IS NULL").fetchone()[0]
            counts = {s: sum(r["status"] == s for r in rows) for s in
                      ("registered", "prechecked", "searching", "ready", "completed")}
            return self.respond(200, {"total": len(rows), "counts": counts,
                                      "sheet_pending": pending, "sheet_configured": bool(SHEET_URL and SHEET_SECRET),
                                      "items": [participant_dict(r, private=True) for r in rows]})
        if path == "/api/participants/export":
            if not self.require(("admin",)):
                return
            return self.send_csv("dados-participantes.csv", participants_download())
        if path == "/api/events/export":
            if not self.require(("admin",)):
                return
            return self.send_csv("logs-movimentacoes.csv", event_logs_download())
        if path == "/api/google-sheets/script":
            if not self.require(("admin",)):
                return
            try:
                script = GOOGLE_SHEETS_SCRIPT.read_text(encoding="utf-8")
            except OSError:
                return self.respond(500, {"error": "Não foi possível ler o Apps Script do backup."})
            return self.respond(200, {"script": script})
        if path == "/api/participants/template":
            if not self.require(("admin",)):
                return
            return self.send_csv("participantes-exemplo.csv", participants_template_download())
        if path == "/api/guiches/config":
            if not self.require(("admin",)):
                return
            try:
                return self.respond(200, {"ranges": configured_ranges()})
            except ValueError as exc:
                return self.respond(500, {"error": str(exc)})
        self.respond(404, {"error": "Rota não encontrada."})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self.body(2_000_000 if path == "/api/participants/import" else 16384)
        except (ValueError, json.JSONDecodeError) as exc:
            return self.respond(400, {"error": str(exc)})
        if path == "/api/lookup":
            if not allowed(self.client_ip()):
                return self.respond(429, {"error": "Muitas tentativas. Aguarde um minuto."})
            cpf = re.sub(r"\D", "", str(data.get("cpf", "")))
            if not re.fullmatch(r"\d{11}", cpf):
                return self.respond(400, {"error": "Informe um CPF válido com 11 dígitos."})
            with connect() as db:
                matches = db.execute("SELECT * FROM participants WHERE cpf=? LIMIT 2", (cpf,)).fetchall()
            if len(matches) > 1:
                return self.respond(409, {"error": "Há mais de uma inscrição com este CPF. Procure atendimento."})
            row = matches[0] if matches else None
            if not row:
                return self.respond(404, {"error": "Inscrição não encontrada. Confira o CPF ou procure a equipe."})
            if not row["paid"]:
                return self.respond(403, {"error": "Pagamento pendente. Procure atendimento para regularizar sua inscrição."})
            token = sign({"kind": "lookup", "id": row["id"], "exp": time.time() + 600})
            return self.respond(200, {"name": row["name"], "affiliation": row["affiliation"],
                                      "status": row["status"], "token": token})
        if path == "/api/precheck":
            token = unsign(str(data.get("token", "")))
            if not token or token.get("kind") != "lookup":
                return self.respond(400, {"error": "Consulta expirada. Tente novamente."})
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM participants WHERE id=?", (token["id"],)).fetchone()
                if not row:
                    return self.respond(404, {"error": "Inscrição não encontrada."})
                if not row["paid"]:
                    return self.respond(403, {"error": "Pagamento pendente. Procure atendimento para regularizar sua inscrição."})
                if row["status"] == "registered":
                    t = now()
                    db.execute("UPDATE participants SET status='prechecked',prechecked_at=?,updated_at=?,revision=revision+1 WHERE id=?",
                               (t, t, row["id"]))
                    row = db.execute("SELECT * FROM participants WHERE id=?", (row["id"],)).fetchone()
                    record_event(db, row, "precheck", "participant")
                    db.commit()
                    export_csv()
            return self.respond(200, {"ok": True, "name": row["name"], "status": row["status"]})
        if path == "/api/login":
            if not allowed("login:" + self.client_ip(), 8, 300):
                return self.respond(429, {"error": "Muitas tentativas. Aguarde alguns minutos."})
            username = str(data.get("username", "")).strip().casefold()
            password = str(data.get("password", ""))
            with connect() as db:
                user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not user or not hmac.compare_digest(password_hash(password, user["salt"]), user["password_hash"]):
                return self.respond(401, {"error": "Usuário ou senha incorretos."})
            token = sign({"kind": "session", "user": username, "exp": time.time() + 12*3600})
            secure = "; Secure" if PUBLIC_URL.startswith("https://") else ""
            return self.respond(200, {"role": user["role"]}, {"Set-Cookie": f"session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200{secure}"})
        user = self.require(("volunteer", "attendant", "admin"))
        if not user:
            return
        if not self.check_csrf(user):
            return self.respond(403, {"error": "Sessão inválida. Recarregue a página."})
        if path == "/api/logout":
            return self.respond(200, {"ok": True}, {"Set-Cookie": "session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
        if path == "/api/participants/import":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode importar participantes."})
            filename, content = data.get("filename"), data.get("content")
            if not isinstance(filename, str) or not isinstance(content, str) or not content.strip():
                return self.respond(400, {"error": "Selecione um arquivo CSV ou JSON válido."})
            suffix = Path(filename).suffix.lower()
            if suffix not in (".csv", ".json"):
                return self.respond(400, {"error": "Use um arquivo CSV ou JSON."})
            try:
                read, changed = import_text(content, suffix, actor=user["username"])
            except (ValueError, json.JSONDecodeError, csv.Error, sqlite3.IntegrityError) as exc:
                return self.respond(400, {"error": str(exc)})
            return self.respond(200, {"read": read, "changed": changed})
        if path == "/api/participants/payment":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode alterar o pagamento."})
            pid, paid = str(data.get("id", "")), str(data.get("paid", "")).strip()
            if paid not in ("0", "1"):
                return self.respond(400, {"error": "Pagamento inválido."})
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
                if not row:
                    return self.respond(404, {"error": "Participante não encontrado."})
                if row["paid"] != int(paid):
                    db.execute("UPDATE participants SET paid=?,updated_at=?,revision=revision+1 WHERE id=?",
                               (int(paid), now(), pid))
                    row = db.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
                    record_event(db, row, "payment_paid" if row["paid"] else "payment_unpaid", user["username"])
                    db.commit()
                    export_csv()
            return self.respond(200, {"item": participant_dict(row, private=True)})
        if path == "/api/guiches/config":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode configurar guichês."})
            try:
                ranges = save_ranges(data.get("ranges"))
            except (ValueError, OSError) as exc:
                return self.respond(400, {"error": str(exc)})
            return self.respond(200, {"ranges": ranges})
        if path == "/api/participants/status":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode alterar a situação."})
            pid, status = str(data.get("id", "")), str(data.get("status", ""))
            if status not in ("registered", "prechecked", "searching", "ready", "completed"):
                return self.respond(400, {"error": "Situação inválida."})
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
                if not row:
                    return self.respond(404, {"error": "Participante não encontrado."})
                t = now()
                if status == "registered":
                    db.execute("UPDATE participants SET status=?,claimed_by=NULL,prechecked_at=NULL,claimed_at=NULL,ready_at=NULL,completed_at=NULL,updated_at=?,revision=revision+1 WHERE id=?", (status, t, pid))
                elif status == "prechecked":
                    db.execute("UPDATE participants SET status=?,claimed_by=NULL,prechecked_at=COALESCE(prechecked_at,?),claimed_at=NULL,ready_at=NULL,completed_at=NULL,updated_at=?,revision=revision+1 WHERE id=?", (status, t, t, pid))
                elif status == "searching":
                    db.execute("UPDATE participants SET status=?,claimed_by=?,prechecked_at=COALESCE(prechecked_at,?),claimed_at=COALESCE(claimed_at,?),ready_at=NULL,completed_at=NULL,updated_at=?,revision=revision+1 WHERE id=?", (status, user["username"], t, t, t, pid))
                elif status == "ready":
                    db.execute("UPDATE participants SET status=?,claimed_by=COALESCE(claimed_by,?),prechecked_at=COALESCE(prechecked_at,?),claimed_at=COALESCE(claimed_at,?),ready_at=COALESCE(ready_at,?),completed_at=NULL,updated_at=?,revision=revision+1 WHERE id=?", (status, user["username"], t, t, t, t, pid))
                else:
                    db.execute("UPDATE participants SET status=?,claimed_by=COALESCE(claimed_by,?),prechecked_at=COALESCE(prechecked_at,?),claimed_at=COALESCE(claimed_at,?),ready_at=COALESCE(ready_at,?),completed_at=COALESCE(completed_at,?),updated_at=?,revision=revision+1 WHERE id=?", (status, user["username"], t, t, t, t, t, pid))
                row = db.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
                record_event(db, row, "status_" + status, user["username"])
                db.commit()
            export_csv()
            return self.respond(200, {"item": participant_dict(row)})
        if path.startswith("/api/action/"):
            action = path.removeprefix("/api/action/")
            if action not in ("claim", "ready", "release", "complete", "undo_ready", "undo_complete"):
                return self.respond(404, {"error": "Ação desconhecida."})
            pid = str(data.get("id", ""))
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
                if not row:
                    return self.respond(404, {"error": "Participante não encontrado."})
                t = now()
                if action == "claim" and user["role"] in ("volunteer", "admin") and row["status"] == "prechecked":
                    db.execute("UPDATE participants SET status='searching',claimed_by=?,claimed_at=?,updated_at=?,revision=revision+1 WHERE id=?",
                               (user["username"], t, t, pid))
                elif action == "ready" and user["role"] in ("volunteer", "admin") and row["status"] == "searching" and (row["claimed_by"] == user["username"] or user["role"] == "admin"):
                    db.execute("UPDATE participants SET status='ready',ready_at=?,updated_at=?,revision=revision+1 WHERE id=?", (t, t, pid))
                elif action == "release" and user["role"] in ("volunteer", "admin") and row["status"] == "searching" and (row["claimed_by"] == user["username"] or user["role"] == "admin"):
                    db.execute("UPDATE participants SET status='prechecked',claimed_by=NULL,claimed_at=NULL,updated_at=?,revision=revision+1 WHERE id=?", (t, pid))
                elif action == "complete" and row["status"] == "ready" and (user["role"] in ("volunteer", "admin") or (user["role"] == "attendant" and user["guiche"] == row["guiche"])):
                    db.execute("UPDATE participants SET status='completed',completed_at=?,updated_at=?,revision=revision+1 WHERE id=?", (t, t, pid))
                elif action == "undo_ready" and row["status"] == "ready" and (user["role"] in ("volunteer", "admin") or (user["role"] == "attendant" and user["guiche"] == row["guiche"])):
                    previous = "searching" if row["claimed_by"] else "prechecked"
                    db.execute("UPDATE participants SET status=?,ready_at=NULL,updated_at=?,revision=revision+1 WHERE id=?", (previous, t, pid))
                elif action == "undo_complete" and row["status"] == "completed" and (user["role"] in ("volunteer", "admin") or (user["role"] == "attendant" and user["guiche"] == row["guiche"])):
                    db.execute("UPDATE participants SET status='ready',completed_at=NULL,updated_at=?,revision=revision+1 WHERE id=?", (t, pid))
                else:
                    return self.respond(409, {"error": "Esta ação não é permitida no estado atual. Atualize a fila."})
                row = db.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
                record_event(db, row, action, user["username"])
                db.commit()
            export_csv()
            return self.respond(200, {"item": participant_dict(row)})
        self.respond(404, {"error": "Rota não encontrada."})


def sync_sheets_once():
    if not SHEET_URL or not SHEET_SECRET:
        return 0
    with connect() as db:
        rows = db.execute("SELECT event_id,payload FROM sheet_outbox WHERE delivered_at IS NULL ORDER BY rowid LIMIT 40").fetchall()
    if not rows:
        return 0
    payload = {"secret": SHEET_SECRET, "records": [json.loads(r["payload"]) for r in rows]}
    request = urllib.request.Request(SHEET_URL, json.dumps(payload).encode(),
                                     {"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
        if result.get("ok") is not True:
            raise ValueError(result.get("error", "Google Sheets recusou a sincronização"))
        with connect() as db:
            db.executemany("UPDATE sheet_outbox SET delivered_at=?,attempts=attempts+1,last_error='' WHERE event_id=?",
                           [(now(), r["event_id"]) for r in rows])
        return len(rows)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        with connect() as db:
            db.executemany("UPDATE sheet_outbox SET attempts=attempts+1,last_error=? WHERE event_id=?",
                           [(str(exc)[:300], r["event_id"]) for r in rows])
        return 0


def sync_worker():
    while True:
        sync_sheets_once()
        time.sleep(15)


def import_file(path):
    path = Path(path)
    result = import_text(path.read_text(encoding="utf-8-sig"), path.suffix.lower())
    print(f"{result[0]} registros lidos; {result[1]} criados ou atualizados.")
    return result


def import_text(content, suffix, actor="system"):
    if suffix == ".json":
        records = json.loads(content.lstrip("\ufeff"))
        if isinstance(records, dict):
            records = records.get("participants", [])
    elif suffix == ".csv":
        records = list(csv.DictReader(io.StringIO(content.lstrip("\ufeff"), newline="")))
    else:
        raise ValueError("Use um arquivo CSV ou JSON.")
    if not isinstance(records, list) or not records:
        raise ValueError("Arquivo deve conter uma lista de participantes.")
    prepared = []
    seen, seen_ids = set(), set()
    for n, item in enumerate(records, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Linha {n}: registro inválido.")
        def value(key):
            raw = item.get(key, "")
            if raw is None:
                return ""
            raw = str(raw).strip()
            return raw[1:] if raw.startswith("'") and raw[1:].lstrip().startswith(("=", "+", "-", "@")) else raw

        pid = value("id")
        if pid:
            try:
                pid = str(uuid.UUID(pid))
            except ValueError as exc:
                raise ValueError(f"Linha {n}: id inválido.") from exc
            if pid in seen_ids:
                raise ValueError(f"Linha {n}: id duplicado.")
            seen_ids.add(pid)
        name, badge_name = value("nome"), value("nome_cracha")
        affiliation, email, cpf = value("afiliacao"), value("email"), value("cpf")
        if not all((name, badge_name, affiliation, email, cpf)):
            raise ValueError(f"Linha {n}: nome, nome_cracha, afiliacao, email e cpf são obrigatórios.")
        if not re.fullmatch(r"(?:\d{11}|\d{3}\.\d{3}\.\d{3}-\d{2})", cpf):
            raise ValueError(f"Linha {n}: cpf deve ter 11 dígitos ou usar o formato xxx.xxx.xxx-xx.")
        cpf = re.sub(r"\D", "", cpf)
        paid_raw, priority_raw = value("pago"), value("prioridade")
        if paid_raw not in ("", "0", "1"):
            raise ValueError(f"Linha {n}: pago deve ser 0 ou 1.")
        if priority_raw not in ("", "0", "1"):
            raise ValueError(f"Linha {n}: prioridade deve ser 0 ou 1.")
        paid, priority = int(paid_raw or 0), int(priority_raw or 0)
        guiche = value("guiche") or guiche_for(name)
        key = (normalize(name), email.casefold())
        if len(key[0]) < 3 or "@" not in email or not guiche:
            raise ValueError(f"Linha {n}: nome e email devem ser válidos; guiche deve ser informado ou calculável.")
        if key in seen:
            raise ValueError(f"Linha {n}: nome e e-mail duplicados.")
        seen.add(key)
        prepared.append((pid, name, badge_name, key[0], email, key[1], cpf, affiliation, paid, priority, guiche))
    count = 0
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for pid, name, badge_name, name_key, email, email_key, cpf, affiliation, paid, priority, guiche in prepared:
            old_by_id = db.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone() if pid else None
            old_by_key = db.execute("SELECT * FROM participants WHERE name_key=? AND email_key=?",
                                    (name_key, email_key)).fetchone()
            if old_by_id and old_by_key and old_by_id["id"] != old_by_key["id"]:
                raise ValueError(f"{name}: id e nome/e-mail identificam pessoas diferentes.")
            old = old_by_id or old_by_key
            if old:
                if any(old[k] != v for k, v in (("name", name), ("badge_name", badge_name), ("email", email),
                                                  ("cpf", cpf), ("affiliation", affiliation), ("paid", paid),
                                                  ("priority", priority), ("guiche", guiche))):
                    if old["guiche"] != guiche and old["status"] in ("searching", "ready", "completed"):
                        raise ValueError(f"Não é possível mudar o guichê de {name}: busca ou retirada já iniciada.")
                    db.execute("UPDATE participants SET name=?,badge_name=?,email=?,cpf=?,affiliation=?,paid=?,priority=?,guiche=?,updated_at=?,revision=revision+1 WHERE id=?",
                               (name, badge_name, email, cpf, affiliation, paid, priority, guiche, now(), old["id"]))
                    row = db.execute("SELECT * FROM participants WHERE id=?", (old["id"],)).fetchone()
                    record_event(db, row, "import_update", actor)
                    count += 1
            else:
                pid = pid or str(uuid.uuid4())
                db.execute("INSERT INTO participants(id,name,badge_name,name_key,email,email_key,cpf,affiliation,paid,priority,guiche,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (pid, name, badge_name, name_key, email, email_key, cpf, affiliation, paid, priority, guiche, now()))
                row = db.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
                record_event(db, row, "import", actor)
                count += 1
        db.commit()
    export_csv()
    return len(prepared), count


def backup_file(path):
    target = Path(path).expanduser().resolve()
    if target == DB.resolve():
        raise ValueError("Escolha um arquivo de backup diferente do banco ativo.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError("O backup já existe. Escolha outro nome para preservar o anterior.")
    with connect() as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    os.chmod(target, 0o600)
    print(f"Backup consistente salvo em {target}")


def main():
    parser = argparse.ArgumentParser(description="Sistema de credenciamento")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    imp = sub.add_parser("import")
    imp.add_argument("file")
    user = sub.add_parser("user")
    user.add_argument("username")
    user.add_argument("role", choices=("volunteer", "attendant", "admin"))
    user.add_argument("--guiche", default="")
    sub.add_parser("sync")
    backup = sub.add_parser("backup")
    backup.add_argument("file")
    args = parser.parse_args()
    init_db()
    if args.command == "import":
        import_file(args.file)
    elif args.command == "user":
        import getpass
        if args.role == "attendant" and not args.guiche:
            parser.error("Atendente precisa de --guiche.")
        password = getpass.getpass("Senha (mínimo 10 caracteres): ")
        if len(password) < 10:
            parser.error("Senha curta demais.")
        salt = secrets.token_hex(16)
        with connect() as db:
            db.execute("INSERT INTO users(username,salt,password_hash,role,guiche) VALUES (?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET salt=excluded.salt,password_hash=excluded.password_hash,role=excluded.role,guiche=excluded.guiche",
                       (args.username.casefold(), salt, password_hash(password, salt), args.role, args.guiche))
        print("Usuário salvo.")
    elif args.command == "sync":
        print(f"{sync_sheets_once()} eventos sincronizados.")
    elif args.command == "backup":
        backup_file(args.file)
    elif args.command == "serve":
        if len(SECRET) < 32:
            parser.error("Defina CHECKIN_SESSION_SECRET com pelo menos 32 caracteres.")
        try:
            event_config()
        except ValueError as exc:
            parser.error(str(exc))
        export_csv()
        threading.Thread(target=sync_worker, daemon=True).start()
        print(f"http://{args.host}:{args.port}")
        ThreadingHTTPServer((args.host, args.port), App).serve_forever()


if __name__ == "__main__":
    main()
