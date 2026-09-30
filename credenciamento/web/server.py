"""Servidor HTTP: páginas estáticas, API pública e API da equipe."""
import csv
import hashlib
import hmac
import html
import ipaddress
import json
import re
import secrets
import sqlite3
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import settings
from ..auth import allowed, csrf_token, password_hash, sign, unsign
from ..common import mask_public_text, normalize, now
from ..csv_io import (event_logs_download, export_csv, import_text, participants_download,
                      participants_template_download)
from ..db import connect, get_participant, participant_dict, pending_reasons, update_participant
from ..desks import (available_guiches, configured_ranges, desk_ranges, guiche_for, priority_guiche,
                     save_ranges)
from ..event_theme import event_config, theme_css
from ..participants import STATUS_STEPS, queue_action_fields, status_fields


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

    def require(self, roles):
        user = self.session()
        if not user or user["role"] not in roles:
            self.respond(HTTPStatus.UNAUTHORIZED, {"error": "Entre com uma conta autorizada."})
            return None
        return user

    def check_csrf(self, user):
        return hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), csrf_token(user["username"]))

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
            body = (settings.STATIC / "assets" / asset).read_bytes()
            mime = ("image/png" if asset.endswith(".png") else
                    "image/jpeg" if asset.endswith((".jpg", ".jpeg")) else
                    "image/webp" if asset.endswith(".webp") else
                    "image/svg+xml" if asset.endswith(".svg") else "font/woff2")
        else:
            routes = {"/": "index.html", "/busca": "busca.html", "/fila": "fila.html",
                      "/painel": "painel.html",
                      "/painel/resumo": "resumo.html", "/login": "login.html",
                      "/app.css": "app.css", "/app.js": "app.js"}
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
            return self.respond(200, {"user": user, "csrf": csrf_token(user["username"])})
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
                query = f"SELECT * FROM participants WHERE status IN {statuses} AND paid=1 AND TRIM(affiliation)<>''"
                args = []
                if guiche:
                    query += " AND guiche=?"
                    args.append(guiche)
                if attendant_view:
                    query += (" ORDER BY CASE WHEN status='ready' THEN 0 ELSE 1 END,"
                              " CASE WHEN status='ready' THEN ready_at END ASC,"
                              " CASE WHEN status='completed' THEN completed_at END DESC, name_key ASC")
                else:
                    query += " ORDER BY priority DESC, prechecked_at ASC, name_key ASC"
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
                desks = available_guiches(db)
            for desk in desks:
                desk["total"] = sum(r["guiche"] == desk["id"] for r in rows)
            counts = {s: sum(r["status"] == s for r in rows) for s in
                      ("registered", "prechecked", "searching", "ready", "completed")}
            return self.respond(200, {"total": len(rows), "counts": counts,
                                      "guidance_pending": sum(r["status"] not in ("registered", "completed")
                                                              and bool(pending_reasons(r)) for r in rows),
                                      "sheet_pending": pending, "sheet_configured": bool(settings.SHEET_URL and settings.SHEET_SECRET),
                                      "desks": desks,
                                      # Pagamento ou afiliação faltando em quem ainda não foi credenciado.
                                      "registration_pending": sum(r["status"] != "completed" and bool(pending_reasons(r))
                                                                  for r in rows),
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
                script = settings.GOOGLE_SHEETS_SCRIPT.read_text(encoding="utf-8")
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
                return self.respond(200, {"ranges": configured_ranges(), "priority_guiche": priority_guiche()})
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
            name_key = normalize(str(data.get("name", "")))
            email_key = str(data.get("email", "")).strip().casefold()
            if cpf and not re.fullmatch(r"\d{11}", cpf):
                return self.respond(400, {"error": "Informe um CPF válido com 11 dígitos."})
            if not cpf and (len(name_key) < 3 or "@" not in email_key):
                return self.respond(400, {"error": "Informe o CPF ou o nome completo e o e-mail da inscrição."})
            with connect() as db:
                if cpf:
                    matches = db.execute("SELECT * FROM participants WHERE cpf=? LIMIT 2", (cpf,)).fetchall()
                else:
                    matches = db.execute("SELECT * FROM participants WHERE name_key=? AND email_key=? LIMIT 2",
                                         (name_key, email_key)).fetchall()
            if len(matches) > 1:
                return self.respond(404, {"error": "Inscrição indisponível. Confira os dados ou procure atendimento."})
            row = matches[0] if matches else None
            if not row:
                return self.respond(404, {"error": "Inscrição indisponível. Confira os dados ou procure atendimento."})
            token = secrets.token_urlsafe(32)
            with connect() as db:
                db.execute("DELETE FROM lookup_tokens WHERE expires_at<?", (time.time(),))
                db.execute("INSERT INTO lookup_tokens(token_hash,participant_id,expires_at) VALUES (?,?,?)",
                           (hashlib.sha256(token.encode()).hexdigest(), row["id"], time.time() + 600))
            # The public page never learns why a registration is pending.
            return self.respond(200, {"name": mask_public_text(row["name"]),
                                      "affiliation": mask_public_text(row["affiliation"]), "token": token})
        if path == "/api/precheck":
            token = data.get("token", "")
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                return self.respond(400, {"error": "Consulta expirada. Tente novamente."})
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                token_row = db.execute("SELECT participant_id FROM lookup_tokens WHERE token_hash=? AND expires_at>=?",
                                       (token_hash, time.time())).fetchone()
                if not token_row:
                    return self.respond(400, {"error": "Consulta expirada. Tente novamente."})
                db.execute("DELETE FROM lookup_tokens WHERE token_hash=?", (token_hash,))
                row = get_participant(db, token_row["participant_id"])
                if not row:
                    return self.respond(404, {"error": "Inscrição não encontrada."})
                needs_guidance = bool(pending_reasons(row))
                if row["status"] == "registered":
                    row = update_participant(db, row["id"], "precheck_pending" if needs_guidance else "precheck",
                                             "participant", status="prechecked", prechecked_at=now())
                    db.commit()
                    export_csv()
            result = {"ok": True, "name": mask_public_text(row["name"]), "needs_guidance": needs_guidance}
            if not needs_guidance:
                # Pending registrations are sent to a volunteer instead of a desk.
                result.update(guiche=row["guiche"], guiche_ranges=desk_ranges(row["guiche"]),
                              guiche_priority=row["guiche"] == priority_guiche())
            return self.respond(200, result)
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
            secure = "; Secure" if settings.PUBLIC_URL.startswith("https://") else ""
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
                row = get_participant(db, pid)
                if not row:
                    return self.respond(404, {"error": "Participante não encontrado."})
                if row["paid"] != int(paid):
                    row = update_participant(db, pid, "payment_paid" if int(paid) else "payment_unpaid",
                                             user["username"], paid=int(paid))
                    db.commit()
                    export_csv()
            return self.respond(200, {"item": participant_dict(row, private=True)})
        if path == "/api/participants/priority":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode alterar a prioridade."})
            pid, priority = str(data.get("id", "")), str(data.get("priority", "")).strip()
            if priority not in ("0", "1"):
                return self.respond(400, {"error": "Prioridade inválida."})
            priority = int(priority)
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = get_participant(db, pid)
                if not row:
                    return self.respond(404, {"error": "Participante não encontrado."})
                desk, guiche = priority_guiche(), row["guiche"]
                try:
                    if priority and desk:
                        guiche = desk
                    elif not priority and guiche == desk:
                        guiche = guiche_for(row["name"])
                except ValueError as exc:
                    return self.respond(400, {"error": str(exc)})
                if guiche != row["guiche"] and row["status"] in ("searching", "ready", "completed"):
                    return self.respond(409, {"error": "Não é possível mudar o guichê: a busca ou a retirada do kit já começou."})
                if row["priority"] != priority or row["guiche"] != guiche:
                    manual = 0 if priority and desk else row["guiche_manual"] if guiche == row["guiche"] else 0
                    row = update_participant(db, pid, "priority_on" if priority else "priority_off", user["username"],
                                             priority=priority, guiche=guiche, guiche_manual=manual)
                    db.commit()
                    export_csv()
            return self.respond(200, {"item": participant_dict(row, private=True)})
        if path == "/api/participants/affiliation":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode alterar a afiliação."})
            pid, affiliation = str(data.get("id", "")), " ".join(str(data.get("affiliation", "")).split())
            if not affiliation or len(affiliation) > 200:
                return self.respond(400, {"error": "Informe a afiliação com até 200 caracteres."})
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = get_participant(db, pid)
                if not row:
                    return self.respond(404, {"error": "Participante não encontrado."})
                if row["affiliation"] != affiliation:
                    row = update_participant(db, pid, "affiliation_update", user["username"], affiliation=affiliation)
                    db.commit()
                    export_csv()
            return self.respond(200, {"item": participant_dict(row, private=True)})
        if path == "/api/guiches/config":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode configurar guichês."})
            try:
                ranges, priority, changed = save_ranges(data.get("ranges"), data.get("priority_guiche"), user["username"])
            except (ValueError, OSError) as exc:
                return self.respond(400, {"error": str(exc)})
            if changed:
                export_csv()
            return self.respond(200, {"ranges": ranges, "priority_guiche": priority, "updated": changed})
        if path == "/api/participants/status":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode alterar a situação."})
            pid, status = str(data.get("id", "")), str(data.get("status", ""))
            if status not in STATUS_STEPS:
                return self.respond(400, {"error": "Situação inválida."})
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = get_participant(db, pid)
                if not row:
                    return self.respond(404, {"error": "Participante não encontrado."})
                if status in ("searching", "ready", "completed") and pending_reasons(row):
                    return self.respond(409, {"error": "Há pagamento ou afiliação pendente. Resolva a pendência antes de avançar a situação."})
                row = update_participant(db, pid, "status_" + status, user["username"],
                                         **status_fields(row, status, user["username"]))
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
                row = get_participant(db, pid)
                if not row:
                    return self.respond(404, {"error": "Participante não encontrado."})
                fields = queue_action_fields(action, user, row)
                if fields is None:
                    return self.respond(409, {"error": "Esta ação não é permitida no estado atual. Atualize a fila."})
                row = update_participant(db, pid, action, user["username"], **fields)
                db.commit()
            export_csv()
            return self.respond(200, {"item": participant_dict(row)})
        self.respond(404, {"error": "Rota não encontrada."})
