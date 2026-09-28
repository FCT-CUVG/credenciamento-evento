#!/usr/bin/env python3
"""BRACIS check-in: dependency-free HTTP server backed by SQLite."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import ipaddress
import json
import os
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
DATA = Path(os.environ.get("BRACIS_DATA_DIR", ROOT / "data"))
DB = DATA / "credenciamento.sqlite3"
CSV = DATA / "participantes.csv"
STATIC = ROOT / "static"
RANGES = Path(os.environ.get("BRACIS_RANGES_FILE", ROOT / "config" / "guiches.json"))
SECRET = os.environ.get("BRACIS_SESSION_SECRET", "").encode()
SHEET_URL = os.environ.get("BRACIS_SHEETS_URL", "")
SHEET_SECRET = os.environ.get("BRACIS_SHEETS_SECRET", "")
PUBLIC_URL = os.environ.get("BRACIS_PUBLIC_URL", "")
TRUST_PROXY = os.environ.get("BRACIS_TRUSTED_PROXY", "") == "1"
RATE = defaultdict(deque)
RATE_LOCK = threading.Lock()
CSV_LOCK = threading.Lock()


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
          email TEXT NOT NULL, email_key TEXT NOT NULL, cpf TEXT NOT NULL DEFAULT '',
          affiliation TEXT NOT NULL DEFAULT '', guiche TEXT NOT NULL,
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


def participant_dict(row, private=False):
    d = {k: row[k] for k in ("id", "name", "affiliation", "guiche", "status",
                                  "claimed_by", "prechecked_at", "claimed_at", "ready_at",
                                  "completed_at", "updated_at", "revision")}
    if private:
        d.update(email=row["email"], cpf=row["cpf"])
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
        fields = ["id", "name", "email", "cpf", "affiliation", "guiche", "status",
                  "claimed_by", "prechecked_at", "claimed_at", "ready_at", "completed_at",
                  "updated_at", "revision"]
        temp = CSV.with_suffix(".csv.tmp")
        with temp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                values = {key: row[key] for key in fields}
                for key in ("name", "email", "cpf", "affiliation", "guiche", "claimed_by"):
                    value = values[key]
                    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                        values[key] = "'" + value
                writer.writerow(values)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, CSV)


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
    server_version = "BRACIS/1"

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

    def body(self):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise ValueError("Envie dados em JSON.")
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 16384:
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
        routes = {"/": "index.html", "/busca": "busca.html", "/fila": "fila.html",
                  "/painel": "painel.html", "/login": "login.html",
                  "/app.css": "app.css", "/app.js": "app.js"}
        filename = routes.get(path)
        if not filename:
            return self.send_error(404)
        mime = "text/css" if filename.endswith(".css") else "text/javascript" if filename.endswith(".js") else "text/html"
        body = (STATIC / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def api_get(self, path):
        if path == "/api/me":
            user = self.session()
            if not user:
                return self.respond(200, {"user": None})
            csrf = hmac.new(SECRET, (user["username"] + ":csrf").encode(), hashlib.sha256).hexdigest()
            return self.respond(200, {"user": user, "csrf": csrf})
        if path == "/api/queue":
            user = self.require(("volunteer", "attendant", "admin"))
            if not user:
                return
            guiche = parse_qs(urlparse(self.path).query).get("guiche", [""])[0].strip()
            if user["role"] == "attendant":
                guiche = user["guiche"]
            with connect() as db:
                query = "SELECT * FROM participants WHERE status IN ('prechecked','searching','ready')"
                args = []
                if guiche:
                    query += " AND guiche=?"
                    args.append(guiche)
                query += " ORDER BY prechecked_at ASC, name_key ASC"
                rows = db.execute(query, args).fetchall()
            return self.respond(200, {"items": [participant_dict(r) for r in rows], "guiche": guiche})
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
        self.respond(404, {"error": "Rota não encontrada."})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self.body()
        except (ValueError, json.JSONDecodeError) as exc:
            return self.respond(400, {"error": str(exc)})
        if path == "/api/lookup":
            if not allowed(self.client_ip()):
                return self.respond(429, {"error": "Muitas tentativas. Aguarde um minuto."})
            name, email = normalize(data.get("name")), str(data.get("email", "")).strip().casefold()
            if len(name) < 3 or "@" not in email or len(email) > 254:
                return self.respond(400, {"error": "Informe nome completo e e-mail válido."})
            with connect() as db:
                row = db.execute("SELECT * FROM participants WHERE name_key=? AND email_key=?",
                                 (name, email)).fetchone()
            if not row:
                return self.respond(404, {"error": "Inscrição não encontrada. Confira os dados ou procure a equipe."})
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
        if path.startswith("/api/action/"):
            action = path.removeprefix("/api/action/")
            if action not in ("claim", "ready", "release", "complete"):
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
                elif action == "complete" and user["role"] in ("attendant", "admin") and row["status"] == "ready" and (user["role"] == "admin" or user["guiche"] == row["guiche"]):
                    db.execute("UPDATE participants SET status='completed',completed_at=?,updated_at=?,revision=revision+1 WHERE id=?", (t, t, pid))
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
    if path.suffix.lower() == ".json":
        records = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(records, dict):
            records = records.get("participants", [])
    else:
        with path.open(newline="", encoding="utf-8-sig") as f:
            records = list(csv.DictReader(f))
    if not isinstance(records, list):
        raise ValueError("Arquivo deve conter uma lista de participantes.")
    prepared = []
    seen = set()
    for n, item in enumerate(records, 1):
        name = str(item.get("name", "")).strip()
        email = str(item.get("email", "")).strip()
        guiche = str(item.get("guiche", "")).strip() or guiche_for(name)
        key = (normalize(name), email.casefold())
        if len(key[0]) < 3 or "@" not in email or not guiche:
            raise ValueError(f"Linha {n}: name e email são obrigatórios; guiche deve ser informado ou calculável pelo JSON.")
        if key in seen:
            raise ValueError(f"Linha {n}: nome e e-mail duplicados.")
        seen.add(key)
        prepared.append((name, key[0], email, key[1], str(item.get("cpf", "")).strip(),
                         str(item.get("affiliation", "")).strip(), guiche))
    count = 0
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for name, name_key, email, email_key, cpf, affiliation, guiche in prepared:
            old = db.execute("SELECT * FROM participants WHERE name_key=? AND email_key=?",
                             (name_key, email_key)).fetchone()
            if old:
                if any(old[k] != v for k, v in (("name", name), ("email", email), ("cpf", cpf),
                                                  ("affiliation", affiliation), ("guiche", guiche))):
                    if old["guiche"] != guiche and old["status"] in ("searching", "ready", "completed"):
                        raise ValueError(f"Não é possível mudar o guichê de {name}: busca ou retirada já iniciada.")
                    db.execute("UPDATE participants SET name=?,email=?,cpf=?,affiliation=?,guiche=?,updated_at=?,revision=revision+1 WHERE id=?",
                               (name, email, cpf, affiliation, guiche, now(), old["id"]))
                    row = db.execute("SELECT * FROM participants WHERE id=?", (old["id"],)).fetchone()
                    record_event(db, row, "import_update", "system")
                    count += 1
            else:
                pid = str(uuid.uuid4())
                db.execute("INSERT INTO participants(id,name,name_key,email,email_key,cpf,affiliation,guiche,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           (pid, name, name_key, email, email_key, cpf, affiliation, guiche, now()))
                row = db.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
                record_event(db, row, "import", "system")
                count += 1
        db.commit()
    export_csv()
    print(f"{len(prepared)} registros lidos; {count} criados ou atualizados.")


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
    parser = argparse.ArgumentParser(description="Credenciamento BRACIS")
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
            parser.error("Defina BRACIS_SESSION_SECRET com pelo menos 32 caracteres.")
        export_csv()
        threading.Thread(target=sync_worker, daemon=True).start()
        print(f"http://{args.host}:{args.port}")
        ThreadingHTTPServer((args.host, args.port), App).serve_forever()


if __name__ == "__main__":
    main()
