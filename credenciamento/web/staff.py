"""Rotas da equipe: guichês, filas e ações de busca e retirada."""
from urllib.parse import parse_qs, urlparse

from ..csv_io import export_csv
from ..db import connect, get_participant, participant_dict, update_participant
from ..desks import available_guiches, priority_guiche
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
    return req.respond(200, {"items": [participant_dict(r) for r in rows], "guiche": guiche,
                             "priority_guiche": priority_guiche()})


@route("POST", "/api/logout", roles=STAFF)
def logout(req, user, data):
    return req.respond(200, {"ok": True}, {"Set-Cookie": "session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})


@route("POST", "/api/action/", roles=STAFF, prefix=True)
def queue_action(req, user, data):
    action = req.api_path.removeprefix("/api/action/")
    if action not in ("claim", "ready", "release", "complete", "undo_ready", "undo_complete"):
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
    return req.respond(200, {"item": participant_dict(row)})
