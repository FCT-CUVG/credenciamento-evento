"""Rotas públicas: identidade do evento, resumo, pré-check-in e login."""
import re

from .. import settings
from ..auth import SESSION_SECONDS, allowed, check_password, create_session, csrf_token, over_limit, record
from ..common import mask_public_text, now
from ..csv_io import export_csv
from ..db import connect, pending_reasons, update_participant
from ..desks import desk_ranges, priority_guiche
from ..event_theme import event_config
from ..lookup import cpf_key, email_key
from ..participants import public_checkin_state
from .routes import route


@route("GET", "/api/event")
def event_info(req, user, data):
    config = event_config()
    with connect() as db:
        checkin_open = public_checkin_state(db)["open"]
    return req.respond(200, {"name": config["name"], "short_name": config["short_name"],
                              "checkin_open": checkin_open,
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
    return req.respond(200, {"user": user, "csrf": csrf_token(req.session_token)})


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
    with connect() as db:
        if not public_checkin_state(db)["open"]:
            return req.respond(403, {"error": "O pré-check-in ainda não está aberto.", "closed": True})
    # Quem acerta só conta no limite geral; quem erra muito (testando CPFs) é barrado antes.
    ip = req.client_ip()
    if over_limit("miss:" + ip, "search_miss") or not allowed("search:" + ip, "search"):
        return req.respond(429, {"error": "Muitas tentativas. Aguarde alguns minutos."})
    cpf = re.sub(r"\D", "", str(data.get("cpf", "")))
    email = str(data.get("email", "")).strip()
    if cpf and not re.fullmatch(r"\d{11}", cpf):
        return req.respond(400, {"error": "Informe um CPF válido com 11 dígitos."})
    if not cpf and not re.fullmatch(r"[^@\s]+@[^@\s]+", email):
        return req.respond(400, {"error": "Informe o CPF ou o e-mail usado na inscrição."})
    column, value = ("cpf_key", cpf_key(cpf)) if cpf else ("email_key", email_key(email))
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        matches = db.execute(f"SELECT * FROM participants WHERE {column}=? LIMIT 2", (value,)).fetchall()
        if len(matches) != 1:
            record("miss:" + ip, "search_miss")
            record("miss-all", "alert_miss")
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
    # Só senhas erradas contam: por IP e por conta (que vale mesmo com muita gente no mesmo IP).
    username = str(data.get("username", "")).strip().casefold()[:100]
    password = str(data.get("password", ""))
    ip_key, user_key = "login-ip:" + req.client_ip(), "login-user:" + username
    if over_limit(ip_key, "login_ip") or over_limit(user_key, "login_user"):
        return req.respond(429, {"error": "Muitas tentativas. Aguarde alguns minutos."})
    with connect() as db:
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not check_password(user, password):
        record(ip_key, "login_ip")
        record(user_key, "login_user")
        return req.respond(401, {"error": "Usuário ou senha incorretos."})
    with connect() as db:
        token = create_session(db, username)
    secure = "; Secure" if settings.PUBLIC_URL.startswith("https://") else ""
    return req.respond(200, {"role": user["role"]}, {"Set-Cookie": f"session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}{secure}"})
