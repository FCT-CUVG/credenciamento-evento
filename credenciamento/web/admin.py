"""Rotas da coordenação: painel detalhado, importação/exportação, guichês e alterações de participante."""
import csv
import json
import sqlite3
from pathlib import Path

from .. import settings
from ..csv_io import (event_logs_download, export_csv, import_text, participants_download,
                      participants_template_download)
from ..db import connect, get_participant, participant_dict, pending_reasons, update_participant
from ..desks import available_guiches, configured_ranges, guiche_for, priority_guiche, save_ranges
from ..participants import STATUS_STEPS, status_fields
from .routes import ADMIN, route


@route("GET", "/api/dashboard", roles=ADMIN)
def dashboard(req, user, data):
    with connect() as db:
        rows = db.execute("SELECT * FROM participants ORDER BY name_key").fetchall()
        pending = db.execute("SELECT COUNT(*) FROM sheet_outbox WHERE delivered_at IS NULL").fetchone()[0]
        desks = available_guiches(db)
    for desk in desks:
        desk["total"] = sum(r["guiche"] == desk["id"] for r in rows)
    counts = {s: sum(r["status"] == s for r in rows) for s in
              ("registered", "prechecked", "searching", "ready", "completed")}
    return req.respond(200, {"total": len(rows), "counts": counts,
                              "guidance_pending": sum(r["status"] not in ("registered", "completed")
                                                      and bool(pending_reasons(r)) for r in rows),
                              "sheet_pending": pending, "sheet_configured": bool(settings.SHEET_URL and settings.SHEET_SECRET),
                              "desks": desks,
                              # Pagamento ou afiliação faltando em quem ainda não foi credenciado.
                              "registration_pending": sum(r["status"] != "completed" and bool(pending_reasons(r))
                                                          for r in rows),
                              "items": [participant_dict(r, private=True) for r in rows]})


@route("GET", "/api/participants/export", roles=ADMIN)
def export_participants(req, user, data):
    return req.send_csv("dados-participantes.csv", participants_download())


@route("GET", "/api/events/export", roles=ADMIN)
def export_events(req, user, data):
    return req.send_csv("logs-movimentacoes.csv", event_logs_download())


@route("GET", "/api/google-sheets/script", roles=ADMIN)
def google_sheets_script(req, user, data):
    try:
        script = settings.GOOGLE_SHEETS_SCRIPT.read_text(encoding="utf-8")
    except OSError:
        return req.respond(500, {"error": "Não foi possível ler o Apps Script do backup."})
    return req.respond(200, {"script": script})


@route("GET", "/api/participants/template", roles=ADMIN)
def participants_template(req, user, data):
    return req.send_csv("participantes-exemplo.csv", participants_template_download())


@route("GET", "/api/guiches/config", roles=ADMIN)
def desk_config(req, user, data):
    try:
        return req.respond(200, {"ranges": configured_ranges(), "priority_guiche": priority_guiche()})
    except ValueError as exc:
        return req.respond(500, {"error": str(exc)})


@route("POST", "/api/participants/import", roles=ADMIN, max_body=2_000_000,
       forbidden="Somente a coordenação pode importar participantes.")
def import_participants(req, user, data):
    filename, content = data.get("filename"), data.get("content")
    if not isinstance(filename, str) or not isinstance(content, str) or not content.strip():
        return req.respond(400, {"error": "Selecione um arquivo CSV ou JSON válido."})
    suffix = Path(filename).suffix.lower()
    if suffix not in (".csv", ".json"):
        return req.respond(400, {"error": "Use um arquivo CSV ou JSON."})
    try:
        read, changed = import_text(content, suffix, actor=user["username"])
    except (ValueError, json.JSONDecodeError, csv.Error, sqlite3.IntegrityError) as exc:
        return req.respond(400, {"error": str(exc)})
    return req.respond(200, {"read": read, "changed": changed})


@route("POST", "/api/participants/payment", roles=ADMIN, forbidden="Somente a coordenação pode alterar o pagamento.")
def set_payment(req, user, data):
    pid, paid = str(data.get("id", "")), str(data.get("paid", "")).strip()
    if paid not in ("0", "1"):
        return req.respond(400, {"error": "Pagamento inválido."})
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = get_participant(db, pid)
        if not row:
            return req.respond(404, {"error": "Participante não encontrado."})
        if row["paid"] != int(paid):
            row = update_participant(db, pid, "payment_paid" if int(paid) else "payment_unpaid",
                                     user["username"], paid=int(paid))
            db.commit()
            export_csv()
    return req.respond(200, {"item": participant_dict(row, private=True)})


@route("POST", "/api/participants/priority", roles=ADMIN, forbidden="Somente a coordenação pode alterar a prioridade.")
def set_priority(req, user, data):
    pid, priority = str(data.get("id", "")), str(data.get("priority", "")).strip()
    if priority not in ("0", "1"):
        return req.respond(400, {"error": "Prioridade inválida."})
    priority = int(priority)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = get_participant(db, pid)
        if not row:
            return req.respond(404, {"error": "Participante não encontrado."})
        desk, guiche = priority_guiche(), row["guiche"]
        try:
            if priority and desk:
                guiche = desk
            elif not priority and guiche == desk:
                guiche = guiche_for(row["name"])
        except ValueError as exc:
            return req.respond(400, {"error": str(exc)})
        if guiche != row["guiche"] and row["status"] in ("searching", "ready", "completed"):
            return req.respond(409, {"error": "Não é possível mudar o guichê: a busca ou a retirada do kit já começou."})
        if row["priority"] != priority or row["guiche"] != guiche:
            manual = 0 if priority and desk else row["guiche_manual"] if guiche == row["guiche"] else 0
            row = update_participant(db, pid, "priority_on" if priority else "priority_off", user["username"],
                                     priority=priority, guiche=guiche, guiche_manual=manual)
            db.commit()
            export_csv()
    return req.respond(200, {"item": participant_dict(row, private=True)})


@route("POST", "/api/participants/affiliation", roles=ADMIN, forbidden="Somente a coordenação pode alterar a afiliação.")
def set_affiliation(req, user, data):
    pid, affiliation = str(data.get("id", "")), " ".join(str(data.get("affiliation", "")).split())
    if not affiliation or len(affiliation) > 200:
        return req.respond(400, {"error": "Informe a afiliação com até 200 caracteres."})
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = get_participant(db, pid)
        if not row:
            return req.respond(404, {"error": "Participante não encontrado."})
        if row["affiliation"] != affiliation:
            row = update_participant(db, pid, "affiliation_update", user["username"], affiliation=affiliation)
            db.commit()
            export_csv()
    return req.respond(200, {"item": participant_dict(row, private=True)})


@route("POST", "/api/guiches/config", roles=ADMIN, forbidden="Somente a coordenação pode configurar guichês.")
def save_desk_config(req, user, data):
    try:
        ranges, priority, changed = save_ranges(data.get("ranges"), data.get("priority_guiche"), user["username"])
    except (ValueError, OSError) as exc:
        return req.respond(400, {"error": str(exc)})
    if changed:
        export_csv()
    return req.respond(200, {"ranges": ranges, "priority_guiche": priority, "updated": changed})


@route("POST", "/api/participants/status", roles=ADMIN, forbidden="Somente a coordenação pode alterar a situação.")
def set_status(req, user, data):
    pid, status = str(data.get("id", "")), str(data.get("status", ""))
    if status not in STATUS_STEPS:
        return req.respond(400, {"error": "Situação inválida."})
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = get_participant(db, pid)
        if not row:
            return req.respond(404, {"error": "Participante não encontrado."})
        if status in ("searching", "ready", "completed") and pending_reasons(row):
            return req.respond(409, {"error": "Há pagamento ou afiliação pendente. Resolva a pendência antes de avançar a situação."})
        row = update_participant(db, pid, "status_" + status, user["username"],
                                 **status_fields(row, status, user["username"]))
        db.commit()
    export_csv()
    return req.respond(200, {"item": participant_dict(row)})
