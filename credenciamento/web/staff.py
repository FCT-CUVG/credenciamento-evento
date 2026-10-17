"""Rotas da equipe: guichês, filas, busca de participantes e ações de busca e retirada."""
import re
from urllib.parse import parse_qs, urlparse

from ..auth import end_session
from ..common import normalize
from ..csv_io import export_csv
from ..db import connect, get_participant, participant_dict, pending_reasons, update_participant
from ..desks import available_guiches, priority_guiche
from ..lookup import cpf_key, email_key
from ..participants import queue_action_fields
from .routes import STAFF, route


@route("GET", "/api/guiches", roles=STAFF)
def desks(req, user, data):
    with connect() as db:
        desks = available_guiches(db)
    return req.respond(200, {"guiches": desks})


@route("GET", "/api/queue", roles=STAFF)
def queue(req, user, data):
    params = parse_qs(urlparse(req.path).query)
    requested = params.get("guiche", [""])[0].strip()
    attendant_view = params.get("view", [""])[0] == "attendant"
    guiche = "" if requested == "all" else requested
    # "Guichês gerais": todos os guichês menos o de prioridade.
    without_priority = params.get("exclude", [""])[0] == "priority" and not guiche
    # Sem guichê pedido, a tela do guichê abre no do atendente; a separação mostra todos.
    if user["role"] == "attendant" and not requested and attendant_view:
        guiche = user["guiche"]
    with connect() as db:
        statuses = "('ready','completed')" if attendant_view else "('prechecked','searching','ready')"
        query = f"SELECT * FROM participants WHERE status IN {statuses} AND paid=1 AND TRIM(affiliation)<>''"
        args = []
        if guiche:
            query += " AND guiche=?"
            args.append(guiche)
        elif without_priority and priority_guiche():
            query += " AND guiche<>?"
            args.append(priority_guiche())
        if attendant_view:
            query += (" ORDER BY CASE WHEN status='ready' THEN 0 ELSE 1 END,"
                      " CASE WHEN status='ready' THEN ready_at END ASC,"
                      " CASE WHEN status='completed' THEN completed_at END DESC, name_key ASC")
        else:
            query += " ORDER BY priority DESC, prechecked_at ASC, name_key ASC"
        rows = db.execute(query, args).fetchall()
    return req.respond(200, {"items": [participant_dict(r) for r in rows], "guiche": guiche,
                             "priority_guiche": priority_guiche()})


SEARCH_LIMIT = 30


def participant_matches(db, query):
    """Participantes para a busca do guichê: CPF completo ou e-mail exatos (só as chaves ficam
    guardadas); até 3 dígitos comparam com o início do CPF; texto procura cada palavra no nome
    ou na afiliação, sem acentos."""
    digits = re.sub(r"\D", "", query)
    if re.fullmatch(r"[\d.\-\s]+", query) and len(digits) == 11:
        return db.execute("SELECT * FROM participants WHERE cpf_key=?", (cpf_key(digits),)).fetchall()
    if re.fullmatch(r"[^@\s]+@[^@\s]+", query):
        return db.execute("SELECT * FROM participants WHERE email_key=?", (email_key(query),)).fetchall()
    if query.isdigit():
        return db.execute("SELECT * FROM participants WHERE cpf_prefix LIKE ? ORDER BY name_key",
                          (query[:3] + "%",)).fetchall() if len(query) <= 3 else []
    words = normalize(query).split()
    rows = db.execute("SELECT * FROM participants ORDER BY name_key").fetchall()
    return [row for row in rows
            if all(word in row["name_key"] or word in normalize(row["affiliation"]) for word in words)]


@route("GET", "/api/participants/search", roles=STAFF)
def search_participants(req, user, data):
    """Busca de qualquer participante a partir do guichê, para pedir a separação do crachá.
    Mostra se há pendência, sem dizer qual: quem orienta é a coordenação."""
    query = " ".join(parse_qs(urlparse(req.path).query).get("q", [""])[0].split())[:100]
    if len(query) < 2 and not query.isdigit():
        return req.respond(200, {"items": [], "total": 0})
    with connect() as db:
        rows = participant_matches(db, query)
    items = [dict(participant_dict(row), pending=bool(pending_reasons(row))) for row in rows[:SEARCH_LIMIT]]
    return req.respond(200, {"items": items, "total": len(rows)})


@route("POST", "/api/logout", roles=STAFF)
def logout(req, user, data):
    with connect() as db:
        end_session(db, req.session_token)
    return req.respond(200, {"ok": True}, {"Set-Cookie": "session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})


@route("POST", "/api/action/", roles=STAFF, prefix=True)
def queue_action(req, user, data):
    action = req.api_path.removeprefix("/api/action/")
    if action not in ("request", "undo_request", "claim", "ready", "release", "complete", "undo_ready", "undo_complete"):
        return req.respond(404, {"error": "Ação desconhecida."})
    pid = str(data.get("id", ""))
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = get_participant(db, pid)
        if not row:
            return req.respond(404, {"error": "Participante não encontrado."})
        fields = queue_action_fields(action, user, row)
        if fields is None:
            return req.respond(409, {"error": "Esta ação não é permitida no estado atual. Atualize a fila."})
        row = update_participant(db, pid, action, user["username"], **fields)
        db.commit()
    export_csv()
    return req.respond(200, {"item": dict(participant_dict(row), pending=bool(pending_reasons(row)))})
