"""Rotas da coordenação: painel detalhado, importação/exportação, guichês e alterações de participante."""
import csv
import json
import re
import sqlite3
from pathlib import Path

from .. import settings
from ..auth import recent_count
from ..csv_io import (event_logs_download, export_csv, import_text, participants_download,
                      participants_template_download)
from ..db import connect, get_participant, participant_dict, pending_reasons, update_participant
from ..changes import ChangeError, participant_change
from ..desks import (available_guiches, balanced_desk_names, balanced_ranges, configured_ranges, letter_counts,
                     priority_guiche, save_ranges, validate_priority_guiche)
from ..event_theme import remove_logo, reset_theme, save_logo, save_theme, theme_settings
from ..lookup import cpf_key, email_key
from ..participants import (REFRESH_LIMITS, public_checkin_state, refresh_intervals, set_public_checkin,
                            set_refresh_intervals)
from ..payments import ok_statuses, payment_options, save_payment_config
from ..snapshot import (backup_bytes, backup_filename, build_backup, clear_participants, restore_backup,
                        save_server_copy)
from .routes import ADMIN, route


@route("GET", "/api/dashboard", roles=ADMIN)
def dashboard(req, user, data):
    with connect() as db:
        rows = db.execute("SELECT * FROM participants ORDER BY name_key").fetchall()
        pending = db.execute("SELECT COUNT(*) FROM sheet_outbox WHERE delivered_at IS NULL").fetchone()[0]
        desks = available_guiches(db)
        checkin = public_checkin_state(db)
        payment_options_list = payment_options(db)
        refresh = refresh_intervals(db)
    alert_limit, alert_window = settings.rate_limit("alert_miss")
    for desk in desks:
        desk["total"] = sum(r["guiche"] == desk["id"] for r in rows)
    counts = {s: sum(r["status"] == s for r in rows) for s in
              ("registered", "prechecked", "searching", "ready", "completed")}
    return req.respond(200, {"total": len(rows), "counts": counts,
                              "guidance_pending": sum(r["status"] not in ("registered", "completed")
                                                      and bool(pending_reasons(r)) for r in rows),
                              "sheet_pending": pending, "sheet_configured": bool(settings.SHEET_URL and settings.SHEET_SECRET),
                              "desks": desks, "public_checkin": checkin,
                              # Buscas públicas sem resultado, de todas as origens: muitas podem ser alguém testando CPFs.
                              "search_misses": {"count": recent_count("miss-all", "alert_miss"),
                                                "alert": alert_limit, "minutes": alert_window // 60},
                              # Pagamento ou afiliação faltando em quem ainda não foi credenciado.
                              "registration_pending": sum(r["status"] != "completed" and bool(pending_reasons(r))
                                                          for r in rows),
                              "payment_options": payment_options_list,
                              "refresh": refresh, "refresh_seconds": refresh["dashboards"],
                              "items": [participant_dict(r, private=True) for r in rows]})


@route("POST", "/api/public-checkin", roles=ADMIN, forbidden="Somente a coordenação pode abrir ou fechar o pré-check-in.")
def public_checkin(req, user, data):
    if not isinstance(data.get("open"), bool):
        return req.respond(400, {"error": "Informe se o pré-check-in fica aberto."})
    with connect() as db:
        state = set_public_checkin(db, data["open"], user["username"])
    return req.respond(200, {"public_checkin": state})


@route("GET", "/api/theme", roles=ADMIN)
def theme(req, user, data):
    return req.respond(200, theme_settings())


@route("POST", "/api/theme", roles=ADMIN, forbidden="Somente a coordenação pode mudar a identidade visual.")
def update_theme(req, user, data):
    try:
        save_theme(data, user["username"])
    except ValueError as exc:
        return req.respond(400, {"error": str(exc)})
    return req.respond(200, theme_settings())


@route("POST", "/api/theme/logo", roles=ADMIN, max_body=1_500_000,
       forbidden="Somente a coordenação pode mudar a identidade visual.")
def upload_logo(req, user, data):
    try:
        save_logo(data.get("data", ""), user["username"])
    except ValueError as exc:
        return req.respond(400, {"error": str(exc)})
    return req.respond(200, theme_settings())


@route("POST", "/api/theme/logo/remove", roles=ADMIN, forbidden="Somente a coordenação pode mudar a identidade visual.")
def default_logo(req, user, data):
    remove_logo(user["username"])
    return req.respond(200, theme_settings())


@route("POST", "/api/theme/reset", roles=ADMIN, forbidden="Somente a coordenação pode mudar a identidade visual.")
def default_theme(req, user, data):
    reset_theme(user["username"])
    return req.respond(200, theme_settings())


@route("POST", "/api/participants/find", roles=ADMIN)
def find_participants(req, user, data):
    """O painel não recebe CPF nem e-mail: para achar alguém por eles, compara as chaves aqui."""
    query = str(data.get("query", "")).strip()
    digits = re.sub(r"\D", "", query)
    if re.fullmatch(r"[\d.\-\s]+", query) and len(digits) == 11:
        column, value = "cpf_key", cpf_key(digits)
    elif re.fullmatch(r"[^@\s]+@[^@\s]+", query):
        column, value = "email_key", email_key(query)
    else:
        return req.respond(400, {"error": "Informe um CPF com 11 dígitos ou um e-mail."})
    with connect() as db:
        ids = [row["id"] for row in db.execute(f"SELECT id FROM participants WHERE {column}=?", (value,))]
    return req.respond(200, {"ids": ids})


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
        ranges, priority = configured_ranges(), priority_guiche()
    except ValueError as exc:
        return req.respond(500, {"error": str(exc)})
    with connect() as db:
        counts = letter_counts(db, priority)
    return req.respond(200, {"ranges": ranges, "priority_guiche": priority, "letter_counts": counts})


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


def apply_change(req, user, data, field, value):
    """Alteração de uma pessoa pelo painel, com as mesmas regras da alteração em lote."""
    pid = str(data.get("id", ""))
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = get_participant(db, pid)
        if not row:
            return req.respond(404, {"error": "Participante não encontrado."})
        try:
            change = participant_change(db, row, field, value, user["username"])
        except ChangeError as exc:
            status = 400 if "inválid" in str(exc) else 409
            return req.respond(status, {"error": str(exc)})
        if change:
            action, fields = change
            row = update_participant(db, pid, action, user["username"], **fields)
            db.commit()
    if change:
        export_csv()
    return req.respond(200, {"item": participant_dict(row, private=True)})


@route("POST", "/api/participants/payment", roles=ADMIN, forbidden="Somente a coordenação pode alterar o pagamento.")
def set_payment(req, user, data):
    # Aceita a situação em texto ou, como antes, paid 0/1.
    value = data.get("payment_status", data.get("paid", ""))
    if str(value).strip() == "":
        return req.respond(400, {"error": "Informe a situação do pagamento."})
    return apply_change(req, user, data, "payment", value)


@route("POST", "/api/participants/priority", roles=ADMIN, forbidden="Somente a coordenação pode alterar a prioridade.")
def set_priority(req, user, data):
    return apply_change(req, user, data, "priority", str(data.get("priority", "")).strip())


@route("POST", "/api/participants/guiche", roles=ADMIN, forbidden="Somente a coordenação pode mudar o guichê.")
def set_desk(req, user, data):
    return apply_change(req, user, data, "guiche", data.get("guiche", ""))


BATCH_FIELDS = ("payment", "status", "guiche", "priority")
BATCH_LIMIT = 5000


@route("POST", "/api/participants/batch", roles=ADMIN, max_body=400_000,
       forbidden="Somente a coordenação pode alterar participantes em lote.")
def batch_change(req, user, data):
    """Mesma alteração para várias pessoas. Quem não pode mudar (ex.: busca já começou) fica
    de fora com o motivo; as demais mudam juntas."""
    ids, field, value = data.get("ids"), data.get("field"), data.get("value")
    if field not in BATCH_FIELDS:
        return req.respond(400, {"error": "Escolha o que alterar."})
    if not isinstance(ids, list) or not ids or len(ids) > BATCH_LIMIT or not all(isinstance(i, str) for i in ids):
        return req.respond(400, {"error": f"Selecione entre 1 e {BATCH_LIMIT} participantes."})
    if value is None or str(value).strip() == "":
        return req.respond(400, {"error": "Escolha o novo valor."})
    updated, unchanged, skipped = 0, 0, []
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for pid in dict.fromkeys(ids):
            row = get_participant(db, pid)
            if not row:
                skipped.append({"id": pid, "name": "", "reason": "Participante não encontrado."})
                continue
            try:
                change = participant_change(db, row, field, value, user["username"])
            except ChangeError as exc:
                skipped.append({"id": pid, "name": row["name"], "reason": str(exc)})
                continue
            if not change:
                unchanged += 1
                continue
            action, fields = change
            update_participant(db, pid, action, user["username"], **fields)
            updated += 1
        db.commit()
    if updated:
        export_csv()
    return req.respond(200, {"updated": updated, "unchanged": unchanged, "skipped": skipped})


@route("GET", "/api/payment/config", roles=ADMIN)
def payment_config(req, user, data):
    with connect() as db:
        return req.respond(200, {"options": payment_options(db), "ok": ok_statuses(db)})


@route("POST", "/api/payment/config", roles=ADMIN, forbidden="Somente a coordenação pode configurar o pagamento.")
def save_payment(req, user, data):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            changed = save_payment_config(db, data.get("ok"), data.get("options", []), user["username"])
        except ValueError as exc:
            return req.respond(400, {"error": str(exc)})
        db.commit()
        result = {"options": payment_options(db), "ok": ok_statuses(db), "updated": changed}
    if changed:
        export_csv()
    return req.respond(200, result)


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


@route("POST", "/api/guiches/balance", roles=ADMIN, forbidden="Somente a coordenação pode configurar guichês.")
def balance_desks(req, user, data):
    """Só calcula a proposta de faixas; nada muda até a coordenação salvar a configuração."""
    count = str(data.get("count", "")).strip()
    if not count.isdigit() or not 1 <= int(count) <= 26:
        return req.respond(400, {"error": "Informe um número de guichês entre 1 e 26."})
    current = data.get("ranges") if isinstance(data.get("ranges"), list) else None
    try:
        priority = validate_priority_guiche(data.get("priority_guiche", priority_guiche()))
        names = balanced_desk_names(int(count), current or configured_ranges(), priority)
        with connect() as db:
            counts = letter_counts(db, priority)
        ranges = balanced_ranges(counts, names)
    except ValueError as exc:
        return req.respond(400, {"error": str(exc)})
    return req.respond(200, {"ranges": ranges, "priority_guiche": priority})


@route("POST", "/api/participants/status", roles=ADMIN, forbidden="Somente a coordenação pode alterar a situação.")
def set_status(req, user, data):
    return apply_change(req, user, data, "status", str(data.get("status", "")))


@route("GET", "/api/backup/download", roles=ADMIN)
def download_backup(req, user, data):
    """Participantes completos (situação, horários) e todo o histórico, num arquivo restaurável."""
    with connect() as db:
        backup = build_backup(db)
    return req.send_file(backup_filename(), backup_bytes(backup), "application/json; charset=utf-8")


@route("POST", "/api/participants/clear", roles=ADMIN,
       forbidden="Somente a coordenação pode limpar os participantes.")
def clear_all(req, user, data):
    """Apaga participantes e histórico (contas e configurações ficam). Guarda antes uma cópia
    no servidor, além da que o painel baixa."""
    if data.get("confirm") != "LIMPAR":
        return req.respond(400, {"error": "Digite LIMPAR para confirmar."})
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        copy = save_server_copy(build_backup(db), "antes-de-limpar")
        people, events = clear_participants(db)
        db.commit()
    export_csv()
    print(f"Participantes e histórico apagados por {user['username']}: {people} participantes, {events} eventos. "
          f"Cópia em {copy}", flush=True)
    return req.respond(200, {"participants": people, "events": events, "server_copy": copy.name})


@route("POST", "/api/backup/restore", roles=ADMIN, max_body=60_000_000,
       forbidden="Somente a coordenação pode restaurar um backup.")
def restore(req, user, data):
    """Troca participantes e histórico pelos do backup; o estado anterior fica guardado no servidor."""
    if data.get("confirm") != "RESTAURAR":
        return req.respond(400, {"error": "Digite RESTAURAR para confirmar."})
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        copy = save_server_copy(build_backup(db), "antes-de-restaurar")
        try:
            people, events = restore_backup(db, data.get("backup"))
        except (ValueError, sqlite3.IntegrityError) as exc:
            db.execute("ROLLBACK")
            message = str(exc) if isinstance(exc, ValueError) else "Backup com dados repetidos ou inconsistentes."
            return req.respond(400, {"error": message})
        db.commit()
    export_csv()
    print(f"Backup restaurado por {user['username']}: {people} participantes, {events} eventos. "
          f"Estado anterior em {copy}", flush=True)
    return req.respond(200, {"participants": people, "events": events, "server_copy": copy.name})


@route("POST", "/api/refresh-config", roles=ADMIN, forbidden="Somente a coordenação pode ajustar a atualização das telas.")
def save_refresh(req, user, data):
    """Intervalos de atualização automática: filas (separação e guichês) e painéis."""
    with connect() as db:
        try:
            refresh = set_refresh_intervals(db, data, user["username"])
        except ValueError as exc:
            return req.respond(400, {"error": str(exc)})
    return req.respond(200, {"refresh": refresh, "limits": REFRESH_LIMITS})
