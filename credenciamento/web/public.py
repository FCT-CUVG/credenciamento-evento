"""Rotas públicas: identidade do evento, resumo, pré-check-in e login."""
import hashlib
import hmac
import re
import secrets
import time

from .. import settings
from ..auth import allowed, csrf_token, password_hash, sign
from ..common import mask_public_text, normalize, now
from ..csv_io import export_csv
from ..db import connect, get_participant, pending_reasons, update_participant
from ..desks import desk_ranges, priority_guiche
from ..event_theme import event_config
from .routes import route


@route("GET", "/api/event")
def event_info(req, user, data):
    config = event_config()
    return req.respond(200, {"name": config["name"], "short_name": config["short_name"],
                              "logo": "/assets/" + config["logo"],
                              "registration_hints": {
                                  "en": config.get("registration_hint_en"),
                                  "pt-BR": config.get("registration_hint_pt")
                              }})


@route("GET", "/api/me")
def me(req, user, data):
    user = req.session()
    if not user:
        return req.respond(200, {"user": None})
    return req.respond(200, {"user": user, "csrf": csrf_token(user["username"])})


@route("GET", "/api/dashboard/summary")
def summary(req, user, data):
    with connect() as db:
        total, arrived, completed = db.execute("""
            SELECT COUNT(*),
                   COALESCE(SUM(status <> 'registered'), 0),
                   COALESCE(SUM(status = 'completed'), 0)
            FROM participants
        """).fetchone()
    return req.respond(200, {"total": total, "arrived": arrived, "completed": completed})


@route("POST", "/api/lookup")
def lookup(req, user, data):
    if not allowed(req.client_ip()):
        return req.respond(429, {"error": "Muitas tentativas. Aguarde um minuto."})
    cpf = re.sub(r"\D", "", str(data.get("cpf", "")))
    name_key = normalize(str(data.get("name", "")))
    email_key = str(data.get("email", "")).strip().casefold()
    if cpf and not re.fullmatch(r"\d{11}", cpf):
        return req.respond(400, {"error": "Informe um CPF válido com 11 dígitos."})
    if not cpf and (len(name_key) < 3 or "@" not in email_key):
        return req.respond(400, {"error": "Informe o CPF ou o nome completo e o e-mail da inscrição."})
    with connect() as db:
        if cpf:
            matches = db.execute("SELECT * FROM participants WHERE cpf=? LIMIT 2", (cpf,)).fetchall()
        else:
            matches = db.execute("SELECT * FROM participants WHERE name_key=? AND email_key=? LIMIT 2",
                                 (name_key, email_key)).fetchall()
    if len(matches) > 1:
        return req.respond(404, {"error": "Inscrição indisponível. Confira os dados ou procure atendimento."})
    row = matches[0] if matches else None
    if not row:
        return req.respond(404, {"error": "Inscrição indisponível. Confira os dados ou procure atendimento."})
    token = secrets.token_urlsafe(32)
    with connect() as db:
        db.execute("DELETE FROM lookup_tokens WHERE expires_at<?", (time.time(),))
        db.execute("INSERT INTO lookup_tokens(token_hash,participant_id,expires_at) VALUES (?,?,?)",
                   (hashlib.sha256(token.encode()).hexdigest(), row["id"], time.time() + 600))
    # The public page never learns why a registration is pending.
    return req.respond(200, {"name": mask_public_text(row["name"]),
                              "affiliation": mask_public_text(row["affiliation"]), "token": token})


@route("POST", "/api/precheck")
def precheck(req, user, data):
    token = data.get("token", "")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return req.respond(400, {"error": "Consulta expirada. Tente novamente."})
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        token_row = db.execute("SELECT participant_id FROM lookup_tokens WHERE token_hash=? AND expires_at>=?",
                               (token_hash, time.time())).fetchone()
        if not token_row:
            return req.respond(400, {"error": "Consulta expirada. Tente novamente."})
        db.execute("DELETE FROM lookup_tokens WHERE token_hash=?", (token_hash,))
        row = get_participant(db, token_row["participant_id"])
        if not row:
            return req.respond(404, {"error": "Inscrição não encontrada."})
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
    return req.respond(200, result)


@route("POST", "/api/login")
def login(req, user, data):
    if not allowed("login:" + req.client_ip(), 8, 300):
        return req.respond(429, {"error": "Muitas tentativas. Aguarde alguns minutos."})
    username = str(data.get("username", "")).strip().casefold()
    password = str(data.get("password", ""))
    with connect() as db:
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user or not hmac.compare_digest(password_hash(password, user["salt"]), user["password_hash"]):
        return req.respond(401, {"error": "Usuário ou senha incorretos."})
    token = sign({"kind": "session", "user": username, "exp": time.time() + 12*3600})
    secure = "; Secure" if settings.PUBLIC_URL.startswith("https://") else ""
    return req.respond(200, {"role": user["role"]}, {"Set-Cookie": f"session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200{secure}"})
