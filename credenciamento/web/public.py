"""Rotas públicas: identidade do evento, resumo, pré-check-in e login."""
import hmac
import re
import time

from .. import settings
from ..auth import allowed, csrf_token, password_hash, sign
from ..common import mask_public_text, now
from ..csv_io import export_csv
from ..db import connect, pending_reasons, update_participant
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


@route("POST", "/api/checkin")
def checkin(req, user, data):
    """Busca a inscrição pelo CPF ou, sem CPF, pelo e-mail e já registra a chegada."""
    if not allowed(req.client_ip()):
        return req.respond(429, {"error": "Muitas tentativas. Aguarde um minuto."})
    cpf = re.sub(r"\D", "", str(data.get("cpf", "")))
    email_key = str(data.get("email", "")).strip().casefold()
    if cpf and not re.fullmatch(r"\d{11}", cpf):
        return req.respond(400, {"error": "Informe um CPF válido com 11 dígitos."})
    if not cpf and not re.fullmatch(r"[^@\s]+@[^@\s]+", email_key):
        return req.respond(400, {"error": "Informe o CPF ou o e-mail usado na inscrição."})
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        column, value = ("cpf", cpf) if cpf else ("email_key", email_key)
        matches = db.execute(f"SELECT * FROM participants WHERE {column}=? LIMIT 2", (value,)).fetchall()
        if not matches:
            return req.respond(404, {"error": "Inscrição não encontrada."})
        if len(matches) > 1:
            # Um mesmo e-mail pode estar em mais de uma inscrição; não dá para saber qual é a da pessoa.
            return req.respond(409, {"error": "Há mais de uma inscrição com este e-mail."})
        row = matches[0]
        needs_guidance = bool(pending_reasons(row))
        if row["status"] == "registered":
            row = update_participant(db, row["id"], "precheck_pending" if needs_guidance else "precheck",
                                     "participant", status="prechecked", prechecked_at=now())
            db.commit()
            export_csv()
    # The public page never learns why a registration is pending.
    result = {"ok": True, "name": mask_public_text(row["name"]), "needs_guidance": needs_guidance}
    if not needs_guidance:
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
